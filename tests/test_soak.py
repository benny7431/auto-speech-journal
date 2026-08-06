from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auto_speech_journal.exporter import MarkdownExporter
from auto_speech_journal.storage import JournalStorage
from auto_speech_journal.types import CapturedSegment, FinalResult, SegmentState


def test_time_compressed_eight_hour_queue_and_markdown_soak(tmp_path: Path) -> None:
    """Exercise eight virtual hours; this is not a real-device or P95 latency test."""

    database = tmp_path / "runtime" / "state.db"
    spool = tmp_path / "spool"
    records = tmp_path / "records"
    storage = JournalStorage(database)
    exporter = MarkdownExporter(storage, records)
    virtual_start = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)
    accepted_per_hour = 24
    expected_ids: set[str] = set()
    max_pending = 0
    max_dirty_hours = 0

    try:
        for hour_offset in range(8):
            hour_start = virtual_start + timedelta(hours=hour_offset)
            # 120 virtual 30-second windows/hour. Silence and rejected noise do
            # not enter the durable queue; every fifth window is accepted speech.
            for window in range(120):
                if window % 5:
                    continue
                started = hour_start + timedelta(seconds=window * 30)
                segment_id = str(uuid.uuid4())
                expected_ids.add(segment_id)
                audio_path = spool / f"{segment_id}.flac"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"fLaC-synthetic-audio")
                text = f"虛擬第 {hour_offset + 1} 小時第 {window // 5 + 1} 段"
                storage.add_captured(
                    CapturedSegment(
                        segment_id=segment_id,
                        audio_path=audio_path,
                        started_at_utc=started,
                        ended_at_utc=started + timedelta(seconds=2),
                        preview_text=f"暫定{text}",
                        preview_raw_text=f"暫定{text}",
                        sample_rate=16_000,
                        duration_ms=2_000,
                    )
                )
                storage.apply_final(
                    FinalResult(segment_id, text, text, "soak:synthetic-cpu-int8")
                )
                max_pending = max(max_pending, storage.count_pending())
                max_dirty_hours = max(max_dirty_hours, len(storage.list_dirty_hours()))

            rebuilt = exporter.rebuild_dirty()
            assert len(rebuilt) == 1
            assert rebuilt[0].entry_count == accepted_per_hour
            assert storage.count_pending() == 0
            assert storage.list_dirty_hours() == []
            assert not tuple(spool.glob("*.flac"))

        records_after_soak = storage.list_segments()
        assert len(records_after_soak) == 8 * accepted_per_hour
        assert {record.segment_id for record in records_after_soak} == expected_ids
        assert {record.state for record in records_after_soak} == {
            SegmentState.AUDIO_DELETED
        }
        assert max_pending == accepted_per_hour
        assert max_dirty_hours == 1

        markdown_paths = sorted(records.glob("*/*.md"))
        assert len(markdown_paths) == 8
        before_replay = {path: path.read_bytes() for path in markdown_paths}
        assert sum(blob.count(b"<!-- asj:id=") for blob in before_replay.values()) == (
            8 * accepted_per_hour
        )
        assert len(list(records.rglob("*.tmp"))) == 0

        # Replaying an already drained rebuild queue stays a no-op rather than
        # accumulating work or appending duplicate Markdown entries.
        for _ in range(32):
            assert exporter.rebuild_dirty() == []
        assert {path: path.read_bytes() for path in markdown_paths} == before_replay
        assert storage.list_dirty_hours() == []
        assert storage.count_pending() == 0
    finally:
        storage.close()
