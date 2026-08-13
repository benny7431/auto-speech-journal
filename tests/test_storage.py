from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from auto_speech_journal.storage import (
    JournalStorage,
    SegmentNotFoundError,
    StateTransitionError,
    hour_key,
)
from auto_speech_journal.types import CapturedSegment, FinalResult, SegmentState


def captured(
    tmp_path: Path,
    *,
    segment_id: str | None = None,
    started: datetime | None = None,
    preview: str = "暫定文字",
    preview_raw: str = "",
    leading_overlap_ms: int = 0,
    previous_segment_id: str | None = None,
) -> CapturedSegment:
    segment_id = segment_id or str(uuid.uuid4())
    started = started or datetime(2026, 7, 12, 6, 23, 8, tzinfo=UTC)
    tmp_path.mkdir(parents=True, exist_ok=True)
    audio_path = tmp_path / f"{segment_id}.flac"
    audio_path.write_bytes(b"fLaC" + b"x" * 32)
    return CapturedSegment(
        segment_id=segment_id,
        audio_path=audio_path,
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=3),
        preview_text=preview,
        sample_rate=16_000,
        duration_ms=3_000,
        preview_raw_text=preview_raw,
        leading_overlap_ms=leading_overlap_ms,
        previous_segment_id=previous_segment_id,
    )


def test_sqlite_is_wal_secure_and_foreign_keys_enabled(storage: JournalStorage) -> None:
    assert storage.pragmas() == {
        "journal_mode": "wal",
        "secure_delete": 1,
        "foreign_keys": 1,
    }


def test_storage_rejects_newer_or_unversioned_existing_database(tmp_path: Path) -> None:
    newer_path = tmp_path / "newer.db"
    connection = sqlite3.connect(newer_path)
    connection.executescript(
        "CREATE TABLE app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO app_meta VALUES('schema_version', '3');"
    )
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported database schema version 3"):
        JournalStorage(newer_path)

    unversioned_path = tmp_path / "unversioned.db"
    connection = sqlite3.connect(unversioned_path)
    connection.execute("CREATE TABLE segments(segment_id TEXT PRIMARY KEY)")
    connection.close()

    with pytest.raises(RuntimeError, match="unversioned"):
        JournalStorage(unversioned_path)


def test_storage_migrates_v1_overlap_columns_transactionally(tmp_path: Path) -> None:
    database = tmp_path / "v1.db"
    original = JournalStorage(database)
    original.close()
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE app_meta SET value = '1' WHERE key = 'schema_version'"
    )
    connection.execute("ALTER TABLE segments DROP COLUMN previous_segment_id")
    connection.execute("ALTER TABLE segments DROP COLUMN leading_overlap_ms")
    connection.commit()
    connection.close()

    migrated = JournalStorage(database)
    try:
        with sqlite3.connect(database) as check:
            version = check.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            columns = {
                row[1] for row in check.execute("PRAGMA table_info(segments)").fetchall()
            }
        assert version == "2"
        assert {"leading_overlap_ms", "previous_segment_id"} <= columns
    finally:
        migrated.close()


def test_hour_key_uses_taipei_and_requires_aware_datetime() -> None:
    assert hour_key(datetime(2026, 7, 12, 15, 59, tzinfo=UTC)) == "2026-07-12_23"
    assert hour_key(datetime(2026, 7, 12, 16, 0, tzinfo=UTC)) == "2026-07-13_00"
    with pytest.raises(ValueError, match="timezone-aware"):
        hour_key(datetime(2026, 7, 12, 16, 0))


def test_list_hours_returns_newest_first(storage: JournalStorage, tmp_path: Path) -> None:
    storage.add_captured(
        captured(
            tmp_path,
            segment_id="first",
            started=datetime(2026, 7, 12, 1, tzinfo=UTC),
        )
    )
    storage.add_captured(
        captured(
            tmp_path,
            segment_id="second",
            started=datetime(2026, 7, 12, 3, tzinfo=UTC),
        )
    )

    assert storage.list_hours() == ["2026-07-12_11", "2026-07-12_09"]


