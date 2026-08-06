from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = ROOT / "packaging" / "windows"
POWERSHELL_SCRIPTS = (
    ROOT / "tools" / "build_windows_installer.ps1",
    ROOT / "tools" / "verify_windows_installer.ps1",
    WINDOWS_PACKAGING / "provision_runner.ps1",
    WINDOWS_PACKAGING / "migrate_legacy_task.ps1",
)


def _csc() -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windir / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
        windir / "Microsoft.NET/Framework/v4.0.30319/csc.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS)
def test_packaging_powershell_is_ps51_utf8_bom_and_parseable(script: Path) -> None:
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        "$errors=$null;"
        f"[Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$null,[ref]$errors)|Out-Null;"
        "if($errors.Count){$errors|%{Write-Error $_};exit 1}"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_inno_contract_is_per_user_transactional_and_data_preserving() -> None:
    source = (WINDOWS_PACKAGING / "AutoSpeechJournal.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in source
    assert r"DefaultDirName={localappdata}\Programs\AutoSpeechJournal" in source
    assert r'DestDir: "{app}\versions\{#AppVersion}"' in source
    assert "installer-probe --isolated" in source
    assert "ActivateInstalledVersion" in source
    assert "current.previous.json" in source
    assert "request-shutdown --timeout 30" in source
    assert "VersionedCliFromManifest" in source
    assert r"{app}\current.previous.json" in source
    assert "CheckForMutexes('Local\\AutoSpeechJournal')" in source
    assert "ProvisionProgressBar" in source
    assert "eta_seconds" in source
    assert "ProvisionCancel" in source
    assert "GpuInstalledBytes" in source
    assert "MigrateLegacyInstall" in source
    assert 'Parameters: "startup disable"' in source
    assert 'Name: "startup"' not in source
    assert r"DelTree(AddBackslash(RuntimeRoot) + 'models'" in source
    assert "Markdown journal folders are not deleted" in source
    assert "UninstallSilent" in source
    assert "ShutdownError := RequestGracefulShutdown()" in source
    assert "if ShutdownError <> '' then" in source
    assert "Optional runtime data was preserved because the application is running" in source
    assert 'Name: "{group}\\Repair runtime"' not in source
    assert "AutoSpeechJournal-Maintenance.exe" in source
    assert (
        'Parameters: "repair models --manifest ""{app}\\manifests\\runtime-models-v1.json"""'
        in source
    )
    assert 'Type: filesandordirs; Name: "{app}\\versions"' in source
    assert "CleanupOldVersions" in source
    assert "ExistingTargetBytes" in source
    assert "ExistingVersionBytes" not in source
    assert "MaintenanceSetupBytes" in source
    assert "ProvisionProcessId := ErrorCode" in source
    assert "ProcessId := ProvisionProcessId" in source
    assert "SetTimer(0, 0, 250, CreateCallback(@ProvisionTimerTick))" in source
    assert "KillTimer(0, ProvisionTimer)" in source
    assert "TTimer" not in source
    assert "WizardIsTaskSelected('gpu') and not CmdLineParamExists('NOGPU')" in source
    assert "RollbackCurrentManifest" not in source
    assert "The verified new version remains active" in source
    assert "manual_start_migration_helper_failed" in source
    assert 'MessagesFile: "languages\\ChineseTraditional.isl"' in source


def test_windows_package_e2e_waits_for_gui_installer_and_uninstaller() -> None:
    source = (ROOT / ".github/workflows/windows-package.yml").read_text(encoding="utf-8")
    assert "Start-Process -FilePath $setup" in source
    assert "Start-Process -FilePath $uninstaller" in source
    assert source.count("-WindowStyle Hidden -Wait -PassThru") == 2
    assert "Setup reported success but the stable CLI launcher is missing" in source
    assert "artifacts/windows/e2e/" in source


def test_windows_package_e2e_exercises_real_pinned_hugging_face_repair() -> None:
    source = (ROOT / ".github/workflows/windows-package.yml").read_text(encoding="utf-8")

    assert "huggingface.co" in source
    assert "packaging/manifests/runtime-models-v1.json" in source
    assert "resolve/$($vad.revision)" in source
    assert "repair models --manifest" in source
    assert ".part" in source
    assert "silero_vad.onnx" in source
    assert "corrupt" in source.lower()


def test_traditional_chinese_inno_translation_is_vendored_with_license() -> None:
    language_file = WINDOWS_PACKAGING / "languages" / "ChineseTraditional.isl"
    contents = language_file.read_bytes()
    assert b"6ef32198ef1f7b7b375cd4b6b90896c2a58eb4c2" in contents
    assert b"Inno Setup License" in contents
    assert b"this list of conditions without modification" in contents
    assert b"Modified for Auto Speech Journal" in contents
    assert b"LanguageID=$0404" in contents
    assert b"LanguageCodePage=950" in contents


def test_legacy_task_migration_requires_exact_owned_action() -> None:
    source = (WINDOWS_PACKAGING / "migrate_legacy_task.ps1").read_text(encoding="utf-8-sig")
    assert '$LegacyTaskName = "\\Auto Speech Journal"' in source
    assert '$StableTaskName = "\\AutoSpeechJournal\\Auto Speech Journal"' in source
    assert '".venv\\Scripts\\pythonw.exe"' in source
    assert "ResolvedCommand.Equals($LegacyPythonw" in source
    assert "auto_speech_journal\\s+run" in source
    assert '"foreign_task_preserved"' in source
    assert "legacy_app_retained = $true" in source
    assert '"startup", "enable"' in source
    assert "Test-StableTaskXml" in source
    assert "Test-StableTaskEnabledXml" in source
    assert "$ResolvedCommand.Equals($StableGui" in source
    assert "Get-TaskQuery -TaskName $StableTaskName" in source
    assert "Get-TaskQuery -TaskName $LegacyTaskName" in source
    assert "$LegacyBeforeDelete = Get-TaskQuery" in source
    assert "Test-LegacyTaskXml -XmlLines $LegacyBeforeDelete.lines" in source
    assert "Migration marker activation completed, but verification failed" in source
    assert '"manual_start_scheduler_unavailable"' in source
    assert '"legacy_task_delete_failed_preserved"' in source
    assert "Invoke-NativeCommand" in source
    assert '$ErrorActionPreference = "Continue"' in source


def _write_cmd(path: Path, source: str) -> None:
    path.write_bytes(source.strip().replace("\n", "\r\n").encode("ascii") + b"\r\n")


def _run_legacy_migration(
    tmp_path: Path,
    *,
    schtasks_source: str,
    stable_cli_source: str = "@echo off\nexit /b 99",
) -> tuple[subprocess.CompletedProcess[str], dict[str, object], str, Path]:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    legacy_root = tmp_path / "legacy-app"
    legacy_python = legacy_root / ".venv/Scripts/pythonw.exe"
    legacy_python.parent.mkdir(parents=True)
    legacy_python.write_bytes(b"")
    stable_cli = tmp_path / "AutoSpeechJournal.CLI.cmd"
    stable_gui = tmp_path / "AutoSpeechJournal.exe"
    stable_gui.write_bytes(b"")
    _write_cmd(stable_cli, stable_cli_source)
    schtasks = tmp_path / "schtasks.cmd"
    _write_cmd(schtasks, schtasks_source)
    marker = tmp_path / "legacy-migration.json"
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_PACKAGING / "migrate_legacy_task.ps1"),
            "-LegacyAppRoot",
            str(legacy_root),
            "-StableCli",
            str(stable_cli),
            "-MarkerPath",
            str(marker),
            "-SchtasksPath",
            str(schtasks),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    payload = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    calls = (tmp_path / "schtasks-calls.txt").read_text(encoding="ascii")
    return result, payload, calls, stable_cli


