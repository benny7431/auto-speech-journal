from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = ROOT / "packaging" / "windows"
BUILD_SCRIPT = ROOT / "tools" / "build_windows_installer.ps1"
INNO_SCRIPT = WINDOWS_PACKAGING / "AutoSpeechJournal.iss"
PYINSTALLER_SPEC = WINDOWS_PACKAGING / "AutoSpeechJournal.spec"


def _source(path: Path, *, bom: bool = False) -> str:
    return path.read_text(encoding="utf-8-sig" if bom else "utf-8")


def test_packaging_powershell_is_ps51_utf8_bom_and_parseable() -> None:
    assert BUILD_SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        "$errors=$null;"
        f"[Management.Automation.Language.Parser]::ParseFile('{BUILD_SCRIPT}',"
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


def test_inno_installs_direct_gui_per_user_with_bilingual_ui() -> None:
    source = _source(INNO_SCRIPT)

    assert "PrivilegesRequired=lowest" in source
    assert r"DefaultDirName={localappdata}\Programs\AutoSpeechJournal\app" in source
    assert r"UninstallFilesDir={localappdata}\Programs\AutoSpeechJournal\uninstall" in source
    assert 'Name: "english"; MessagesFile: "compiler:Default.isl"' in source
    assert r'Name: "chinesetraditional"; MessagesFile: "languages\ChineseTraditional.isl"' in source
    assert r'Source: "{#AppPayloadRoot}\*"; DestDir: "{app}"' in source
    assert r'Filename: "{app}\{#AppExeName}"' in source
    assert (
        r'Filename: "{app}\{#CliExeName}"; Parameters: "startup disable"' in source
    )
    assert "CloseApplications=yes" in source
    assert r"AppMutex=Local\AutoSpeechJournal" in source
    assert '[InstallDelete]' in source
    assert 'Type: filesandordirs; Name: "{app}"' in source

    for removed in (
        "\\versions\\",
        "current.json",
        "installer-probe",
        "request-shutdown",
        "provision_runner",
        "runtime-models-v1.json",
        "cuda-runtime-v1.json",
        "repair models",
        "repair gpu",
        "ProvisionProgress",
        "GpuDownloadBytes",
    ):
        assert removed not in source


def test_pyinstaller_builds_direct_gui_and_cli_with_hugging_face_runtime_data() -> None:
    source = _source(PYINSTALLER_SPEC)

    assert 'includes=["qml/**", "assets/**", "runtime-models-v1.json"]' in source
    assert 'excludes=["assets/fonts/**", "models/**"]' in source
    assert '"huggingface-hub"' in source
    assert '"huggingface_hub"' in source
    assert '"hf_xet"' in source
    assert 'name="AutoSpeechJournal"' in source
    assert "console=False" in source
    for forbidden_package in ('"nvidia"', '"safetensors"', '"torch"', '"transformers"'):
        assert forbidden_package in source

    assert "cli_entry.py" in source
    assert "cli_analysis" in source
    assert 'name="AutoSpeechJournal.CLI"' in source
    assert "MERGE(" in source
    assert "console=True" in source


def test_windows_build_has_direct_payload_and_no_installer_provisioning_stack() -> None:
    source = _source(BUILD_SCRIPT, bom=True)

    assert "ASJ_GUI_VERSION_FILE" in source
    assert "ASJ_CLI_VERSION_FILE" in source
    assert "AutoSpeechJournal.exe" in source
    assert "AutoSpeechJournal.CLI.exe" in source
    assert "runtime-models-v1.json" in source
    assert "AutoSpeechJournal-Setup-$Version-x64.exe" in source
    for removed in (
        "CLI_LAUNCHER",
        "LauncherPath",
        "CurrentManifest",
        "cuda-runtime-v1.json",
        "GpuDownloadBytes",
        "GpuInstalledBytes",
        "verify_windows_installer.ps1",
    ):
        assert removed not in source


def test_windows_package_e2e_installs_and_uninstalls_the_direct_gui() -> None:
    source = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(
        encoding="utf-8"
    )

    assert "Start-Process -FilePath $setup" in source
    assert "Start-Process -FilePath $uninstaller" in source
    assert "AutoSpeechJournal.exe" in source
    assert "AutoSpeechJournal.CLI.exe" in source
    assert "self-test" in source
    assert "artifacts/windows/e2e/" in source.replace("\\", "/")
    assert "installer-probe" not in source
    assert "repair models" not in source
    assert "current.json" not in source
    assert "cuda-runtime-v1.json" not in source


def test_release_is_one_unsigned_prerelease_job_with_checksums() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    windows = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(
        encoding="utf-8"
    )
    workflows = release + windows

    assert "signpath" not in workflows.casefold()
    assert "SIGNPATH_" not in workflows
    assert "signtool" not in workflows.casefold()
    assert "Get-AuthenticodeSignature" not in workflows
    jobs = release.split("jobs:", 1)[1]
    assert re.findall(r"^  ([A-Za-z][A-Za-z0-9_-]*):\s*$", jobs, flags=re.MULTILINE) == [
        "release"
    ]
    assert "gh release create" in release
    assert "--prerelease" in release
    assert "SHA256SUMS.txt" in release
    assert "--draft" not in release
    assert "attest-build-provenance" not in release
    assert "gh attestation verify" not in release
    assert "--clobber" not in release
    assert "Unknown publisher" in release
    assert "Microsoft Defender SmartScreen" in release
    assert "Do not disable Microsoft Defender" in release
    assert "commits/$env:GITHUB_SHA/check-runs" in release
    assert '"Windows / Python 3.11"' in release
    assert '"Python security-extended"' in release
    assert '"Unsigned installer contract"' in release


def test_removed_installer_only_components_stay_removed() -> None:
    removed = (
        WINDOWS_PACKAGING / "launcher" / "Program.cs",
        WINDOWS_PACKAGING / "migrate_legacy_task.ps1",
        WINDOWS_PACKAGING / "provision_runner.ps1",
        ROOT / "tools" / "verify_windows_installer.ps1",
        ROOT / "src" / "auto_speech_journal" / "gpu_runtime.py",
        ROOT / "src" / "auto_speech_journal" / "provisioning.py",
        ROOT / "src" / "auto_speech_journal" / "shutdown_ipc.py",
    )

    assert [path for path in removed if path.exists()] == []


def test_traditional_chinese_inno_translation_is_vendored_with_license() -> None:
    language_file = WINDOWS_PACKAGING / "languages" / "ChineseTraditional.isl"
    contents = language_file.read_bytes()

    assert b"6ef32198ef1f7b7b375cd4b6b90896c2a58eb4c2" in contents
    assert b"Inno Setup License" in contents
    assert b"this list of conditions without modification" in contents
    assert b"Modified for Auto Speech Journal" in contents
    assert b"LanguageID=$0404" in contents
    assert b"LanguageCodePage=950" in contents
