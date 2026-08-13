from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .transcript_quality import is_pathological_repetition
from .types import CapturedSegment, FinalResult, SegmentState

TAIPEI = ZoneInfo("Asia/Taipei")
_HOUR_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
_DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SCHEMA_VERSION = 2


class SegmentNotFoundError(KeyError):
    pass


class StateTransitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    segment_id: str
    audio_path: Path
    started_at_utc: datetime
    ended_at_utc: datetime
    hour_key: str
    state: SegmentState
    provisional_raw: str
    provisional_text: str
    final_raw: str
    final_text: str
    corrected_text: str
    user_locked: bool
    engine_profile: str
    retry_count: int
    last_error: str | None
    sample_rate: int
    duration_ms: int
    leading_overlap_ms: int
    previous_segment_id: str | None
    recovered_orphan: bool
    created_at_utc: datetime
    updated_at_utc: datetime

    @property
    def display_text(self) -> str:
        if self.user_locked:
            return self.corrected_text
        return self.final_text or self.provisional_text

    @property
    def display_state(self) -> str:
        if self.user_locked:
            return "corrected"
        if self.final_text:
            return "final"
        return "provisional"


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    reset_finalizing: tuple[str, ...]
    registered_orphans: tuple[str, ...]
    missing_audio: tuple[str, ...]
    pending_audio_deletion: tuple[str, ...]
    completed_audio_deletion: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeletedHour:
    hour_key: str
    segment_ids: tuple[str, ...]
    audio_paths: tuple[Path, ...]


_ALLOWED_TRANSITIONS: dict[SegmentState, frozenset[SegmentState]] = {
    SegmentState.CAPTURED: frozenset(
        {SegmentState.FINALIZING, SegmentState.RETRY, SegmentState.FAILED}
    ),
    SegmentState.FINALIZING: frozenset(
        {
            SegmentState.CAPTURED,
            SegmentState.FINAL_READY,
            SegmentState.RETRY,
            SegmentState.FAILED,
        }
    ),
    SegmentState.RETRY: frozenset(
        {SegmentState.CAPTURED, SegmentState.FINALIZING, SegmentState.FAILED}
    ),
    SegmentState.FAILED: frozenset({SegmentState.RETRY, SegmentState.FINALIZING}),
    SegmentState.FINAL_READY: frozenset({SegmentState.EXPORTED}),
    SegmentState.EXPORTED: frozenset({SegmentState.AUDIO_DELETED}),
    SegmentState.AUDIO_DELETED: frozenset(),
}


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"stored timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def validate_hour_key(value: str) -> str:
    if not _HOUR_KEY_RE.fullmatch(value):
        raise ValueError(f"invalid hour key: {value!r}")
    datetime.strptime(value, "%Y-%m-%d_%H")
    return value


def validate_day_key(value: str) -> str:
    if not _DAY_KEY_RE.fullmatch(value):
        raise ValueError(f"invalid day key: {value!r}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def hour_key(value: datetime, timezone_name: str = "Asia/Taipei") -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d_%H")


