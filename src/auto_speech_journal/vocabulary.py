from __future__ import annotations

import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher

from .storage import JournalStorage, SegmentNotFoundError, SegmentRecord, _utc_iso


@dataclass(frozen=True, slots=True)
class Replacement:
    source_text: str
    term: str
    amount: int = 1


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    segment: SegmentRecord
    replacements: tuple[Replacement, ...]


def _trim_non_words(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and (
        value[start].isspace() or unicodedata.category(value[start])[0] in {"P", "C"}
    ):
        start += 1
    while end > start and (
        value[end - 1].isspace()
        or unicodedata.category(value[end - 1])[0] in {"P", "C"}
    ):
        end -= 1
    return value[start:end]


def _contains_word_character(value: str) -> bool:
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in value)


def extract_replacements(
    original: str,
    corrected: str,
    *,
    max_replacements: int = 8,
    max_term_chars: int = 24,
) -> tuple[Replacement, ...]:
    """Extract only bounded, unambiguous replace opcodes.

    Insertions and deletions are intentionally ignored: treating them as global
    replacements would turn ordinary editing into unsafe blind substitutions.
    """

    if max_replacements < 0:
        raise ValueError("max_replacements must not be negative")
    if max_term_chars <= 0:
        raise ValueError("max_term_chars must be positive")
    if not original or not corrected or original == corrected or max_replacements == 0:
        return ()

    matcher = SequenceMatcher(a=original, b=corrected, autojunk=False)
    pairs: Counter[tuple[str, str]] = Counter()
    for tag, original_start, original_end, corrected_start, corrected_end in matcher.get_opcodes():
        if tag != "replace":
            continue
        source = _trim_non_words(original[original_start:original_end])
        term = _trim_non_words(corrected[corrected_start:corrected_end])
        if not source or not term or source == term:
            continue
        if len(source) > max_term_chars or len(term) > max_term_chars:
            continue
        if not _contains_word_character(source) or not _contains_word_character(term):
            continue
        pairs[(source, term)] += 1

    ordered = sorted(
        pairs.items(),
        key=lambda item: (-item[1], item[0][1], item[0][0]),
    )[:max_replacements]
    return tuple(
        Replacement(source_text=source, term=term, amount=amount)
        for (source, term), amount in ordered
    )


