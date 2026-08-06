from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from auto_speech_journal.storage import JournalStorage
from auto_speech_journal.types import CapturedSegment
from auto_speech_journal.vocabulary import VocabularyStore, extract_replacements


def add_preview(
    storage: JournalStorage,
    tmp_path: Path,
    preview: str,
    *,
    started: datetime | None = None,
) -> str:
    segment_id = str(uuid.uuid4())
    audio = tmp_path / f"{segment_id}.flac"
    audio.write_bytes(b"fLaC")
    start = started or datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    storage.add_captured(
        CapturedSegment(
            segment_id,
            audio,
            start,
            start + timedelta(seconds=1),
            preview,
            16_000,
            1_000,
        )
    )
    return segment_id


def test_extraction_is_conservative_and_bounded() -> None:
    replacements = extract_replacements("甲錯甲錯", "甲對甲對")
    assert [(item.source_text, item.term, item.amount) for item in replacements] == [
        ("錯", "對", 2)
    ]
    assert extract_replacements("原文", "原文新增") == ()
    assert extract_replacements("原文刪除", "原文") == ()
    assert extract_replacements("你好，世界", "你好。世界") == ()
    assert extract_replacements("太長", "新詞語", max_term_chars=1) == ()
    assert len(extract_replacements("甲錯乙壞", "甲對乙好", max_replacements=1)) == 1


def test_apply_recorrection_replaces_contribution_instead_of_double_counting(
    tmp_path: Path,
) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        segment_id = add_preview(storage, tmp_path, "甲錯甲錯")
        vocabulary = VocabularyStore(storage)
        result = vocabulary.apply_correction(segment_id, "甲對甲對")
        assert result.segment.user_locked is True
        assert result.segment.display_text == "甲對甲對"
        assert vocabulary.term_counts() == {"對": 2}

        vocabulary.apply_correction(segment_id, "甲好甲好")
        assert vocabulary.term_counts() == {"好": 2}
        assert vocabulary.contributions_for_segment(segment_id)[0].source_text == "錯"
    finally:
        storage.close()


def test_apply_correction_without_learning_keeps_lock_and_existing_contribution(
    tmp_path: Path,
) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        segment_id = add_preview(storage, tmp_path, "甲錯甲錯")
        vocabulary = VocabularyStore(storage)
        learned = vocabulary.apply_correction(segment_id, "甲對甲對")

        result = vocabulary.apply_correction(segment_id, "甲好甲好", learn=False)

        assert result.segment.user_locked is True
        assert result.segment.display_text == "甲好甲好"
        assert result.replacements == ()
        assert vocabulary.term_counts() == {"對": 2}
        assert vocabulary.contributions_for_segment(segment_id) == learned.replacements
    finally:
        storage.close()


def test_storage_correction_learns_and_clear_rolls_back(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        segment_id = add_preview(storage, tmp_path, "今天錯字")
        storage.correct_segment(segment_id, "今天正字")
        vocabulary = VocabularyStore(storage)
        assert vocabulary.term_counts() == {"正": 1}
        storage.clear_correction(segment_id)
        assert vocabulary.term_counts() == {}
        assert storage.get_segment(segment_id).user_locked is False
    finally:
        storage.close()


def test_delete_term_keeps_other_terms_and_all_correction_locks(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        vocabulary = VocabularyStore(storage)
        first = add_preview(storage, tmp_path, "甲錯")
        second = add_preview(storage, tmp_path, "乙錯")
        vocabulary.apply_correction(first, "甲龍")
        second_result = vocabulary.apply_correction(second, "乙虎")

        assert vocabulary.delete_term("龍") is True

        assert vocabulary.term_counts() == {"虎": 1}
        assert vocabulary.contributions_for_segment(first) == ()
        assert vocabulary.contributions_for_segment(second) == second_result.replacements
        assert storage.get_segment(first).user_locked is True
        assert storage.get_segment(first).display_text == "甲龍"
        assert storage.get_segment(second).user_locked is True
        assert storage.get_segment(second).display_text == "乙虎"
    finally:
        storage.close()


def test_delete_term_handles_missing_and_empty_values(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        vocabulary = VocabularyStore(storage)

        assert vocabulary.delete_term("不存在") is False
        with pytest.raises(ValueError, match="term must not be empty"):
            vocabulary.delete_term("   ")
        assert vocabulary.term_counts() == {}
    finally:
        storage.close()


def test_clear_vocabulary_keeps_correction_locks_and_is_safe_when_empty(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        vocabulary = VocabularyStore(storage)
        first = add_preview(storage, tmp_path, "甲錯")
        second = add_preview(storage, tmp_path, "乙錯")
        vocabulary.apply_correction(first, "甲龍")
        vocabulary.apply_correction(second, "乙虎")

        assert vocabulary.clear() == 2

        assert vocabulary.term_counts() == {}
        assert vocabulary.contributions_for_segment(first) == ()
        assert vocabulary.contributions_for_segment(second) == ()
        assert storage.get_segment(first).user_locked is True
        assert storage.get_segment(first).display_text == "甲龍"
        assert storage.get_segment(second).user_locked is True
        assert storage.get_segment(second).display_text == "乙虎"
        assert vocabulary.clear() == 0
    finally:
        storage.close()


def test_hotwords_are_limited_by_count_total_chars_and_frequency(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        vocabulary = VocabularyStore(storage, max_hotwords=3, max_hotword_chars=5)
        for preview, corrected in [
            ("甲錯", "甲龍"),
            ("乙錯", "乙龍"),
            ("丙錯", "丙虎"),
            ("丁錯", "丁豹"),
        ]:
            vocabulary.apply_correction(add_preview(storage, tmp_path, preview), corrected)
        assert vocabulary.term_counts() == {"龍": 2, "豹": 1, "虎": 1}
        assert vocabulary.hotwords() == ("龍", "豹", "虎")
        assert vocabulary.hotwords(limit=2) == ("龍", "豹")
        assert vocabulary.hotwords(max_chars=1) == ("龍",)
        assert vocabulary.hotwords(minimum_count=2) == ("龍",)
        assert vocabulary.hotword_prompt(limit=2) == "龍,豹"
    finally:
        storage.close()


def test_deleting_one_hour_keeps_other_segments_shared_term_count(tmp_path: Path) -> None:
    storage = JournalStorage(tmp_path / "state.db")
    try:
        vocabulary = VocabularyStore(storage)
        first = add_preview(
            storage,
            tmp_path,
            "錯",
            started=datetime(2026, 7, 12, 6, 0, tzinfo=UTC),
        )
        second = add_preview(
            storage,
            tmp_path,
            "錯",
            started=datetime(2026, 7, 12, 7, 0, tzinfo=UTC),
        )
        vocabulary.apply_correction(first, "對")
        vocabulary.apply_correction(second, "對")
        assert vocabulary.term_counts() == {"對": 2}

        storage.delete_hour("2026-07-12_14")

        assert vocabulary.term_counts() == {"對": 1}
    finally:
        storage.close()