def test_legacy_task_migration_treats_missing_task_as_normal(tmp_path: Path) -> None:
    calls = tmp_path / "schtasks-calls.txt"
    result, marker, invocation_log, _stable_cli = _run_legacy_migration(
        tmp_path,
        schtasks_source=rf"""
@echo off
echo %*>>"{calls}"
if /I "%~2"=="/FO" exit /b 0
exit /b 1
""",
    )

    assert result.returncode == 0, result.stderr
    assert marker["legacy_task_status"] == "no_task"
    assert marker["legacy_app_retained"] is True
    assert marker["manual_start_required"] is False
    assert "/Delete" not in invocation_log


def test_legacy_task_migration_degrades_when_scheduler_is_unavailable(tmp_path: Path) -> None:
    calls = tmp_path / "schtasks-calls.txt"
    result, marker, invocation_log, _stable_cli = _run_legacy_migration(
        tmp_path,
        schtasks_source=rf"""
@echo off
echo %*>>"{calls}"
echo scheduler unavailable 1>&2
exit /b 5
""",
    )

    assert result.returncode == 0, result.stderr
    assert marker["legacy_task_status"] == "manual_start_scheduler_unavailable"
    assert marker["legacy_task_retained"] is True
    assert marker["manual_start_required"] is True
    assert "/Delete" not in invocation_log