def _deduplicate_overlap(previous: str, current: str, overlap_ms: int) -> str:
    """Remove only an exact, bounded suffix/prefix copied by audio overlap."""

    if overlap_ms <= 0 or not previous or not current:
        return current
    max_chars = min(len(previous), len(current), max(4, min(64, overlap_ms * 32 // 1000)))
    previous_folded = previous.casefold()
    current_folded = current.casefold()
    for length in range(max_chars, 1, -1):
        if previous_folded[-length:] != current_folded[:length]:
            continue
        remainder = current[length:].lstrip()
        return remainder if remainder else current
    return current


class JournalStorage:
    """Crash-tolerant SQLite storage for captured speech segments.

    The audio writer must finish and atomically rename the FLAC file before it
    calls :meth:`add_captured`. SQLite is the source of truth after that point.
    """

    def __init__(self, database_path: Path, timezone_name: str = "Asia/Taipei") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        self._lock = threading.RLock()
        self._savepoint_counter = 0
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._configure()
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA secure_delete = ON")
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(f"could not enable SQLite WAL mode: {mode}")

    def _migrate(self) -> None:
        with self._lock:
            tables = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            existing_version: int | None = None
            if "app_meta" in tables:
                row = self._connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    raise RuntimeError("database schema version is missing")
                try:
                    existing_version = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"invalid database schema version: {row[0]!r}") from exc
                if existing_version not in {1, SCHEMA_VERSION}:
                    raise RuntimeError(
                        f"unsupported database schema version {existing_version}; "
                        f"this application supports {SCHEMA_VERSION}"
                    )
            elif tables:
                raise RuntimeError(
                    "refusing to open an unversioned database with existing tables"
                )

        schema = """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS segments (
            segment_id TEXT PRIMARY KEY,
            audio_path TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            ended_at_utc TEXT NOT NULL,
            hour_key TEXT NOT NULL,
            state TEXT NOT NULL,
            provisional_raw TEXT NOT NULL DEFAULT '',
            provisional_text TEXT NOT NULL DEFAULT '',
            final_raw TEXT NOT NULL DEFAULT '',
            final_text TEXT NOT NULL DEFAULT '',
            corrected_text TEXT NOT NULL DEFAULT '',
            user_locked INTEGER NOT NULL DEFAULT 0 CHECK (user_locked IN (0, 1)),
            engine_profile TEXT NOT NULL DEFAULT '',
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            last_error TEXT,
            sample_rate INTEGER NOT NULL DEFAULT 16000,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            leading_overlap_ms INTEGER NOT NULL DEFAULT 0
                CHECK (leading_overlap_ms >= 0),
            previous_segment_id TEXT,
            recovered_orphan INTEGER NOT NULL DEFAULT 0 CHECK (recovered_orphan IN (0, 1)),
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_segments_state_started
            ON segments(state, started_at_utc);
        CREATE INDEX IF NOT EXISTS idx_segments_hour_started
            ON segments(hour_key, started_at_utc, segment_id);

        CREATE TABLE IF NOT EXISTS audio_deletion_queue (
            segment_id TEXT PRIMARY KEY,
            audio_path TEXT NOT NULL,
            hour_key TEXT NOT NULL,
            reason TEXT NOT NULL,
            queued_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vocabulary_terms (
            term TEXT PRIMARY KEY,
            use_count INTEGER NOT NULL CHECK (use_count > 0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vocabulary_contributions (
            segment_id TEXT NOT NULL REFERENCES segments(segment_id) ON DELETE CASCADE,
            source_text TEXT NOT NULL,
            term TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 1 CHECK (amount > 0),
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY (segment_id, source_text, term)
        );
        CREATE INDEX IF NOT EXISTS idx_vocabulary_contributions_term
            ON vocabulary_contributions(term);

        CREATE TRIGGER IF NOT EXISTS vocabulary_contribution_insert
        AFTER INSERT ON vocabulary_contributions
        BEGIN
            INSERT INTO vocabulary_terms(term, use_count, updated_at_utc)
            VALUES (NEW.term, NEW.amount, NEW.created_at_utc)
            ON CONFLICT(term) DO UPDATE SET
                use_count = vocabulary_terms.use_count + NEW.amount,
                updated_at_utc = NEW.created_at_utc;
        END;

        DROP TRIGGER IF EXISTS vocabulary_contribution_delete;
        CREATE TRIGGER vocabulary_contribution_delete
        AFTER DELETE ON vocabulary_contributions
        BEGIN
            DELETE FROM vocabulary_terms
            WHERE term = OLD.term AND use_count <= OLD.amount;
            UPDATE vocabulary_terms
            SET use_count = use_count - OLD.amount,
                updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
            WHERE term = OLD.term;
        END;

        CREATE TABLE IF NOT EXISTS dirty_hours (
            hour_key TEXT PRIMARY KEY,
            changed_at_utc TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS segment_insert_dirty_hour
        AFTER INSERT ON segments
        BEGIN
            INSERT INTO dirty_hours(hour_key, changed_at_utc)
            VALUES (NEW.hour_key, NEW.updated_at_utc)
            ON CONFLICT(hour_key) DO UPDATE SET changed_at_utc = NEW.updated_at_utc;
        END;

        CREATE TRIGGER IF NOT EXISTS segment_text_update_dirty_hour
        AFTER UPDATE OF provisional_text, final_text, corrected_text, user_locked,
                        started_at_utc, hour_key ON segments
        BEGIN
            INSERT INTO dirty_hours(hour_key, changed_at_utc)
            VALUES (OLD.hour_key, NEW.updated_at_utc)
            ON CONFLICT(hour_key) DO UPDATE SET changed_at_utc = NEW.updated_at_utc;
            INSERT INTO dirty_hours(hour_key, changed_at_utc)
            VALUES (NEW.hour_key, NEW.updated_at_utc)
            ON CONFLICT(hour_key) DO UPDATE SET changed_at_utc = NEW.updated_at_utc;
        END;

        CREATE TRIGGER IF NOT EXISTS segment_delete_dirty_hour
        AFTER DELETE ON segments
        BEGIN
            INSERT INTO dirty_hours(hour_key, changed_at_utc)
            VALUES (OLD.hour_key, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
            ON CONFLICT(hour_key) DO UPDATE SET changed_at_utc = excluded.changed_at_utc;
        END;
        """
        with self._lock:
            if existing_version is None:
                # Keep first-time schema creation and its version marker in one
                # transaction. A hard kill can then leave an empty database,
                # never a populated but permanently "unversioned" one.
                try:
                    self._connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + schema
                        + "\nINSERT INTO app_meta(key, value) "
                        + f"VALUES ('schema_version', '{SCHEMA_VERSION}');\n"
                        + "COMMIT;"
                    )
                except BaseException:
                    self._connection.rollback()
                    raise
            else:
                self._connection.executescript(schema)

            if existing_version == 1:
                columns = {
                    str(row[1])
                    for row in self._connection.execute(
                        "PRAGMA table_info(segments)"
                    ).fetchall()
                }
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    if "leading_overlap_ms" not in columns:
                        self._connection.execute(
                            "ALTER TABLE segments ADD COLUMN leading_overlap_ms "
                            "INTEGER NOT NULL DEFAULT 0 CHECK (leading_overlap_ms >= 0)"
                        )
                    if "previous_segment_id" not in columns:
                        self._connection.execute(
                            "ALTER TABLE segments ADD COLUMN previous_segment_id TEXT"
                        )
                    self._connection.execute(
                        "UPDATE app_meta SET value = ? WHERE key = 'schema_version'",
                        (str(SCHEMA_VERSION),),
                    )
                except BaseException:
                    self._connection.rollback()
                    raise
                else:
                    self._connection.commit()

            required_columns = {"leading_overlap_ms", "previous_segment_id"}
            actual_columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(segments)").fetchall()
            }
            missing_columns = required_columns - actual_columns
            if missing_columns:
                raise RuntimeError(
                    "database schema is missing columns: "
                    + ", ".join(sorted(missing_columns))
                )

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Open a transaction, using a savepoint when called recursively."""

        with self._lock:
            if self._connection.in_transaction:
                self._savepoint_counter += 1
                name = f"asj_sp_{self._savepoint_counter}"
                self._connection.execute(f"SAVEPOINT {name}")
                try:
                    yield self._connection
                except BaseException:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
                    self._connection.execute(f"RELEASE SAVEPOINT {name}")
                    raise
                else:
                    self._connection.execute(f"RELEASE SAVEPOINT {name}")
                return

            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> JournalStorage:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def pragmas(self) -> dict[str, object]:
        with self._lock:
            return {
                "journal_mode": self._connection.execute("PRAGMA journal_mode").fetchone()[0],
                "secure_delete": self._connection.execute("PRAGMA secure_delete").fetchone()[0],
                "foreign_keys": self._connection.execute("PRAGMA foreign_keys").fetchone()[0],
            }

    def add_captured(self, segment: CapturedSegment) -> SegmentRecord:
        start = _utc_iso(segment.started_at_utc)
        end = _utc_iso(segment.ended_at_utc)
        now = _utc_iso(datetime.now(UTC))
        key = hour_key(segment.started_at_utc, self.timezone_name)
        path = str(Path(segment.audio_path).resolve())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO segments(
                    segment_id, audio_path, started_at_utc, ended_at_utc,
                    hour_key, state, provisional_raw, provisional_text,
                    sample_rate, duration_ms, leading_overlap_ms,
                    previous_segment_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_id) DO NOTHING
                """,
                (
                    segment.segment_id,
                    path,
                    start,
                    end,
                    key,
                    SegmentState.CAPTURED.value,
                    segment.preview_raw_text or segment.preview_text,
                    segment.preview_text,
                    segment.sample_rate,
                    segment.duration_ms,
                    segment.leading_overlap_ms,
                    segment.previous_segment_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM segments WHERE segment_id = ?", (segment.segment_id,)
            ).fetchone()
        assert row is not None
        record = self._row_to_record(row)
        if (
            record.audio_path != Path(path)
            or record.started_at_utc != segment.started_at_utc.astimezone(UTC)
            or record.ended_at_utc != segment.ended_at_utc.astimezone(UTC)
            or record.sample_rate != segment.sample_rate
            or record.duration_ms != segment.duration_ms
            or record.leading_overlap_ms != segment.leading_overlap_ms
            or record.previous_segment_id != segment.previous_segment_id
        ):
            raise ValueError(f"segment id collision: {segment.segment_id}")
        return record

    # Controller-friendly alias.
    append_segment = add_captured

    def get_segment(self, segment_id: str) -> SegmentRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM segments WHERE segment_id = ?", (segment_id,)
            ).fetchone()
        if row is None:
            raise SegmentNotFoundError(segment_id)
        return self._row_to_record(row)

    def claim_next_for_finalization(self) -> SegmentRecord | None:
        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT segment_id FROM segments
                WHERE state IN (?, ?)
                ORDER BY started_at_utc, segment_id
                LIMIT 1
                """,
                (SegmentState.CAPTURED.value, SegmentState.RETRY.value),
            ).fetchone()
            if row is None:
                return None
            segment_id = str(row["segment_id"])
            connection.execute(
                """
                UPDATE segments SET state = ?, last_error = NULL, updated_at_utc = ?
                WHERE segment_id = ?
                """,
                (SegmentState.FINALIZING.value, now, segment_id),
            )
            claimed = connection.execute(
                "SELECT * FROM segments WHERE segment_id = ?", (segment_id,)
            ).fetchone()
        assert claimed is not None
        return self._row_to_record(claimed)

    def mark_finalizing(self, segment_id: str) -> SegmentRecord:
        """Record an accepted finalizer submission without regressing completed work."""

        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM segments WHERE segment_id = ?", (segment_id,)
            ).fetchone()
            if row is None:
                raise SegmentNotFoundError(segment_id)
            current = SegmentState(row["state"])
            if current in {
                SegmentState.FINALIZING,
                SegmentState.FINAL_READY,
                SegmentState.EXPORTED,
                SegmentState.AUDIO_DELETED,
            }:
                return self._row_to_record(row)
            if current not in {SegmentState.CAPTURED, SegmentState.RETRY}:
                raise StateTransitionError(
                    f"cannot submit segment in state {current.value} for finalization"
                )
            connection.execute(
                """
                UPDATE segments
                SET state = ?, last_error = NULL, updated_at_utc = ?
                WHERE segment_id = ?
                """,
                (SegmentState.FINALIZING.value, now, segment_id),
            )
            updated = connection.execute(
                "SELECT * FROM segments WHERE segment_id = ?", (segment_id,)
            ).fetchone()
        assert updated is not None
        return self._row_to_record(updated)

    def apply_final(self, result: FinalResult) -> SegmentRecord:
        if not result.success:
            return self.mark_retry(result.segment_id, result.error or "finalizer failed")

        now = _utc_iso(result.completed_at_utc)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT state, final_raw, final_text, engine_profile
                FROM segments WHERE segment_id = ?
                """,
                (result.segment_id,),
            ).fetchone()
            if row is None:
                raise SegmentNotFoundError(result.segment_id)
            normalized_text = result.normalized_text
            engine_profile = result.engine_profile
            current = SegmentState(row["state"])
            if current in {
                SegmentState.FINAL_READY,
                SegmentState.EXPORTED,
                SegmentState.AUDIO_DELETED,
            }:
                if (
                    str(row["final_raw"]) == result.raw_text
                    and str(row["final_text"]) == normalized_text
                    and str(row["engine_profile"]) == engine_profile
                ):
                    return self.get_segment(result.segment_id)
                raise ValueError(f"conflicting final result: {result.segment_id}")
            if current not in {
                SegmentState.CAPTURED,
                SegmentState.FINALIZING,
                SegmentState.RETRY,
            }:
                raise StateTransitionError(f"cannot finalize segment in state {current.value}")
            connection.execute(
                """
                UPDATE segments
                SET final_raw = ?, final_text = ?, engine_profile = ?, state = ?,
                    last_error = NULL, updated_at_utc = ?
                WHERE segment_id = ?
                """,
                (
                    result.raw_text,
                    normalized_text,
                    engine_profile,
                    SegmentState.FINAL_READY.value,
                    now,
                    result.segment_id,
                ),
            )
        return self.get_segment(result.segment_id)

    def repair_pathological_transcripts(self) -> tuple[str, ...]:
        """Replace legacy decoder loops with their already-durable preview text."""

        repaired: list[str] = []
        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT segment_id, provisional_text, final_text, engine_profile
                FROM segments
                WHERE user_locked = 0 AND final_text <> ''
                ORDER BY started_at_utc, segment_id
                """
            ).fetchall()
            for row in rows:
                final_text = str(row["final_text"])
                if not is_pathological_repetition(final_text):
                    continue
                segment_id = str(row["segment_id"])
                profile = str(row["engine_profile"])
                if ":repeat-filter" not in profile:
                    profile += ":repeat-filter:preview"
                connection.execute(
                    """
                    UPDATE segments
                    SET final_text = ?, engine_profile = ?, updated_at_utc = ?
                    WHERE segment_id = ?
                    """,
                    (str(row["provisional_text"]).strip(), profile, now, segment_id),
                )
                repaired.append(segment_id)
        return tuple(repaired)

    def correct_segment(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn_vocabulary: bool = True,
    ) -> SegmentRecord:
        if not corrected_text.strip():
            raise ValueError("corrected text must not be empty")
        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT final_text, provisional_text
                FROM segments WHERE segment_id = ?
                """,
                (segment_id,),
            ).fetchone()
            if row is None:
                raise SegmentNotFoundError(segment_id)
            cursor = connection.execute(
                """
                UPDATE segments
                SET corrected_text = ?, user_locked = 1, updated_at_utc = ?
                WHERE segment_id = ?
                """,
                (corrected_text.strip(), now, segment_id),
            )
            if cursor.rowcount != 1:
                raise SegmentNotFoundError(segment_id)
            if learn_vocabulary:
                # Imported lazily to keep the storage schema usable on its own.
                from .vocabulary import extract_replacements, replace_contributions

                original = str(row["final_text"] or row["provisional_text"])
                replace_contributions(
                    connection,
                    segment_id,
                    extract_replacements(original, corrected_text.strip()),
                )
        return self.get_segment(segment_id)

    # Controller-friendly alias.
    apply_user_correction = correct_segment

    def transition(
        self,
        segment_id: str,
        target: SegmentState,
        *,
        expected: SegmentState | Sequence[SegmentState] | None = None,
        error: str | None = None,
        increment_retry: bool = False,
    ) -> SegmentRecord:
        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state FROM segments WHERE segment_id = ?", (segment_id,)
            ).fetchone()
            if row is None:
                raise SegmentNotFoundError(segment_id)
            current = SegmentState(row["state"])
            expected_states = (
                {expected}
                if isinstance(expected, SegmentState)
                else set(expected or ())
            )
            if expected_states and current not in expected_states:
                raise StateTransitionError(
                    f"expected {sorted(s.value for s in expected_states)}, got {current.value}"
                )
            if current != target and target not in _ALLOWED_TRANSITIONS[current]:
                raise StateTransitionError(
                    f"invalid transition: {current.value} -> {target.value}"
                )
            connection.execute(
                """
                UPDATE segments
                SET state = ?, last_error = ?,
                    retry_count = retry_count + ?, updated_at_utc = ?
                WHERE segment_id = ?
                """,
                (target.value, error, int(increment_retry), now, segment_id),
            )
        return self.get_segment(segment_id)

    def mark_retry(self, segment_id: str, error: str) -> SegmentRecord:
        current = self.get_segment(segment_id).state
        if current == SegmentState.RETRY:
            now = _utc_iso(datetime.now(UTC))
            with self.transaction() as connection:
                connection.execute(
                    """
                    UPDATE segments SET last_error = ?, retry_count = retry_count + 1,
                                        updated_at_utc = ?
                    WHERE segment_id = ?
                    """,
                    (error, now, segment_id),
                )
            return self.get_segment(segment_id)
        return self.transition(
            segment_id, SegmentState.RETRY, error=error, increment_retry=True
        )

    def mark_exported(self, segment_id: str) -> SegmentRecord:
        current = self.get_segment(segment_id).state
        if current in {SegmentState.EXPORTED, SegmentState.AUDIO_DELETED}:
            return self.get_segment(segment_id)
        return self.transition(
            segment_id,
            SegmentState.EXPORTED,
            expected=SegmentState.FINAL_READY,
        )

    def mark_audio_deleted(self, segment_id: str) -> SegmentRecord:
        current = self.get_segment(segment_id).state
        if current == SegmentState.AUDIO_DELETED:
            return self.get_segment(segment_id)
        return self.transition(
            segment_id,
            SegmentState.AUDIO_DELETED,
            expected=SegmentState.EXPORTED,
        )

    def list_hour_segments(self, key: str) -> list[SegmentRecord]:
        validate_hour_key(key)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM segments
                WHERE hour_key = ?
                ORDER BY started_at_utc, segment_id
                """,
                (key,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_day_segments(self, day_key: str) -> list[SegmentRecord]:
        """Return a local calendar day's durable segments from oldest to newest."""

        validate_day_key(day_key)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM segments
                WHERE hour_key GLOB ?
                ORDER BY started_at_utc, segment_id
                """,
                (f"{day_key}_*",),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_hours(self) -> list[str]:
        """Return all recorded hour keys, newest first, for explicit deletion UI."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT hour_key FROM segments ORDER BY hour_key DESC"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_segments(
        self,
        *,
        states: Sequence[SegmentState] | None = None,
        limit: int | None = None,
    ) -> list[SegmentRecord]:
        query = "SELECT * FROM segments"
        parameters: list[object] = []
        if states:
            placeholders = ",".join("?" for _ in states)
            query += f" WHERE state IN ({placeholders})"
            parameters.extend(state.value for state in states)
        query += " ORDER BY started_at_utc, segment_id"
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must not be negative")
            query += " LIMIT ?"
            parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_pending(self) -> int:
        final_states = (SegmentState.EXPORTED.value, SegmentState.AUDIO_DELETED.value)
        with self._lock:
            return int(
                self._connection.execute(
                    "SELECT count(*) FROM segments WHERE state NOT IN (?, ?)", final_states
                ).fetchone()[0]
            )

    def list_dirty_hours(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT hour_key FROM dirty_hours ORDER BY hour_key"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def dirty_hour_revision(self, key: str) -> str | None:
        validate_hour_key(key)
        with self._lock:
            row = self._connection.execute(
                "SELECT changed_at_utc FROM dirty_hours WHERE hour_key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row[0])

    def clear_dirty_hour_if_unchanged(self, key: str, revision: str | None) -> bool:
        """Acknowledge an export without losing a concurrent text update."""

        validate_hour_key(key)
        if revision is None:
            return self.dirty_hour_revision(key) is None
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM dirty_hours
                WHERE hour_key = ? AND changed_at_utc = ?
                """,
                (key, revision),
            )
        return cursor.rowcount == 1

    def delete_hour(self, key: str) -> DeletedHour:
        validate_hour_key(key)
        now = _utc_iso(datetime.now(UTC))
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT segment_id, audio_path FROM segments WHERE hour_key = ?", (key,)
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO audio_deletion_queue(
                    segment_id, audio_path, hour_key, reason, queued_at_utc
                ) VALUES (?, ?, ?, 'hour-delete', ?)
                ON CONFLICT(segment_id) DO UPDATE SET
                    audio_path = excluded.audio_path,
                    hour_key = excluded.hour_key,
                    reason = excluded.reason,
                    queued_at_utc = excluded.queued_at_utc
                """,
                [
                    (str(row["segment_id"]), str(row["audio_path"]), key, now)
                    for row in rows
                ],
            )
            connection.execute("DELETE FROM segments WHERE hour_key = ?", (key,))
        # secure_delete clears database pages; truncating the WAL prevents deleted
        # transcript text from lingering in the write-ahead log.
        with self._lock:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return DeletedHour(
            hour_key=key,
            segment_ids=tuple(str(row["segment_id"]) for row in rows),
            audio_paths=tuple(Path(row["audio_path"]) for row in rows),
        )

    def acknowledge_audio_deletion(self, segment_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM audio_deletion_queue WHERE segment_id = ?", (segment_id,)
            )

    def pending_audio_deletions(self) -> tuple[tuple[str, Path], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT segment_id, audio_path FROM audio_deletion_queue
                ORDER BY queued_at_utc, segment_id
                """
            ).fetchall()
        return tuple((str(row["segment_id"]), Path(row["audio_path"])) for row in rows)

    def recover(self, spool_dir: Path | None = None) -> RecoveryReport:
        """Reset interrupted work and register complete UUID-named orphan FLACs."""

        now_dt = datetime.now(UTC)
        now = _utc_iso(now_dt)
        reset_ids: list[str] = []
        orphan_ids: list[str] = []
        missing_ids: list[str] = []
        cleanup_ids: list[str] = []
        completed_cleanup_ids: list[str] = []

        with self.transaction() as connection:
            interrupted = connection.execute(
                "SELECT segment_id FROM segments WHERE state = ?",
                (SegmentState.FINALIZING.value,),
            ).fetchall()
            reset_ids = [str(row[0]) for row in interrupted]
            connection.execute(
                """
                UPDATE segments
                SET state = ?, retry_count = retry_count + 1,
                    last_error = 'recovered after interrupted finalization',
                    updated_at_utc = ?
                WHERE state = ?
                """,
                (SegmentState.RETRY.value, now, SegmentState.FINALIZING.value),
            )

            deletion_rows = connection.execute(
                "SELECT segment_id, audio_path FROM audio_deletion_queue"
            ).fetchall()
            deletion_paths = {
                os.path.normcase(os.path.abspath(row["audio_path"])) for row in deletion_rows
            }
            for row in deletion_rows:
                segment_id = str(row["segment_id"])
                try:
                    Path(row["audio_path"]).unlink(missing_ok=True)
                except OSError:
                    cleanup_ids.append(segment_id)
                else:
                    connection.execute(
                        "DELETE FROM audio_deletion_queue WHERE segment_id = ?", (segment_id,)
                    )
                    completed_cleanup_ids.append(segment_id)

            rows = connection.execute(
                "SELECT segment_id, audio_path, state FROM segments"
            ).fetchall()
            known_paths = {
                os.path.normcase(os.path.abspath(row["audio_path"])) for row in rows
            } | deletion_paths
            for row in rows:
                state = SegmentState(row["state"])
                audio_path = Path(row["audio_path"])
                segment_id = str(row["segment_id"])
                if state == SegmentState.EXPORTED:
                    try:
                        audio_path.unlink(missing_ok=True)
                    except OSError:
                        cleanup_ids.append(segment_id)
                    else:
                        connection.execute(
                            """
                            UPDATE segments SET state = ?, last_error = NULL,
                                                updated_at_utc = ?
                            WHERE segment_id = ?
                            """,
                            (SegmentState.AUDIO_DELETED.value, now, segment_id),
                        )
                        completed_cleanup_ids.append(segment_id)
                    continue
                if state not in {SegmentState.AUDIO_DELETED} and not audio_path.exists():
                    missing_ids.append(segment_id)
                    connection.execute(
                        """
                        UPDATE segments SET last_error = ?, updated_at_utc = ?
                        WHERE segment_id = ?
                        """,
                        ("audio file missing during recovery", now, segment_id),
                    )

            if spool_dir is not None:
                spool = Path(spool_dir)
                if spool.exists():
                    for audio_path in sorted(spool.glob("*.flac")):
                        resolved = os.path.normcase(os.path.abspath(audio_path))
                        if resolved in known_paths:
                            continue
                        try:
                            uuid.UUID(audio_path.stem)
                        except ValueError:
                            continue
                        modified = datetime.fromtimestamp(audio_path.stat().st_mtime, tz=UTC)
                        sample_rate = 16_000
                        duration_ms = 0
                        started = modified
                        try:
                            import soundfile as sf

                            audio_info = sf.info(str(audio_path))
                            probed_rate = int(audio_info.samplerate)
                            if probed_rate <= 0:
                                raise ValueError("FLAC sample rate must be positive")
                            duration_seconds = int(audio_info.frames) / probed_rate
                            sample_rate = probed_rate
                            duration_ms = round(duration_seconds * 1000)
                            started = modified - timedelta(seconds=duration_seconds)
                        except (ImportError, OSError, RuntimeError, ValueError, ZeroDivisionError):
                            # The finalizer may still decode a file that libsndfile cannot probe.
                            # Keep the orphan durable and surface its zero-duration fallback.
                            pass
                        segment_id = audio_path.stem
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO segments(
                                segment_id, audio_path, started_at_utc, ended_at_utc,
                                hour_key, state, engine_profile, recovered_orphan,
                                created_at_utc, updated_at_utc,
                                sample_rate, duration_ms
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                            """,
                            (
                                segment_id,
                                str(audio_path.resolve()),
                                _utc_iso(started),
                                _utc_iso(modified),
                                hour_key(started, self.timezone_name),
                                SegmentState.CAPTURED.value,
                                "recovered-orphan",
                                now,
                                now,
                                sample_rate,
                                duration_ms,
                            ),
                        )
                        if connection.execute("SELECT changes()").fetchone()[0]:
                            orphan_ids.append(segment_id)

        return RecoveryReport(
            reset_finalizing=tuple(reset_ids),
            registered_orphans=tuple(orphan_ids),
            missing_audio=tuple(missing_ids),
            pending_audio_deletion=tuple(cleanup_ids),
            completed_audio_deletion=tuple(completed_cleanup_ids),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SegmentRecord:
        return SegmentRecord(
            segment_id=str(row["segment_id"]),
            audio_path=Path(row["audio_path"]),
            started_at_utc=_parse_datetime(row["started_at_utc"]),
            ended_at_utc=_parse_datetime(row["ended_at_utc"]),
            hour_key=str(row["hour_key"]),
            state=SegmentState(row["state"]),
            provisional_raw=str(row["provisional_raw"]),
            provisional_text=str(row["provisional_text"]),
            final_raw=str(row["final_raw"]),
            final_text=str(row["final_text"]),
            corrected_text=str(row["corrected_text"]),
            user_locked=bool(row["user_locked"]),
            engine_profile=str(row["engine_profile"]),
            retry_count=int(row["retry_count"]),
            last_error=row["last_error"],
            sample_rate=int(row["sample_rate"]),
            duration_ms=int(row["duration_ms"]),
            leading_overlap_ms=int(row["leading_overlap_ms"]),
            previous_segment_id=(
                str(row["previous_segment_id"])
                if row["previous_segment_id"] is not None
                else None
            ),
            recovered_orphan=bool(row["recovered_orphan"]),
            created_at_utc=_parse_datetime(row["created_at_utc"]),
            updated_at_utc=_parse_datetime(row["updated_at_utc"]),
        )
