from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .storage import (
    DeletedHour,
    JournalStorage,
    SegmentRecord,
    _deduplicate_overlap,
    validate_hour_key,
)
from .types import SegmentState

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class ExportResult:
    hour_key: str
    path: Path
    entry_count: int
    removed_empty_file: bool = False
    audio_cleanup_failures: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class HourDeleteResult:
    deleted: DeletedHour
    markdown_path: Path
    audio_cleanup_failures: tuple[Path, ...]
    markdown_cleanup_failure: Path | None = None

    @property
    def cleanup_failures(self) -> tuple[Path, ...]:
        markdown = (
            (self.markdown_cleanup_failure,)
            if self.markdown_cleanup_failure is not None
            else ()
        )
        return markdown + self.audio_cleanup_failures


@dataclass(frozen=True, slots=True)
class DeletionRetryResult:
    completed_segment_ids: tuple[str, ...]
    pending_paths: tuple[Path, ...]


def _one_line(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())


class MarkdownExporter:
    """Deterministically rebuild program-owned hourly Markdown files."""

    def __init__(self, storage: JournalStorage, records_root: Path) -> None:
        self.storage = storage
        self.records_root = Path(records_root).expanduser().resolve()

    def set_records_root(self, records_root: Path) -> None:
        """Switch future Markdown writes while no recorder workers are active."""

        candidate = Path(records_root).expanduser()
        if not candidate.is_absolute():
            raise ValueError("records_root must be an absolute path")
        candidate = candidate.resolve()
        candidate.mkdir(parents=True, exist_ok=True)
        self.records_root = candidate

    def path_for_hour(self, key: str) -> Path:
        validate_hour_key(key)
        return self.records_root / key[:10] / f"{key}.md"

    def render_hour(self, key: str) -> tuple[str, int]:
        validate_hour_key(key)
        records = self.storage.list_hour_segments(key)
        renderable = [record for record in records if self._should_render(record)]
        if not renderable:
            return "", 0

        date, hour = key.rsplit("_", 1)
        lines = [f"# {date} {hour}:00\N{EN DASH}{hour}:59", ""]
        records_by_id = {record.segment_id: record for record in records}
        for record in renderable:
            if not _SAFE_ID_RE.fullmatch(record.segment_id):
                raise ValueError(f"unsafe segment id for Markdown metadata: {record.segment_id!r}")
            local_time = record.started_at_utc.astimezone(self.storage.timezone)
            text = _one_line(self._render_text(record, records_by_id))
            lines.append(
                f"<!-- asj:id={record.segment_id} state={record.display_state} -->"
            )
            lines.append(f"- [{local_time:%H:%M:%S}] {text}".rstrip())
            lines.append("")
        return "\n".join(lines), len(renderable)

    def _render_text(
        self,
        record: SegmentRecord,
        records_by_id: dict[str, SegmentRecord],
    ) -> str:
        """Derive overlap display without mutating the canonical model final."""

        if (
            record.user_locked
            or not record.final_text
            or not record.previous_segment_id
            or record.leading_overlap_ms <= 0
        ):
            return record.display_text
        previous = records_by_id.get(record.previous_segment_id)
        if previous is None:
            # Hour files are independent rebuild units. Never derive one file
            # from a predecessor stored in a different hour, otherwise a later
            # correction would require cross-hour invalidation and could leave
            # stale, destructively shortened Markdown.
            return record.display_text
        previous_text = (
            previous.corrected_text if previous.user_locked else previous.final_text
        )
        if not previous_text:
            return record.display_text
        return _deduplicate_overlap(
            previous_text,
            record.final_text,
            record.leading_overlap_ms,
        )

    def rebuild_hour(self, key: str) -> ExportResult:
        """Atomically replace one hour and clean audio for finalized entries."""

        validate_hour_key(key)
        dirty_revision = self.storage.dirty_hour_revision(key)
        destination = self.path_for_hour(key)
        rendered, count = self.render_hour(key)
        if not rendered:
            destination.unlink(missing_ok=True)
            self.storage.clear_dirty_hour_if_unchanged(key, dirty_revision)
            return ExportResult(key, destination, 0, removed_empty_file=True)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

        cleanup_failures: list[Path] = []
        # Only a successfully replaced file may advance state and release audio.
        for record in self.storage.list_hour_segments(key):
            if record.state == SegmentState.FINAL_READY:
                record = self.storage.mark_exported(record.segment_id)
            if record.state == SegmentState.EXPORTED:
                try:
                    record.audio_path.unlink(missing_ok=True)
                except OSError:
                    cleanup_failures.append(record.audio_path)
                else:
                    self.storage.mark_audio_deleted(record.segment_id)
        self.storage.clear_dirty_hour_if_unchanged(key, dirty_revision)
        return ExportResult(
            key,
            destination,
            count,
            audio_cleanup_failures=tuple(cleanup_failures),
        )

    def export_segment(self, segment_id: str) -> ExportResult:
        return self.rebuild_hour(self.storage.get_segment(segment_id).hour_key)

    def export_due_provisionals(
        self,
        *,
        now_utc: datetime | None = None,
        deadline: timedelta = timedelta(seconds=10),
    ) -> list[ExportResult]:
        """Publish late preview text without treating it as a final result."""

        now = now_utc or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        if deadline.total_seconds() < 0:
            raise ValueError("deadline must not be negative")
        cutoff = now.astimezone(UTC) - deadline
        keys = {
            record.hour_key
            for record in self.storage.list_segments(
                states=(
                    SegmentState.CAPTURED,
                    SegmentState.FINALIZING,
                    SegmentState.RETRY,
                    SegmentState.FAILED,
                )
            )
            if record.provisional_text and record.ended_at_utc <= cutoff
        }
        return [self.rebuild_hour(key) for key in sorted(keys)]

    def rebuild_dirty(self) -> list[ExportResult]:
        return [self.rebuild_hour(key) for key in self.storage.list_dirty_hours()]

    def retry_pending_deletions(self) -> DeletionRetryResult:
        """Retry durable final-export and explicit-hour audio deletions.

        Missing files count as success: a crash may happen after unlink but
        before the corresponding SQLite state/tombstone update.
        """

        completed: list[str] = []
        pending: list[Path] = []
        for record in self.storage.list_segments(states=(SegmentState.EXPORTED,)):
            try:
                record.audio_path.unlink(missing_ok=True)
            except OSError:
                pending.append(record.audio_path)
            else:
                self.storage.mark_audio_deleted(record.segment_id)
                completed.append(record.segment_id)

        for segment_id, audio_path in self.storage.pending_audio_deletions():
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pending.append(audio_path)
            else:
                self.storage.acknowledge_audio_deletion(segment_id)
                completed.append(segment_id)

        # A deleted hour stays dirty until its Markdown file can be removed.
        # This makes a Windows file lock recoverable across process restarts.
        for hour_key in self.storage.list_dirty_hours():
            if self.storage.list_hour_segments(hour_key):
                continue
            dirty_revision = self.storage.dirty_hour_revision(hour_key)
            markdown_path = self.path_for_hour(hour_key)
            try:
                markdown_path.unlink(missing_ok=True)
            except OSError:
                pending.append(markdown_path)
            else:
                self.storage.clear_dirty_hour_if_unchanged(hour_key, dirty_revision)
        return DeletionRetryResult(tuple(completed), tuple(pending))

    def delete_hour(self, key: str) -> HourDeleteResult:
        """Delete an hour from DB, vocabulary, Markdown, and the audio spool."""

        validate_hour_key(key)
        deleted = self.storage.delete_hour(key)
        dirty_revision = self.storage.dirty_hour_revision(key)
        markdown_path = self.path_for_hour(key)
        markdown_cleanup_failure: Path | None = None
        try:
            markdown_path.unlink(missing_ok=True)
        except OSError:
            markdown_cleanup_failure = markdown_path
        cleanup_failures: list[Path] = []
        for segment_id, audio_path in zip(
            deleted.segment_ids, deleted.audio_paths, strict=True
        ):
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                cleanup_failures.append(audio_path)
            else:
                self.storage.acknowledge_audio_deletion(segment_id)
        if markdown_cleanup_failure is None:
            self.storage.clear_dirty_hour_if_unchanged(key, dirty_revision)
        return HourDeleteResult(
            deleted,
            markdown_path,
            tuple(cleanup_failures),
            markdown_cleanup_failure,
        )

    @staticmethod
    def _should_render(record: SegmentRecord) -> bool:
        return bool(record.display_text)