def test_legacy_task_migration_never_touches_foreign_task(tmp_path: Path) -> None:
    calls = tmp_path / "schtasks-calls.txt"
    foreign_xml = tmp_path / "foreign-task.xml"
    foreign_xml.write_text(
        "<Task><RegistrationInfo><Description>foreign</Description>"
        "<URI>\\Auto Speech Journal</URI></RegistrationInfo>"
        "<Actions><Exec><Command>C:\\Windows\\System32\\notepad.exe</Command>"
        "<Arguments /></Exec></Actions></Task>",
        encoding="utf-8",
    )
    result, marker, invocation_log, stable_cli = _run_legacy_migration(
        tmp_path,
        schtasks_source=rf"""
@echo off
echo %*>>"{calls}"
if /I "%~3"=="\Auto Speech Journal" (
  type "{foreign_xml}"
  exit /b 0
)
if /I "%~2"=="/FO" exit /b 0
exit /b 1
""",
    )

    assert result.returncode == 0, result.stderr
    assert marker["legacy_task_status"] == "foreign_task_preserved"
    assert marker["legacy_task_retained"] is True
    assert "/Delete" not in invocation_log
    assert not stable_cli.with_name("stable-cli-calls.txt").exists()


def test_legacy_task_enable_failure_preserves_owned_legacy_task(tmp_path: Path) -> None:
    calls = tmp_path / "schtasks-calls.txt"
    stable_calls = tmp_path / "stable-cli-calls.txt"
    legacy_python = tmp_path / "legacy-app/.venv/Scripts/pythonw.exe"
    owned_xml = tmp_path / "owned-legacy-task.xml"
    owned_xml.write_text(
        "<Task><RegistrationInfo><Description>legacy</Description>"
        "<URI>\\Auto Speech Journal</URI></RegistrationInfo>"
        f"<Actions><Exec><Command>{legacy_python}</Command>"
        "<Arguments>-m auto_speech_journal run</Arguments></Exec></Actions></Task>",
        encoding="utf-8",
    )
    result, marker, invocation_log, _stable_cli = _run_legacy_migration(
        tmp_path,
        schtasks_source=rf"""
@echo off
echo %*>>"{calls}"
if /I "%~3"=="\Auto Speech Journal" (
  type "{owned_xml}"
  exit /b 0
)
if /I "%~2"=="/FO" exit /b 0
exit /b 1
""",
        stable_cli_source=rf"""
@echo off
echo %*>>"{stable_calls}"
exit /b 9
""",
    )

    assert result.returncode == 0, result.stderr
    assert marker["legacy_task_status"] == "manual_start_enable_failed"
    assert marker["legacy_task_retained"] is True
    assert marker["manual_start_required"] is True
    assert "startup enable" in stable_calls.read_text(encoding="ascii")
    assert "/Delete" not in invocation_log


def test_release_workflow_allows_unsigned_setup_and_preserves_integrity_gates() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    windows_package = (ROOT / ".github/workflows/windows-package.yml").read_text(
        encoding="utf-8"
    )
    packaging_workflows = workflow + windows_package

    assert "signpath" not in packaging_workflows.lower()
    assert "SIGNPATH_" not in packaging_workflows
    assert "secrets." not in workflow
    assert "AllowUnsigned" not in packaging_workflows
    assert "ExpectedPublisher" not in packaging_workflows
    assert "UnsignedAppPayloadPath" not in packaging_workflows
    assert "UnsignedLauncherPath" not in packaging_workflows
    assert "UnsignedSetupPath" not in packaging_workflows
    assert "Build unsigned Windows release package" in workflow
    assert "Install unsigned Setup" in workflow
    assert "Windows / Python 3.11" in workflow
    assert "CodeQL" in workflow
    assert "Python security-extended" in workflow
    assert "Unsigned installer contract" in workflow
    assert "-ReleaseBuild" in workflow
    assert "--clobber" not in workflow
    assert "--draft" in workflow
    assert "SHA256SUMS.txt" in workflow
    assert "AutoSpeechJournal.cdx.json" in workflow
    assert "attest-build-provenance" in workflow
    assert "gh attestation verify" in workflow
    assert "Start-Process -FilePath $setup" in windows_package
    assert "Start-Process -FilePath $uninstaller" in windows_package


