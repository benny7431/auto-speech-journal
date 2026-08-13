from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.ps1"
UNINSTALLER = ROOT / "uninstall.ps1"
APP_CONTROL = ROOT / "app-control.ps1"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("script", [INSTALLER, UNINSTALLER, APP_CONTROL])
def test_windows_powershell_scripts_are_ps51_utf8_bom_and_parseable(
    script: Path, assert_powershell_script
) -> None:
    assert_powershell_script(script, timeout=15)


def test_shared_process_helpers_are_defined_once_and_dot_sourced_by_both_scripts() -> None:
    shared = _source(APP_CONTROL)
    helpers = ("Test-AppMutex", "Get-AppProcessIds", "Wait-AppStopped")
    for helper in helpers:
        assert f"function {helper}" in shared

    for script in (INSTALLER, UNINSTALLER):
        source = _source(script)
        assert '. (Join-Path $PSScriptRoot "app-control.ps1")' in source
        # The helpers must live in exactly one place, otherwise the two copies
        # can drift apart again.
        for helper in helpers:
            assert f"function {helper}" not in source

    # The helpers read $MutexName and $AppRoot from the caller scope, so both
    # scripts must still define them before the dot-source runs.
    for script in (INSTALLER, UNINSTALLER):
        source = _source(script)
        dot_source = source.index('. (Join-Path $PSScriptRoot "app-control.ps1")')
        assert 0 < source.index("$MutexName =") < dot_source
        assert 0 < source.index("$AppRoot =") < dot_source


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


def test_source_installer_has_no_removed_scene_art_dependency() -> None:
    source = _source(INSTALLER)

    assert "scene_assets" not in source
    assert "assets\\scenes" not in source
    assert "離線場景" not in source


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