def test_list_day_segments_uses_taipei_day_boundary_and_validates_key(
    storage: JournalStorage, tmp_path: Path
) -> None:
    starts = (
        ("previous", datetime(2026, 7, 11, 15, 59, 59, tzinfo=UTC)),
        ("midnight", datetime(2026, 7, 11, 16, 0, tzinfo=UTC)),
        ("late", datetime(2026, 7, 12, 15, 59, 59, tzinfo=UTC)),
        ("next", datetime(2026, 7, 12, 16, 0, tzinfo=UTC)),
    )
    for segment_id, started in starts:
        storage.add_captured(
            captured(tmp_path, segment_id=segment_id, started=started)
        )

    assert [
        record.segment_id for record in storage.list_day_segments("2026-07-12")
    ] == ["midnight", "late"]

    for invalid in ("2026-7-12", "2026-02-30", "2026-07-12_00"):
        with pytest.raises(ValueError, match="invalid day key|day is out of range"):
            storage.list_day_segments(invalid)


def test_state_machine_final_and_user_lock_survives_late_result(
    storage: JournalStorage, tmp_path: Path
) -> None:
    segment = captured(tmp_path)
    stored = storage.add_captured(segment)
    assert stored.state == SegmentState.CAPTURED
    assert stored.hour_key == "2026-07-12_14"
    assert stored.display_text == "暫定文字"

    claimed = storage.claim_next_for_finalization()
    assert claimed is not None and claimed.state == SegmentState.FINALIZING
    corrected = storage.correct_segment(segment.segment_id, "使用者修正")
    assert corrected.user_locked is True

    finalized = storage.apply_final(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="模型原文",
            normalized_text="模型定稿",
            engine_profile="cuda:int8_float16",
        )
    )
    assert finalized.state == SegmentState.FINAL_READY
    assert finalized.final_text == "模型定稿"
    assert finalized.display_text == "使用者修正"
    assert finalized.display_state == "corrected"

    assert storage.mark_exported(segment.segment_id).state == SegmentState.EXPORTED
    assert storage.mark_audio_deleted(segment.segment_id).state == SegmentState.AUDIO_DELETED

    # Delivery retries are idempotent, but a different final cannot overwrite
    # an already exported/deleted result.
    repeated = storage.apply_final(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="模型原文",
            normalized_text="模型定稿",
            engine_profile="cuda:int8_float16",
        )
    )
    assert repeated.state == SegmentState.AUDIO_DELETED
    with pytest.raises(ValueError, match="conflicting final"):
        storage.apply_final(
            FinalResult(segment.segment_id, "不同", "不同", "cuda:int8_float16")
        )


def test_failed_final_becomes_retry_and_can_be_claimed_again(
    storage: JournalStorage, tmp_path: Path
) -> None:
    segment = captured(tmp_path)
    storage.add_captured(segment)
    storage.claim_next_for_finalization()
    retried = storage.apply_final(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="",
            normalized_text="",
            engine_profile="cuda",
            success=False,
            error="CUDA out of memory",
        )
    )
    assert retried.state == SegmentState.RETRY
    assert retried.retry_count == 1
    assert retried.last_error == "CUDA out of memory"
    assert storage.claim_next_for_finalization().segment_id == segment.segment_id  # type: ignore[union-attr]


def test_repair_pathological_transcripts_uses_preview_and_is_idempotent(
    tmp_path: Path,
    storage: JournalStorage,
) -> None:
    segment = captured(tmp_path, preview="可讀預覽")
    storage.add_captured(segment)
    storage.apply_final(
        FinalResult(
            segment.segment_id,
            "及時," * 60,
            "及時," * 60,
            "faster-whisper:cuda:int8_float16:s2tw",
        )
    )

    assert storage.repair_pathological_transcripts() == (segment.segment_id,)
    repaired = storage.get_segment(segment.segment_id)
    assert repaired.final_raw == "及時," * 60
    assert repaired.final_text == "可讀預覽"
    assert repaired.engine_profile.endswith(":repeat-filter:preview")
    assert storage.repair_pathological_transcripts() == ()


