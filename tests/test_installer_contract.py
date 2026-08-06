from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.ps1"
UNINSTALLER = ROOT / "uninstall.ps1"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("script", [INSTALLER, UNINSTALLER])
def test_windows_powershell_scripts_are_ps51_utf8_bom_and_parseable(script: Path) -> None:
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
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_source_installer_keeps_transactional_rollback() -> None:
    source = _source(INSTALLER)

    for fragment in (
        "Assert-UnderRuntime $AppRoot",
        "$StageRoot",
        "$BackupRoot",
        "Move-Item -LiteralPath $StageRoot -Destination $AppRoot",
        "Remove-Item -LiteralPath $AppRoot -Recurse -Force",
        "Move-Item -LiteralPath $BackupRoot -Destination $AppRoot",
        "$ExistingTaskWasRunning",
        "$LegacyTaskRemoved",
    ):
        assert fragment in source


def test_source_installer_keeps_advanced_cuda_and_uses_official_model_command() -> None:
    source = _source(INSTALLER)

    assert '@("--extra", "cuda")' in source
    assert "-m auto_speech_journal download-models" in source
    assert "Hugging Face cache" in source
    assert '$SelfTestArguments += "--no-model-check"' in source
    assert '$SelfTestArguments += "--allow-cpu-finalizer"' in source
    assert "repair models" not in source
    assert "provision-progress" not in source
    assert ".part" not in source

    download = source.index("-m auto_speech_journal download-models")
    failure = source.index("else {", download)
    self_test = source.index("$SelfTestArguments", failure)
    assert "throw" not in source[failure:self_test]


def test_source_installer_defers_consent_and_microphone_to_first_launch() -> None:
    source = _source(INSTALLER)

    assert '"auto_speech_journal", "setup"' not in source
    assert "--non-interactive" not in source
    assert "--test-microphone" not in source
    assert '"--deep-model-check", "--no-microphone-check"' in source


def test_source_installer_does_not_create_a_second_startup_owner() -> None:
    source = _source(INSTALLER)

    assert "New-ScheduledTask" not in source
    assert "Start-Process" in source
    assert "登入自啟：未建立" in source
    assert "保留同名但不符合舊版" in source


def test_uninstaller_removes_only_owned_app_and_preserves_runtime_state() -> None:
    source = _source(UNINSTALLER)

    stop = source.index("Stop-ScheduledTask")
    wait = source.index("Wait-AppStopped", stop)
    unregister = source.index("Unregister-ScheduledTask", wait)
    remove_app = source.index("Remove-Item -LiteralPath $AppRoot", unregister)
    assert stop < wait < unregister < remove_app
    assert source.count("Remove-Item") == 1
    assert "Remove-Item -LiteralPath $RuntimeRoot" not in source
    assert 'Remove-Item -LiteralPath $AppRoot -Recurse -Force' in source
    assert "設定、設定歷程、資料庫、模型、暫存、日誌、本機字體與聲明" in source
