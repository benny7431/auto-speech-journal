from __future__ import annotations

import base64
import json
import os
import shutil
import struct
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
        'Parameters: "repair models --manifest ""{app}\\manifests\\models-v1.json"""'
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
    assert "if RollbackCurrentManifest() then" in source
    assert "current.json could not be restored" in source
    assert 'MessagesFile: "languages\\ChineseTraditional.isl"' in source


def test_windows_package_e2e_waits_for_gui_installer_and_uninstaller() -> None:
    source = (ROOT / ".github/workflows/windows-package.yml").read_text(encoding="utf-8")
    assert "Start-Process -FilePath $setup" in source
    assert "Start-Process -FilePath $uninstaller" in source
    assert source.count("-WindowStyle Hidden -Wait -PassThru") == 2
    assert "Setup reported success but the stable CLI launcher is missing" in source
    assert "artifacts/windows/e2e/*.log" in source


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
    assert "startup enable" in source
    assert "Test-StableTaskXml" in source
    assert "$ResolvedCommand.Equals($StableGui" in source
    assert "Get-TaskXmlLines -TaskName $StableTaskName" in source
    assert "function Restore-LegacyTask" in source
    assert "startup disable" in source
    assert "Test-LegacyTaskXml -XmlLines $RestoredXml" in source
    assert "Migration marker activation completed, but verification failed" in source
    assert "if ($Migrated -and $LegacyTaskRemoved)" in source


def test_release_workflow_signs_inner_and_setup_separately_and_fails_closed() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert workflow.count("signpath/github-action-submit-signing-request@") == 2
    assert workflow.count("github-artifact-id:") == 2
    assert "SIGNPATH_PROGRAM_ARTIFACT_CONFIGURATION" in workflow
    assert "SIGNPATH_SETUP_ARTIFACT_CONFIGURATION" in workflow
    assert "Unsigned public releases are forbidden" in workflow
    assert "-ReleaseBuild" in workflow
    assert "--clobber" not in workflow
    assert "--draft" in workflow
    assert "attest-build-provenance" in workflow


def test_windows_resource_and_signature_verification_contracts_are_exact() -> None:
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

    assert "[string]$UnsignedSetupPath" in verify
    assert "Get-AuthenticodeNormalizedSha256" in verify
    assert "Signing changed Authenticode-covered PE content" in verify
    assert "Signed release verification requires -UnsignedSetupPath" in verify
    assert "X509NameType]::SimpleName" in verify
    assert "[StringComparison]::Ordinal" in verify
    assert 'TimestampReceipt["signed_at_utc"]' in verify
    assert "File version does not exactly match" in verify
    assert 'OriginalFilename = "AutoSpeechJournal.CLI.exe"' in verify
    assert 'InternalName = "AutoSpeechJournal.CLI"' in verify
    assert "authenticode_normalized_invariants" in verify
    assert "runtime_inventory.py" in verify
    assert "frozen-runtime-inventory.json" in verify
    assert "runtime_inventory_validation" in verify


def test_authenticode_normalization_ignores_only_pe_signature_fields(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")

    unsigned = bytearray(512)
    unsigned[0:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", unsigned, 0x3C, pe_offset)
    unsigned[pe_offset : pe_offset + 4] = b"PE\x00\x00"
    struct.pack_into("<H", unsigned, pe_offset + 20, 240)
    optional_offset = pe_offset + 24
    struct.pack_into("<H", unsigned, optional_offset, 0x20B)
    unsigned[0x1A0:0x1B0] = b"covered-content!"

    signed = bytearray(unsigned)
    struct.pack_into("<I", signed, optional_offset + 64, 0x12345678)
    security_directory = optional_offset + 112 + 32
    struct.pack_into("<II", signed, security_directory, len(signed), 16)
    signed.extend(struct.pack("<IHH", 16, 0x200, 2) + b"sigbytes")

    altered = bytearray(signed)
    altered[0x1A0] ^= 0xFF
    unsigned_path = tmp_path / "unsigned.exe"
    signed_path = tmp_path / "signed.exe"
    altered_path = tmp_path / "altered.exe"
    unsigned_path.write_bytes(unsigned)
    signed_path.write_bytes(signed)
    altered_path.write_bytes(altered)

    function_names = ("Assert-File", "Get-AuthenticodeNormalizedSha256")
    escaped_script = str(ROOT / "tools/verify_windows_installer.ps1").replace("'", "''")
    escaped_paths = [
        str(path).replace("'", "''")
        for path in (unsigned_path, signed_path, altered_path)
    ]
    command = (
        f"$ast=[Management.Automation.Language.Parser]::ParseFile('{escaped_script}',"
        "[ref]$null,[ref]$null);"
        f"foreach($name in @({','.join(repr(name) for name in function_names)})){{"
        "$fn=$ast.FindAll({param($node) $node -is "
        "[Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},"
        "$true)|Select-Object -First 1;Invoke-Expression $fn.Extent.Text};"
        + ";".join(
            f"Get-AuthenticodeNormalizedSha256 '{path}'" for path in escaped_paths
        )
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    digests = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(digests) == 3
    assert digests[0] == digests[1]
    assert digests[0] != digests[2]


def test_release_build_rejects_repository_model_placeholder(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(ROOT / "tools/build_windows_installer.ps1"),
            "-Stage",
            "Installer",
            "-OutputRoot",
            str(tmp_path / "output"),
            "-ReleaseBuild",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert "placeholders are forbidden" in output


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