def test_mark_finalizing_is_idempotent_and_never_regresses_completed_state(
    storage: JournalStorage, tmp_path: Path
) -> None:
    segment = captured(tmp_path)
    storage.add_captured(segment)

    first = storage.mark_finalizing(segment.segment_id)
    repeated = storage.mark_finalizing(segment.segment_id)
    assert first.state == SegmentState.FINALIZING
    assert repeated.state == SegmentState.FINALIZING

    finalized = storage.apply_final(
        FinalResult(segment.segment_id, "完成", "完成", "cpu:int8")
    )
    assert finalized.state == SegmentState.FINAL_READY
    assert storage.mark_finalizing(segment.segment_id).state == SegmentState.FINAL_READY


def test_mark_finalizing_accepts_retry_but_rejects_failed(
    storage: JournalStorage, tmp_path: Path
) -> None:
    retry_segment = captured(tmp_path)
    storage.add_captured(retry_segment)
    storage.mark_retry(retry_segment.segment_id, "transient")
    assert storage.mark_finalizing(retry_segment.segment_id).state == SegmentState.FINALIZING

    failed_segment = captured(tmp_path, started=retry_segment.started_at_utc + timedelta(minutes=1))
    storage.add_captured(failed_segment)
    storage.transition(failed_segment.segment_id, SegmentState.FAILED)
    with pytest.raises(StateTransitionError, match="cannot submit"):
        storage.mark_finalizing(failed_segment.segment_id)


def test_invalid_transition_and_missing_segment_are_explicit(
    storage: JournalStorage, tmp_path: Path
) -> None:
    segment = captured(tmp_path)
    storage.add_captured(segment)
    with pytest.raises(StateTransitionError):
        storage.mark_audio_deleted(segment.segment_id)
    with pytest.raises(SegmentNotFoundError):
        storage.get_segment("missing")


def test_add_is_idempotent_but_rejects_id_collision(
    storage: JournalStorage, tmp_path: Path
) -> None:
    segment = captured(tmp_path)
    assert storage.add_captured(segment) == storage.add_captured(segment)
    other = captured(
        tmp_path / "other",
        segment_id=segment.segment_id,
        started=segment.started_at_utc + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="collision"):
        storage.add_captured(other)


def test_captured_stores_distinct_raw_normalized_preview_and_overlap_metadata(
    storage: JournalStorage, tmp_path: Path
) -> None:
    previous_id = str(uuid.uuid4())
    segment = captured(
        tmp_path,
        preview="簡體預覽",
        preview_raw="简体预览",
        leading_overlap_ms=1_000,
        previous_segment_id=previous_id,
    )

    record = storage.add_captured(segment)

    assert record.provisional_raw == "简体预览"
    assert record.provisional_text == "簡體預覽"
    assert record.leading_overlap_ms == 1_000
    assert record.previous_segment_id == previous_id


def test_forced_overlap_final_preserves_canonical_normalized_text(
    storage: JournalStorage, tmp_path: Path
) -> None:
    first = captured(tmp_path, segment_id=str(uuid.uuid4()))
    second = captured(
        tmp_path,
        segment_id=str(uuid.uuid4()),
        started=first.ended_at_utc - timedelta(seconds=1),
        leading_overlap_ms=1_000,
        previous_segment_id=first.segment_id,
    )
    storage.add_captured(first)
    storage.add_captured(second)
    storage.apply_final(
        FinalResult(first.segment_id, "前文重叠", "前文重疊", "cuda:int8_float16")
    )
    current = FinalResult(
        second.segment_id,
        "重叠后文",
        "重疊後文",
        "cuda:int8_float16",
    )

    record = storage.apply_final(current)

    assert record.final_raw == "重叠后文"
    assert record.final_text == "重疊後文"
    assert not record.engine_profile.endswith(":overlap-dedup")
    assert storage.apply_final(current) == record


def test_forced_overlap_never_deduplicates_against_provisional_predecessor(
    storage: JournalStorage, tmp_path: Path
) -> None:
    first = captured(
        tmp_path,
        segment_id=str(uuid.uuid4()),
        preview="預覽重疊",
    )
    second = captured(
        tmp_path,
        segment_id=str(uuid.uuid4()),
        started=first.ended_at_utc - timedelta(seconds=1),
        leading_overlap_ms=1_000,
        previous_segment_id=first.segment_id,
    )
    storage.add_captured(first)
    storage.add_captured(second)

    current = storage.apply_final(
        FinalResult(
            second.segment_id,
            "重叠后文",
            "重疊後文",
            "cuda:int8_float16",
        )
    )

    assert current.final_text == "重疊後文"
    assert not current.engine_profile.endswith(":overlap-dedup")


