from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.replay_fault_recovery import SCENARIOS, run_replay  # noqa: E402


def test_all_crash_boundaries_replay_without_loss_or_duplicates(tmp_path: Path) -> None:
    results = run_replay(tmp_path / "fault-replay")

    assert [result.name for result in results] == [name for name, _scenario in SCENARIOS]
    assert sum(result.segment_count for result in results) == 6
    assert sum(result.markdown_entry_count for result in results) == 6