def test_unsigned_release_notice_is_explicit_without_disabling_defender() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (ROOT / "README.en.md").read_text(encoding="utf-8")

    assert "Unknown publisher" in workflow
    assert "Microsoft Defender SmartScreen" in workflow
    assert "Do not disable Microsoft Defender" in workflow
    assert "未知的發行者" in readme_zh
    assert "Microsoft Defender SmartScreen" in readme_zh
    assert "SHA-256" in readme_zh
    assert "artifact attestation" in readme_zh
    assert "Unknown publisher" in readme_en
    assert "Microsoft Defender SmartScreen" in readme_en
    assert "SHA-256" in readme_en
    assert "artifact attestation" in readme_en
    for forbidden in (
        "turn off Windows Defender",
        "disable Windows Defender before",
        "關閉 Windows Defender",
        "停用 Windows Defender 後",
    ):
        assert forbidden not in workflow
        assert forbidden not in readme_zh
        assert forbidden not in readme_en


def test_release_workflow_uses_hugging_face_manifest_without_models_release() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    models = (ROOT / ".github/workflows/models.yml").read_text(encoding="utf-8")

    assert "validate_runtime_model_manifest.py" in release
    assert "verify_runtime_models.py" in release
    assert "runtime-models-v1.json" in release
    assert "gh release download models-v1" not in release
    assert "models-v1.sha256" not in release
    assert "build_model_bundle.py" not in models
    assert "runtime-models-v1.json" in models
    assert "workflow_dispatch" in models
    assert "verify_runtime_models.py" in models


def test_windows_resource_and_unsigned_verification_contracts_are_exact() -> None:
    build = (ROOT / "tools/build_windows_installer.ps1").read_text(encoding="utf-8-sig")
    verify = (ROOT / "tools/verify_windows_installer.ps1").read_text(
        encoding="utf-8-sig"
    )
    inno = (WINDOWS_PACKAGING / "AutoSpeechJournal.iss").read_text(encoding="utf-8")

    assert "ASJ_GUI_VERSION_FILE" in build
    assert "ASJ_CLI_VERSION_FILE" in build
    assert '"AutoSpeechJournal.CLI.exe" "Auto Speech Journal CLI"' in build
    assert 'StringStruct(\'InternalName\', \'$InternalName\')' in build
    assert 'StringStruct(\'OriginalFilename\', \'$ExecutableName\')' in build
    assert 'PinnedInnoVersion = "6.7.3"' in build
    assert "Get-InnoCompilerVersion $Inno" in build
    assert "Release builds require Inno Setup" in build
    assert '"/DAppNumericVersion=$NumericVersion"' in build
    assert "#ifndef AppNumericVersion" in inno
    assert "VersionInfoVersion={#AppNumericVersion}" in inno

    assert "[switch]$AllowUnsigned" not in verify
    assert "ExpectedPublisher" not in verify
    assert "UnsignedAppPayloadPath" not in verify
    assert "UnsignedLauncherPath" not in verify
    assert "UnsignedSetupPath" not in verify
    assert 'if ($Signature.Status -eq "NotSigned")' in verify
    assert 'authenticode_policy = "optional_unsigned_allowed"' in verify
    assert "Artifact contains an invalid Authenticode signature" in verify
    assert "X509NameType]::SimpleName" in verify
    assert "File version does not exactly match" in verify
    assert 'OriginalFilename = "AutoSpeechJournal.CLI.exe"' in verify
    assert 'InternalName = "AutoSpeechJournal.CLI"' in verify
    assert "authenticode_normalized_invariants" not in verify
    assert "payload_tree_sha256" in verify
    assert "launcher_tree_sha256" in verify
    assert "runtime_inventory.py" in verify
    assert "frozen-runtime-inventory.json" in verify
    assert "runtime_inventory_validation" in verify