def test_recovery_resets_finalizing_registers_orphan_and_reports_cleanup(
    storage: JournalStorage, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    interrupted = captured(spool)
    storage.add_captured(interrupted)
    storage.claim_next_for_finalization()

    exported = captured(spool, started=interrupted.started_at_utc + timedelta(minutes=1))
    storage.add_captured(exported)
    storage.apply_final(
        FinalResult(exported.segment_id, "完成", "完成", "cpu:int8")
    )
    storage.mark_exported(exported.segment_id)

    missing = captured(spool, started=interrupted.started_at_utc + timedelta(minutes=2))
    storage.add_captured(missing)
    missing.audio_path.unlink()

    orphan_id = str(uuid.uuid4())
    orphan = spool / f"{orphan_id}.flac"
    orphan.write_bytes(b"fLaC-orphan")
    (spool / "not-a-uuid.flac").write_bytes(b"ignored")
    (spool / f"{uuid.uuid4()}.flac.tmp").write_bytes(b"incomplete")

    report = storage.recover(spool)
    assert report.reset_finalizing == (interrupted.segment_id,)
    assert report.registered_orphans == (orphan_id,)
    assert report.missing_audio == (missing.segment_id,)
    assert report.pending_audio_deletion == ()
    assert report.completed_audio_deletion == (exported.segment_id,)
    assert storage.get_segment(interrupted.segment_id).state == SegmentState.RETRY
    assert storage.get_segment(orphan_id).recovered_orphan is True
    assert storage.get_segment(exported.segment_id).state == SegmentState.AUDIO_DELETED


def test_recovery_derives_orphan_start_time_and_hour_from_flac_duration(
    storage: JournalStorage, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    orphan_id = str(uuid.uuid4())
    orphan = spool / f"{orphan_id}.flac"
    sf.write(orphan, np.zeros(32_000, dtype=np.float32), 16_000, format="FLAC")
    ended = datetime(2026, 7, 12, 6, 0, 1, tzinfo=UTC)
    os.utime(orphan, (ended.timestamp(), ended.timestamp()))

    report = storage.recover(spool)

    assert report.registered_orphans == (orphan_id,)
    recovered = storage.get_segment(orphan_id)
    assert recovered.started_at_utc == ended - timedelta(seconds=2)
    assert recovered.ended_at_utc == ended
    assert recovered.hour_key == "2026-07-12_13"
    assert recovered.sample_rate == 16_000
    assert recovered.duration_ms == 2_000


def test_recovery_finishes_crash_interrupted_hour_audio_deletion(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    spool = tmp_path / "spool"
    first = JournalStorage(database)
    segment = captured(spool)
    first.add_captured(segment)
    first.delete_hour("2026-07-12_14")
    assert first.pending_audio_deletions() == ((segment.segment_id, segment.audio_path),)
    first.close()

    recovered = JournalStorage(database)
    try:
        report = recovered.recover(spool)
        assert report.completed_audio_deletion == (segment.segment_id,)
        assert report.registered_orphans == ()
        assert recovered.pending_audio_deletions() == ()
        assert recovered.list_segments() == []
        assert not segment.audio_path.exists()
    finally:
        recovered.close()


def test_recovery_does_not_resurrect_locked_hour_deletion_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state.db"
    spool = tmp_path / "spool"
    storage = JournalStorage(database)
    segment = captured(spool)
    storage.add_captured(segment)
    storage.delete_hour("2026-07-12_14")
    real_unlink = Path.unlink

    def deny_audio_delete(path: Path, *, missing_ok: bool = False) -> None:
        if path == segment.audio_path:
            raise PermissionError("file is locked")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_audio_delete)
    try:
        report = storage.recover(spool)
        assert report.pending_audio_deletion == (segment.segment_id,)
        assert report.registered_orphans == ()
        assert storage.list_segments() == []
        assert storage.pending_audio_deletions() == ((segment.segment_id, segment.audio_path),)
    finally:
        storage.close()
