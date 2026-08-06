from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from auto_speech_journal.exporter import MarkdownExporter
from auto_speech_journal.storage import JournalStorage, SegmentNotFoundError
from auto_speech_journal.types import CapturedSegment, FinalResult, SegmentState
from auto_speech_journal.vocabulary import VocabularyStore


def add_segment(
    storage: JournalStorage,
    spool: Path,
    *,
    started: datetime,
    preview: str = "暫定句子",
    leading_overlap_ms: int = 0,
    previous_segment_id: str | None = None,
) -> CapturedSegment:
    segment_id = str(uuid.uuid4())
    path = spool / f"{segment_id}.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC-audio")
    segment = CapturedSegment(
        segment_id,
        path,
        started,
        started + timedelta(seconds=5),
        preview,
        16_000,
        5_000,
        leading_overlap_ms=leading_overlap_ms,
        previous_segment_id=previous_segment_id,
    )
    storage.add_captured(segment)
    return segment


@pytest.fixture
def system(tmp_path: Path):
    storage = JournalStorage(tmp_path / "runtime" / "state.db")
    exporter = MarkdownExporter(storage, tmp_path / "records")
    yield storage, exporter, tmp_path / "spool"
    storage.close()


def test_late_provisional_then_final_atomically_replaces_and_deletes_audio(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    start = datetime(2026, 7, 12, 6, 23, 8, tzinfo=UTC)
    segment = add_segment(storage, spool, started=start)

    results = exporter.export_due_provisionals(
        now_utc=start + timedelta(seconds=30), deadline=timedelta(seconds=10)
    )
    assert len(results) == 1
    path = results[0].path
    provisional = path.read_text(encoding="utf-8")
    assert provisional.startswith("# 2026-07-12 14:00–14:59\n")
    assert f"<!-- asj:id={segment.segment_id} state=provisional -->" in provisional
    assert "- [14:23:08] 暫定句子" in provisional
    assert segment.audio_path.exists()
    assert storage.get_segment(segment.segment_id).state == SegmentState.CAPTURED

    storage.claim_next_for_finalization()
    storage.apply_final(
        FinalResult(segment.segment_id, "簡體原文", "台灣正體定稿", "cuda:int8_float16")
    )
    final_result = exporter.export_segment(segment.segment_id)
    final = path.read_text(encoding="utf-8")
    assert final_result.entry_count == 1
    assert "state=final" in final
    assert "台灣正體定稿" in final
    assert "暫定句子" not in final
    assert storage.get_segment(segment.segment_id).state == SegmentState.AUDIO_DELETED
    assert not segment.audio_path.exists()
    assert list(path.parent.glob("*.tmp")) == []


def test_segment_is_filed_by_start_time_even_when_it_crosses_hour(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    start = datetime(2026, 7, 12, 6, 59, 58, tzinfo=UTC)
    segment = add_segment(storage, spool, started=start)
    storage.apply_final(FinalResult(segment.segment_id, "跨時", "跨時", "cpu:int8"))
    result = exporter.export_segment(segment.segment_id)
    assert result.path.name == "2026-07-12_14.md"
    assert "[14:59:58] 跨時" in result.path.read_text(encoding="utf-8")
    assert not (result.path.parent / "2026-07-12_15.md").exists()


def test_records_root_can_switch_before_recording_starts(
    system: tuple[JournalStorage, MarkdownExporter, Path],
    tmp_path: Path,
) -> None:
    storage, exporter, spool = system
    next_root = tmp_path / "selected-records"
    exporter.set_records_root(next_root)
    start = datetime(2026, 7, 12, 6, 23, 8, tzinfo=UTC)
    segment = add_segment(storage, spool, started=start)
    storage.apply_final(FinalResult(segment.segment_id, "新路徑", "新路徑", "cpu:int8"))

    result = exporter.export_segment(segment.segment_id)

    assert exporter.records_root == next_root.resolve()
    assert result.path.is_relative_to(next_root)
    assert result.path.exists()


def test_user_lock_wins_when_final_arrives_after_correction(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    start = datetime(2026, 7, 12, 7, 0, tzinfo=UTC)
    segment = add_segment(storage, spool, started=start, preview="機器暫定")
    storage.correct_segment(segment.segment_id, "我的修正")
    storage.apply_final(FinalResult(segment.segment_id, "模型", "模型定稿", "cuda"))
    result = exporter.export_segment(segment.segment_id)
    content = result.path.read_text(encoding="utf-8")
    assert "state=corrected" in content
    assert "我的修正" in content
    assert "模型定稿" not in content


def test_overlap_display_recomputes_after_predecessor_correction(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    start = datetime(2026, 7, 12, 7, 10, tzinfo=UTC)
    first = add_segment(storage, spool, started=start)
    second = add_segment(
        storage,
        spool,
        started=start + timedelta(seconds=4),
        leading_overlap_ms=1_000,
        previous_segment_id=first.segment_id,
    )
    storage.apply_final(
        FinalResult(first.segment_id, "前文重叠", "前文重疊", "cuda:int8_float16")
    )
    storage.apply_final(
        FinalResult(second.segment_id, "重叠后文", "重疊後文", "cuda:int8_float16")
    )

    before, _ = exporter.render_hour("2026-07-12_15")
    assert "前文重疊" in before
    assert "] 後文" in before
    assert storage.get_segment(second.segment_id).final_text == "重疊後文"

    storage.correct_segment(first.segment_id, "完全不同")
    after, _ = exporter.render_hour("2026-07-12_15")

    assert "完全不同" in after
    assert "] 重疊後文" in after


def test_cross_hour_overlap_never_creates_cross_file_dependency(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    first = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 7, 59, 58, tzinfo=UTC),
    )
    second = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 8, 0, 2, tzinfo=UTC),
        leading_overlap_ms=1_000,
        previous_segment_id=first.segment_id,
    )
    storage.apply_final(
        FinalResult(first.segment_id, "前文重叠", "前文重疊", "cuda:int8_float16")
    )
    storage.apply_final(
        FinalResult(second.segment_id, "重叠后文", "重疊後文", "cuda:int8_float16")
    )
    exporter.export_segment(first.segment_id)
    second_result = exporter.export_segment(second.segment_id)

    before = second_result.path.read_text(encoding="utf-8")
    assert "] 重疊後文" in before

    storage.correct_segment(first.segment_id, "完全不同")
    exporter.rebuild_hour("2026-07-12_15")

    assert second_result.path.read_text(encoding="utf-8") == before
    assert storage.list_dirty_hours() == []


def test_delete_hour_removes_db_markdown_audio_and_vocabulary(
    system: tuple[JournalStorage, MarkdownExporter, Path]
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
        preview="錯字",
    )
    vocabulary = VocabularyStore(storage)
    vocabulary.apply_correction(segment.segment_id, "正字")
    exporter.rebuild_hour("2026-07-12_16")
    markdown = exporter.path_for_hour("2026-07-12_16")
    assert markdown.exists()
    assert vocabulary.term_counts() == {"正": 1}

    result = exporter.delete_hour("2026-07-12_16")
    assert result.deleted.segment_ids == (segment.segment_id,)
    assert not markdown.exists()
    assert not segment.audio_path.exists()
    assert vocabulary.term_counts() == {}
    with pytest.raises(SegmentNotFoundError):
        storage.get_segment(segment.segment_id)


def test_failed_replace_preserves_previous_file_and_state(
    system: tuple[JournalStorage, MarkdownExporter, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 9, 0, tzinfo=UTC),
    )
    storage.apply_final(FinalResult(segment.segment_id, "定稿", "定稿", "cpu"))
    destination = exporter.path_for_hour("2026-07-12_17")
    destination.parent.mkdir(parents=True)
    destination.write_text("old\n", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("auto_speech_journal.exporter.os.replace", fail_replace)
    with pytest.raises(PermissionError, match="locked"):
        exporter.rebuild_hour("2026-07-12_17")
    assert destination.read_text(encoding="utf-8") == "old\n"
    assert storage.get_segment(segment.segment_id).state == SegmentState.FINAL_READY
    assert segment.audio_path.exists()
    assert not list(destination.parent.glob(".*.tmp"))


def test_concurrent_correction_remains_dirty_for_follow_up_rebuild(
    system: tuple[JournalStorage, MarkdownExporter, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
        preview="舊文字",
    )
    real_replace = __import__("os").replace

    def correct_during_replace(source: Path, destination: Path) -> None:
        storage.correct_segment(segment.segment_id, "並行修正")
        real_replace(source, destination)

    monkeypatch.setattr(
        "auto_speech_journal.exporter.os.replace", correct_during_replace
    )
    first = exporter.rebuild_hour("2026-07-12_18")
    assert "舊文字" in first.path.read_text(encoding="utf-8")
    assert storage.list_dirty_hours() == ["2026-07-12_18"]

    monkeypatch.setattr("auto_speech_journal.exporter.os.replace", real_replace)
    exporter.rebuild_dirty()
    assert "並行修正" in first.path.read_text(encoding="utf-8")
    assert storage.list_dirty_hours() == []


def test_final_audio_file_lock_is_retried_until_state_is_deleted(
    system: tuple[JournalStorage, MarkdownExporter, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 11, 0, tzinfo=UTC),
    )
    storage.apply_final(FinalResult(segment.segment_id, "定稿", "定稿", "cpu"))
    real_unlink = Path.unlink
    locked = True

    def deny_audio_delete(path: Path, *, missing_ok: bool = False) -> None:
        if locked and path == segment.audio_path:
            raise PermissionError("file is locked")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_audio_delete)
    result = exporter.export_segment(segment.segment_id)
    assert result.audio_cleanup_failures == (segment.audio_path,)
    assert storage.get_segment(segment.segment_id).state == SegmentState.EXPORTED
    assert segment.audio_path.exists()

    locked = False
    retried = exporter.retry_pending_deletions()
    assert retried.completed_segment_ids == (segment.segment_id,)
    assert retried.pending_paths == ()
    assert storage.get_segment(segment.segment_id).state == SegmentState.AUDIO_DELETED
    assert not segment.audio_path.exists()


def test_hour_delete_tombstone_survives_lock_and_retry(
    system: tuple[JournalStorage, MarkdownExporter, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )
    real_unlink = Path.unlink
    locked = True

    def deny_audio_delete(path: Path, *, missing_ok: bool = False) -> None:
        if locked and path == segment.audio_path:
            raise PermissionError("file is locked")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_audio_delete)
    deleted = exporter.delete_hour("2026-07-12_20")
    assert deleted.audio_cleanup_failures == (segment.audio_path,)
    assert storage.pending_audio_deletions() == ((segment.segment_id, segment.audio_path),)
    assert storage.list_segments() == []

    first_retry = exporter.retry_pending_deletions()
    assert first_retry.completed_segment_ids == ()
    assert first_retry.pending_paths == (segment.audio_path,)

    locked = False
    second_retry = exporter.retry_pending_deletions()
    assert second_retry.completed_segment_ids == (segment.segment_id,)
    assert second_retry.pending_paths == ()
    assert storage.pending_audio_deletions() == ()
    assert not segment.audio_path.exists()


def test_hour_delete_retries_locked_markdown_without_restoring_db(
    system: tuple[JournalStorage, MarkdownExporter, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, exporter, spool = system
    segment = add_segment(
        storage,
        spool,
        started=datetime(2026, 7, 12, 6, 0, tzinfo=UTC),
    )
    markdown = exporter.rebuild_hour("2026-07-12_14").path
    real_unlink = Path.unlink
    locked = True

    def deny_markdown_delete(path: Path, *, missing_ok: bool = False) -> None:
        if locked and path == markdown:
            raise PermissionError("markdown is locked")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_markdown_delete)
    deleted = exporter.delete_hour("2026-07-12_14")

    assert deleted.markdown_cleanup_failure == markdown
    assert deleted.cleanup_failures == (markdown,)
    assert storage.list_hours() == []
    assert markdown.exists()
    assert storage.list_dirty_hours() == ["2026-07-12_14"]

    pending = exporter.retry_pending_deletions()
    assert pending.pending_paths == (markdown,)

    locked = False
    completed = exporter.retry_pending_deletions()
    assert completed.pending_paths == ()
    assert not markdown.exists()
    assert storage.list_dirty_hours() == []
    assert not segment.audio_path.exists()