def test_authenticode_receipt_accepts_unsigned_executable(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    compiler = _csc()
    if powershell is None or compiler is None:
        pytest.skip("Windows PowerShell or .NET Framework csc.exe is unavailable")

    source = tmp_path / "Program.cs"
    executable = tmp_path / "unsigned.exe"
    source.write_text("internal static class Program { static void Main() {} }", encoding="ascii")
    compiled = subprocess.run(
        [str(compiler), "/nologo", f"/out:{executable}", str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    function_names = ("Assert-File", "Get-CertificateReceipt", "Get-AuthenticodeReceipt")
    escaped_script = str(ROOT / "tools/verify_windows_installer.ps1").replace("'", "''")
    escaped_executable = str(executable).replace("'", "''")
    command = (
        f"$ast=[Management.Automation.Language.Parser]::ParseFile('{escaped_script}',"
        "[ref]$null,[ref]$null);"
        f"foreach($name in @({','.join(repr(name) for name in function_names)})){{"
        "$fn=$ast.FindAll({param($node) $node -is "
        "[Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},"
        "$true)|Select-Object -First 1;Invoke-Expression $fn.Extent.Text};"
        f"Get-AuthenticodeReceipt '{escaped_executable}'|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {
        "status": "NotSigned",
        "present": False,
        "signer": None,
        "timestamp": None,
    }


def test_release_build_validates_canonical_runtime_model_manifest() -> None:
    source = (ROOT / "tools/build_windows_installer.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'packaging\\manifests\\runtime-models-v1.json"' in source
    assert "validate_runtime_model_manifest.py" in source
    assert "models-v1.sha256" not in source
    assert "MODELS_V1_MANIFEST_NOT_PUBLISHED" not in source


def test_native_cli_launcher_preserves_windows_argv_and_working_directory(
    tmp_path: Path,
) -> None:
    compiler = _csc()
    if compiler is None:
        pytest.skip(".NET Framework csc.exe is unavailable")

    launcher = tmp_path / "AutoSpeechJournal.CLI.exe"
    compile_launcher = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            "/define:CLI_LAUNCHER",
            f"/out:{launcher}",
            "/reference:System.Runtime.Serialization.dll",
            str(WINDOWS_PACKAGING / "launcher/Program.cs"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compile_launcher.returncode == 0, compile_launcher.stdout + compile_launcher.stderr

    target_dir = tmp_path / "versions/0.2.0"
    target_dir.mkdir(parents=True)
    target = target_dir / "AutoSpeechJournal.CLI.exe"
    echo_source = tmp_path / "ArgEcho.cs"
    echo_source.write_text(
        """
using System;
using System.IO;
using System.Text;
internal static class ArgEcho {
  public static int Main(string[] args) {
    using (var output = new StreamWriter(
        Environment.GetEnvironmentVariable("ASJ_ARGV_CAPTURE"),
        false,
        new UTF8Encoding(false))) {
      output.WriteLine(Convert.ToBase64String(
          Encoding.UTF8.GetBytes(Environment.CurrentDirectory)));
      foreach (string arg in args) {
        output.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(arg)));
      }
    }
    return 17;
  }
}
""",
        encoding="utf-8",
    )
    compile_target = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", f"/out:{target}", str(echo_source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compile_target.returncode == 0, compile_target.stdout + compile_target.stderr
    (tmp_path / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.2.0",
                "targets": {
                    "gui": "AutoSpeechJournal.exe",
                    "cli": "AutoSpeechJournal.CLI.exe",
                },
            }
        ),
        encoding="utf-8",
    )

    capture = tmp_path / "argv.txt"
    working_directory = tmp_path / "working directory"
    working_directory.mkdir()
    arguments = ["", "a b", 'embedded"quote', "trailing slash\\", 'x \\\" y', "中文"]
    environment = os.environ.copy()
    environment["ASJ_ARGV_CAPTURE"] = str(capture)
    result = subprocess.run(
        [str(launcher), *arguments],
        cwd=working_directory,
        env=environment,
        check=False,
        timeout=30,
    )
    assert result.returncode == 17
    captured = [base64.b64decode(line).decode() for line in capture.read_text().splitlines()]
    assert Path(captured[0]) == working_directory
    assert captured[1:] == arguments

    (tmp_path / "current.json").replace(tmp_path / "current.previous.json")
    (tmp_path / "current.json").write_text("not json", encoding="utf-8")
    capture.unlink()
    fallback_arguments = ["fallback", "after interrupted manifest switch"]
    fallback = subprocess.run(
        [str(launcher), *fallback_arguments],
        cwd=working_directory,
        env=environment,
        check=False,
        timeout=30,
    )
    assert fallback.returncode == 17
    fallback_capture = [
        base64.b64decode(line).decode() for line in capture.read_text().splitlines()
    ]
    assert Path(fallback_capture[0]) == working_directory
    assert fallback_capture[1:] == fallback_arguments
