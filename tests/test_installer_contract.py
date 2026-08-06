from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.ps1"
UNINSTALLER = ROOT / "uninstall.ps1"
PYPROJECT = ROOT / "pyproject.toml"
GITIGNORE = ROOT / ".gitignore"
WHEEL_VERIFIER = ROOT / "tools" / "verify_wheel_contents.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("script", [INSTALLER, UNINSTALLER])
def test_windows_powershell_scripts_have_utf8_bom(script: Path) -> None:
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("script", [INSTALLER, UNINSTALLER])
def test_windows_powershell_51_parser_accepts_scripts(script: Path) -> None:
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


def test_installer_uses_transactional_runtime_and_task_rollback() -> None:
    source = _source(INSTALLER)

    assert 'Assert-UnderRuntime $AppRoot' in source
    assert '$ExistingTaskWasRunning' in source
    assert '$TaskRegistered = $true' in source
    assert 'if ($ExistingTaskWasRunning)' in source
    assert 'Unregister-ScheduledTask `' in source
    register_new = source.index("Register-ScheduledTask `")
    registration_confirmed = source.index("$TaskRegistered = $true", register_new)
    assert register_new < registration_confirmed

    stop_for_rollback = source.index("if ($TaskRegistered) {", source.index("finally"))
    wait_for_exit = source.index("-not (Wait-AppStopped", stop_for_rollback)
    unregister_new = source.index("Unregister-ScheduledTask", wait_for_exit)
    remove_new_app = source.index("Remove-Item -LiteralPath $AppRoot", unregister_new)
    restore_state = source.index("Copy-Item -LiteralPath $BackupStatePath", remove_new_app)
    restore_task = source.index("-Xml $ExistingTaskXml", restore_state)

    assert stop_for_rollback < wait_for_exit < unregister_new
    assert unregister_new < remove_new_app < restore_state < restore_task


def test_installer_task_contract_and_three_second_gate() -> None:
    source = _source(INSTALLER)

    required_fragments = (
        '$TaskPath = "\\"',
        "-TaskPath $TaskPath",
        '$Trigger.Delay = "PT20S"',
        "-AllowStartIfOnBatteries",
        "-DontStopIfGoingOnBatteries",
        "-RestartCount 999",
        "-MultipleInstances IgnoreNew",
        'Start-Sleep -Seconds 3',
        'if ($StartedTask.State -ne "Running")',
        'if (-not (Test-AppMutex))',
        '$LogSignatureAfter',
        '$SceneManifest.schema_version -ne 2',
        '$SceneManifest.assets.Count -ne 192',
        '$IncompleteScenes.Count -ne 0',
        '$LegacyPackagedFonts',
        'Assert-UnderRuntime $LegacyPackagedFonts',
        'Remove-Item -LiteralPath $LegacyPackagedFonts -Recurse -Force',
        '-m auto_speech_journal.scene_assets --strict',
        'throw "離線場景資產驗證失敗',
        '$ExistingAppProcessIds = @(Get-AppProcessIds)',
        'Wait-AppStopped -KnownProcessIds $ExistingAppProcessIds',
        '$RollbackAppProcessIds = @(Get-AppProcessIds)',
        'Wait-AppStopped -KnownProcessIds $RollbackAppProcessIds',
    )
    for fragment in required_fragments:
        assert fragment in source

    assert "$PrimaryFontManifestPath" not in source
    assert "$FallbackFontManifestPath" not in source
    assert "Test-PinnedAsset" not in source
    assert "Invoke-WebRequest" not in source
    assert "PersonalFont" not in source


def test_installer_defers_microphone_choice_to_first_app_launch() -> None:
    source = _source(INSTALLER)

    assert '"auto_speech_journal", "setup"' not in source
    assert "--non-interactive" not in source
    assert "--test-microphone" not in source
    assert "runtime-models-v1.json" in source
    assert "-m auto_speech_journal repair models" in source
    assert '"--deep-model-check", "--no-microphone-check"' in source


def test_model_download_failure_keeps_program_and_enables_later_repair() -> None:
    source = _source(INSTALLER)
    provision = source.index("-m auto_speech_journal repair models")
    failure_branch = source.index("if ($LASTEXITCODE -eq 0)", provision)
    self_test = source.index("$SelfTestArguments", failure_branch)

    failure_path = source[failure_branch:self_test]
    assert "throw" not in failure_path
    assert "程式安裝已保留" in failure_path
    assert ".part 續傳" in failure_path
    assert '$SelfTestArguments += "--no-model-check"' in source


def test_local_fonts_and_notices_are_excluded_from_distribution() -> None:
    installer = _source(INSTALLER)
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    gitignore = GITIGNORE.read_text(encoding="utf-8")
    verifier = WHEEL_VERIFIER.read_text(encoding="utf-8")

    for path in ("/字體/", "/聲明/", "/src/auto_speech_journal/assets/fonts/"):
        assert path in gitignore
    for path in (
        '"/字體/**"',
        '"/聲明/**"',
        '"/src/auto_speech_journal/assets/fonts/**"',
    ):
        assert path in pyproject

    assert '"auto_speech_journal/assets/fonts/"' in verifier
    assert '"字體/"' in verifier
    assert '"聲明/"' in verifier
    for packaged_asset in (
        "AmbientSoundRiver.qml",
        "TodayParticleLayer.qml",
        "TodayWorkspace.qml",
        "mist-mote.png",
        "glow-mote.png",
        "soft-ripple.png",
    ):
        assert packaged_asset in verifier
    assert "$PrimaryFontManifestPath" not in installer
    assert "$FallbackFontManifestPath" not in installer
    assert "Invoke-WebRequest" not in installer


def test_uninstaller_waits_for_process_and_preserves_runtime_state() -> None:
    source = _source(UNINSTALLER)

    stop = source.index("Stop-ScheduledTask")
    wait = source.index("Wait-AppStopped", stop)
    unregister = source.index("Unregister-ScheduledTask", wait)
    remove_app = source.index("Remove-Item -LiteralPath $AppRoot", unregister)
    assert stop < wait < unregister < remove_app
    assert "$ExistingAppProcessIds = @(Get-AppProcessIds)" in source
    assert "Wait-AppStopped -KnownProcessIds $ExistingAppProcessIds" in source

    assert source.count("Remove-Item") == 1
    assert "Remove-Item -LiteralPath $RuntimeRoot" not in source
    assert 'Remove-Item -LiteralPath $AppRoot -Recurse -Force' in source
    assert "設定、設定歷程、資料庫、模型、暫存、日誌、本機字體與聲明" in source
