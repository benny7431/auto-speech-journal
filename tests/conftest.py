from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Qt must be headless before any test module imports PySide6. conftest is imported
# first, so setting it here keeps the three UI test modules free of import-order noise.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")


@pytest.fixture
def paths(tmp_path: Path):
    """The runtime/records split used by every worker, CLI, and setup-wizard test.

    Named `paths` to match the local variable it replaces, so test bodies are unchanged.
    """
    from auto_speech_journal.paths import AppPaths

    return AppPaths(tmp_path / "runtime", tmp_path / "records")


@pytest.fixture
def assert_powershell_script():
    """Assert a .ps1 keeps its UTF-8 BOM and parses under Windows PowerShell 5.1.

    Lives here rather than in a sibling test module because windows-package.yml runs
    tests/test_windows_packaging_contract.py standalone.
    """
    import shutil
    import subprocess

    def _assert(script: Path, *, timeout: int = 20) -> None:
        assert script.read_bytes().startswith(b"\xef\xbb\xbf")
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            pytest.skip("Windows PowerShell is unavailable")
        command = (
            "$errors = $null; "
            f"[Management.Automation.Language.Parser]::ParseFile('{script}', "
            "[ref]$null, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        assert result.returncode == 0, result.stderr

    return _assert


@pytest.fixture
def storage(tmp_path: Path) -> Iterator:
    """An open JournalStorage on a throwaway database, always closed afterwards."""
    from auto_speech_journal.storage import JournalStorage

    value = JournalStorage(tmp_path / "state.db")
    yield value
    value.close()
