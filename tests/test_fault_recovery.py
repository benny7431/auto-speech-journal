from __future__ import annotations

from pathlib import Path

from tools.replay_fault_recovery import SCENARIOS, run_replay


def test_all_crash_boundaries_replay_without_loss_or_duplicates(tmp_path: Path) -> None:
    results = run_replay(tmp_path / "fault-replay")

    assert [result.name for result in results] == [name for name, _scenario in SCENARIOS]
    assert sum(result.segment_count for result in results) == 6
    assert sum(result.markdown_entry_count for result in results) == 6