def replace_contributions(
    connection: sqlite3.Connection,
    segment_id: str,
    replacements: tuple[Replacement, ...],
    *,
    now_utc: datetime | None = None,
) -> None:
    """Replace one segment's learned terms inside the caller's transaction."""

    now = _utc_iso(now_utc or datetime.now(UTC))
    connection.execute(
        "DELETE FROM vocabulary_contributions WHERE segment_id = ?", (segment_id,)
    )
    connection.executemany(
        """
        INSERT INTO vocabulary_contributions(
            segment_id, source_text, term, amount, created_at_utc
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (segment_id, replacement.source_text, replacement.term, replacement.amount, now)
            for replacement in replacements
        ],
    )


class VocabularyStore:
    def __init__(
        self,
        storage: JournalStorage,
        *,
        max_replacements_per_correction: int = 8,
        max_term_chars: int = 24,
        max_hotwords: int = 32,
        max_hotword_chars: int = 256,
    ) -> None:
        if max_replacements_per_correction < 0:
            raise ValueError("max_replacements_per_correction must not be negative")
        if max_term_chars <= 0 or max_hotwords <= 0 or max_hotword_chars <= 0:
            raise ValueError("vocabulary bounds must be positive")
        self.storage = storage
        self.max_replacements_per_correction = max_replacements_per_correction
        self.max_term_chars = max_term_chars
        self.max_hotwords = max_hotwords
        self.max_hotword_chars = max_hotword_chars

    def apply_correction(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn: bool = True,
    ) -> CorrectionResult:
        if not corrected_text.strip():
            raise ValueError("corrected text must not be empty")
        if not isinstance(learn, bool):
            raise TypeError("learn must be boolean")
        with self.storage.transaction() as connection:
            row = connection.execute(
                """
                SELECT final_text, provisional_text
                FROM segments WHERE segment_id = ?
                """,
                (segment_id,),
            ).fetchone()
            if row is None:
                raise SegmentNotFoundError(segment_id)
            original = str(row["final_text"] or row["provisional_text"])
            replacements = (
                extract_replacements(
                    original,
                    corrected_text.strip(),
                    max_replacements=self.max_replacements_per_correction,
                    max_term_chars=self.max_term_chars,
                )
                if learn
                else ()
            )
            segment = self.storage.correct_segment(
                segment_id, corrected_text.strip(), learn_vocabulary=False
            )
            if learn:
                replace_contributions(connection, segment_id, replacements)
        return CorrectionResult(segment, replacements)

    def rollback_segment(self, segment_id: str) -> None:
        with self.storage.transaction() as connection:
            connection.execute(
                "DELETE FROM vocabulary_contributions WHERE segment_id = ?", (segment_id,)
            )

    def contributions_for_segment(self, segment_id: str) -> tuple[Replacement, ...]:
        with self.storage.transaction(immediate=False) as connection:
            rows = connection.execute(
                """
                SELECT source_text, term, amount
                FROM vocabulary_contributions
                WHERE segment_id = ?
                ORDER BY term, source_text
                """,
                (segment_id,),
            ).fetchall()
        return tuple(
            Replacement(str(row["source_text"]), str(row["term"]), int(row["amount"]))
            for row in rows
        )

    def term_counts(self) -> dict[str, int]:
        with self.storage.transaction(immediate=False) as connection:
            rows = connection.execute(
                """
                SELECT term, use_count FROM vocabulary_terms
                ORDER BY use_count DESC, term
                """
            ).fetchall()
        return {str(row["term"]): int(row["use_count"]) for row in rows}

    def delete_term(self, term: str) -> bool:
        normalized = term.strip()
        if not normalized:
            raise ValueError("term must not be empty")
        with self.storage.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM vocabulary_terms WHERE term = ?",
                (normalized,),
            ).fetchone()
            connection.execute(
                "DELETE FROM vocabulary_contributions WHERE term = ?",
                (normalized,),
            )
            connection.execute(
                "DELETE FROM vocabulary_terms WHERE term = ?",
                (normalized,),
            )
        return exists is not None

    def clear(self) -> int:
        with self.storage.transaction() as connection:
            row = connection.execute("SELECT COUNT(*) FROM vocabulary_terms").fetchone()
            count = int(row[0])
            connection.execute("DELETE FROM vocabulary_contributions")
            connection.execute("DELETE FROM vocabulary_terms")
        return count

    def hotwords(
        self,
        *,
        limit: int | None = None,
        max_chars: int | None = None,
        minimum_count: int = 1,
    ) -> tuple[str, ...]:
        actual_limit = self.max_hotwords if limit is None else min(limit, self.max_hotwords)
        actual_max_chars = (
            self.max_hotword_chars
            if max_chars is None
            else min(max_chars, self.max_hotword_chars)
        )
        if actual_limit < 0 or actual_max_chars < 0:
            raise ValueError("hotword bounds must not be negative")
        if minimum_count <= 0:
            raise ValueError("minimum_count must be positive")
        if actual_limit == 0 or actual_max_chars == 0:
            return ()

        with self.storage.transaction(immediate=False) as connection:
            rows = connection.execute(
                """
                SELECT term FROM vocabulary_terms
                WHERE use_count >= ?
                ORDER BY use_count DESC, updated_at_utc DESC, term
                """,
                (minimum_count,),
            ).fetchall()
        selected: list[str] = []
        used_chars = 0
        for row in rows:
            if len(selected) >= actual_limit:
                break
            term = str(row["term"])
            separator_chars = 1 if selected else 0
            if used_chars + separator_chars + len(term) > actual_max_chars:
                continue
            selected.append(term)
            used_chars += separator_chars + len(term)
        return tuple(selected)

    def hotword_prompt(self, **kwargs: int) -> str:
        return ",".join(self.hotwords(**kwargs))
