"""Replay durable-storage crash boundaries without starting audio or ASR workers.

Run from a source checkout with::

    $env:PYTHONPATH = "src"
    uv run --no-sync python tools/replay_fault_recovery.py --workdir artifacts/fault-replay

The supplied work directory must not already exist.  Omitting ``--workdir``
uses a temporary directory and still exercises every boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import soundfile as sf

from auto_speech_journal.exporter import MarkdownExporter
from auto_speech_journal.storage import JournalStorage
from auto_speech_journal.types import CapturedSegment, FinalResult, SegmentState


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    segment_count: int
    markdown_entry_count: int


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _write_flac(path: Path, *, seconds: float = 0.25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        path,
        np.zeros(round(16_000 * seconds), dtype=np.float32),
        16_000,
        format="FLAC",
        subtype="PCM_16",
    )


def _captured(
    spool: Path,
    *,
    started_at_utc: datetime,
    preview: str,
) -> CapturedSegment:
    segment_id = str(uuid.uuid4())
    audio_path = spool / f"{segment_id}.flac"
    _write_flac(audio_path)
    return CapturedSegment(
        segment_id=segment_id,
        audio_path=audio_path,
        started_at_utc=started_at_utc,
        ended_at_utc=started_at_utc + timedelta(milliseconds=250),
        preview_text=preview,
        preview_raw_text=preview,
        sample_rate=16_000,
        duration_ms=250,
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_markdown(
    exporter: MarkdownExporter,
    hour: str,
    expected: dict[str, str],
) -> int:
    path = exporter.path_for_hour(hour)
    content = path.read_text(encoding="utf-8")
    for segment_id, text in expected.items():
        marker = f"<!-- asj:id={segment_id} state=final -->"
        _check(content.count(marker) == 1, f"{segment_id}: metadata is missing or duplicated")
        _check(content.count(text) == 1, f"{segment_id}: transcript is missing or duplicated")
    return sum(content.count(f"<!-- asj:id={segment_id} ") for segment_id in expected)


def replay_flac_before_db(root: Path) -> ScenarioResult:
    """Crash after final FLAC rename and before the CAPTURED insert."""

    database = root / "state.db"
    spool = root / "spool"
    records = root / "records"
    segment_id = str(uuid.uuid4())
    audio_path = spool / f"{segment_id}.flac"
    _write_flac(audio_path, seconds=1.0)
    ended_at = datetime(2026, 7, 12, 6, 23, 9, tzinfo=UTC)
    os.utime(audio_path, (ended_at.timestamp(), ended_at.timestamp()))

    with JournalStorage(database) as storage:
        first = storage.recover(spool)
        second = storage.recover(spool)
        _check(first.registered_orphans == (segment_id,), "orphan FLAC was not registered")
        _check(second.registered_orphans == (), "orphan FLAC was registered twice")
        _check(len(storage.list_segments()) == 1, "orphan recovery duplicated a segment")
        storage.apply_final(
            FinalResult(segment_id, "復原定稿", "復原定稿", "replay:cpu-int8")
        )
        exporter = MarkdownExporter(storage, records)
        exporter.export_segment(segment_id)
        _check(
            storage.get_segment(segment_id).state == SegmentState.AUDIO_DELETED,
            "recovered orphan did not reach AUDIO_DELETED",
        )
        _check(not audio_path.exists(), "successfully exported orphan audio was retained")
        entries = _verify_markdown(
            exporter,
            storage.get_segment(segment_id).hour_key,
            {segment_id: "復原定稿"},
        )
    return ScenarioResult("flac-before-db", 1, entries)


def replay_db_pending_states(root: Path) -> ScenarioResult:
    """Crash with one CAPTURED row and one FINALIZING row committed."""

    database = root / "state.db"
    spool = root / "spool"
    records = root / "records"
    start = datetime(2026, 7, 12, 7, 1, tzinfo=UTC)
    captured = _captured(spool, started_at_utc=start, preview="已擷取")
    finalizing = _captured(
        spool,
        started_at_utc=start + timedelta(minutes=1),
        preview="定稿途中",
    )
    storage = JournalStorage(database)
    storage.add_captured(captured)
    storage.add_captured(finalizing)
    storage.mark_finalizing(finalizing.segment_id)
    storage.close()

    with JournalStorage(database) as reopened:
        report = reopened.recover(spool)
        _check(report.reset_finalizing == (finalizing.segment_id,), "FINALIZING was not reset")
        _check(
            reopened.get_segment(captured.segment_id).state == SegmentState.CAPTURED,
            "CAPTURED row changed during recovery",
        )
        _check(
            reopened.get_segment(finalizing.segment_id).state == SegmentState.RETRY,
            "FINALIZING row did not become RETRY",
        )
        _check(len(reopened.list_segments()) == 2, "pending recovery duplicated rows")
        expected = {
            captured.segment_id: "已擷取定稿",
            finalizing.segment_id: "重試定稿",
        }
        for segment_id, text in expected.items():
            reopened.apply_final(
                FinalResult(segment_id, text, text, "replay:cpu-int8")
            )
        exporter = MarkdownExporter(reopened, records)
        results = exporter.rebuild_dirty()
        _check(len(results) == 1, "pending rows did not coalesce to one hourly rebuild")
        _check(reopened.count_pending() == 0, "pending rows did not drain")
        _check(not tuple(spool.glob("*.flac")), "exported pending audio was retained")
        entries = _verify_markdown(exporter, "2026-07-12_15", expected)
    return ScenarioResult("db-captured-finalizing", 2, entries)


def replay_provisional_markdown(root: Path) -> ScenarioResult:
    """Crash after provisional Markdown replace and before a final result."""

    database = root / "state.db"
    spool = root / "spool"
    records = root / "records"
    started = datetime(2026, 7, 12, 8, 3, tzinfo=UTC)
    segment = _captured(spool, started_at_utc=started, preview="暫定內容")
    storage = JournalStorage(database)
    storage.add_captured(segment)
    exporter = MarkdownExporter(storage, records)
    exporter.export_due_provisionals(
        now_utc=segment.ended_at_utc + timedelta(seconds=11)
    )
    provisional_path = exporter.path_for_hour("2026-07-12_16")
    provisional = provisional_path.read_text(encoding="utf-8")
    _check(provisional.count(f"asj:id={segment.segment_id}") == 1, "bad provisional export")
    storage.close()

    with JournalStorage(database) as reopened:
        reopened.recover(spool)
        exporter = MarkdownExporter(reopened, records)
        exporter.export_due_provisionals(
            now_utc=segment.ended_at_utc + timedelta(seconds=12)
        )
        replayed = provisional_path.read_text(encoding="utf-8")
        _check(
            replayed.count(f"asj:id={segment.segment_id}") == 1,
            "provisional replay duplicated Markdown",
        )
        reopened.apply_final(
            FinalResult(segment.segment_id, "最終內容", "最終內容", "replay:cpu-int8")
        )
        exporter.export_segment(segment.segment_id)
        final = provisional_path.read_text(encoding="utf-8")
        _check("暫定內容" not in final, "final export retained provisional text")
        _check(not segment.audio_path.exists(), "final provisional-replay audio was retained")
        entries = _verify_markdown(
            exporter,
            "2026-07-12_16",
            {segment.segment_id: "最終內容"},
        )
    return ScenarioResult("provisional-markdown", 1, entries)


def replay_final_ready_and_exported(root: Path) -> ScenarioResult:
    """Crash once at FINAL_READY and once after EXPORTED but before unlink."""

    database = root / "state.db"
    spool = root / "spool"
    records = root / "records"
    start = datetime(2026, 7, 12, 9, 10, tzinfo=UTC)
    ready = _captured(spool, started_at_utc=start, preview="準備完成")
    exported = _captured(
        spool,
        started_at_utc=start + timedelta(minutes=1),
        preview="已匯出",
    )
    storage = JournalStorage(database)
    storage.add_captured(ready)
    storage.add_captured(exported)
    storage.apply_final(FinalResult(ready.segment_id, "定稿甲", "定稿甲", "replay:cpu-int8"))
    storage.apply_final(
        FinalResult(exported.segment_id, "定稿乙", "定稿乙", "replay:cpu-int8")
    )

    # Reproduce the durable boundary inside rebuild_hour: the hourly file has
    # been atomically replaced and the DB row is EXPORTED, but audio unlink has
    # not happened yet.  Leave the other row at FINAL_READY.
    exporter = MarkdownExporter(storage, records)
    content, _count = exporter.render_hour("2026-07-12_17")
    _atomic_write(exporter.path_for_hour("2026-07-12_17"), content)
    storage.mark_exported(exported.segment_id)
    storage.close()

    with JournalStorage(database) as reopened:
        report = reopened.recover(spool)
        _check(
            report.completed_audio_deletion == (exported.segment_id,),
            "EXPORTED audio cleanup did not resume",
        )
        _check(
            reopened.get_segment(exported.segment_id).state == SegmentState.AUDIO_DELETED,
            "EXPORTED row did not reach AUDIO_DELETED",
        )
        _check(
            reopened.get_segment(ready.segment_id).state == SegmentState.FINAL_READY,
            "FINAL_READY row regressed during recovery",
        )
        exporter = MarkdownExporter(reopened, records)
        exporter.rebuild_dirty()
        _check(
            reopened.get_segment(ready.segment_id).state == SegmentState.AUDIO_DELETED,
            "FINAL_READY row did not export after restart",
        )
        _check(not tuple(spool.glob("*.flac")), "successful final audio was retained")
        entries = _verify_markdown(
            exporter,
            "2026-07-12_17",
            {ready.segment_id: "定稿甲", exported.segment_id: "定稿乙"},
        )
    return ScenarioResult("final-ready-exported", 2, entries)


SCENARIOS: tuple[tuple[str, Callable[[Path], ScenarioResult]], ...] = (
    ("flac-before-db", replay_flac_before_db),
    ("db-captured-finalizing", replay_db_pending_states),
    ("provisional-markdown", replay_provisional_markdown),
    ("final-ready-exported", replay_final_ready_and_exported),
)


def run_replay(root: Path) -> list[ScenarioResult]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    results: list[ScenarioResult] = []
    for name, scenario in SCENARIOS:
        scenario_root = root / name
        scenario_root.mkdir()
        results.append(scenario(scenario_root))
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        help="new directory in which replay artifacts are retained",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workdir is not None:
        root = args.workdir.expanduser().resolve()
        results = run_replay(root)
        payload = {"status": "passed", "root": str(root), "scenarios": results}
        print(json.dumps(payload, ensure_ascii=False, default=asdict, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="asj-fault-replay-") as temporary:
        root = Path(temporary) / "run"
        results = run_replay(root)
        payload = {"status": "passed", "root": None, "scenarios": results}
        print(json.dumps(payload, ensure_ascii=False, default=asdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
