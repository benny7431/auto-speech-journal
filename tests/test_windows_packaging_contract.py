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


def test_windows_build_accepts_canonical_stable_and_prerelease_versions() -> None:
    source = _source(BUILD_SCRIPT, bom=True)
    version_match = re.search(r"\$Version -notmatch '([^']+)'", source)

    assert version_match is not None
    version_pattern = re.compile(version_match.group(1))
    for version in ("0.3.2", "0.4.0a1", "0.4.0b1", "0.4.0rc1", "0.4.0.dev1"):
        assert version_pattern.fullmatch(version)
    assert version_pattern.fullmatch("0.4.0-rc.1") is None


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


def test_release_is_one_unsigned_job_with_stable_and_prerelease_modes() -> None:
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
    assert '$releaseArgs = @(' in release
    assert '"release",' in release
    assert '"create",' in release
    stable_match = re.search(r"\$isStable = \$version -match '([^']+)'", release)
    assert stable_match is not None
    stable_pattern = re.compile(stable_match.group(1))
    assert stable_pattern.fullmatch("0.3.2")
    assert stable_pattern.fullmatch("0.4.0rc1") is None
    assert '$releaseArgs += "--latest"' in release
    assert '$releaseArgs += @("--prerelease", "--latest=false")' in release
    assert "gh @releaseArgs" in release
    assert "SHA256SUMS.txt" in release
    assert "--draft" not in release
    assert "attest-build-provenance" not in release
    assert "gh attestation verify" not in release
    assert "--clobber" not in release
    download_index = release.index('"## 下載 Windows 版"')
    changelog_index = release.index(") + $changelog[$start..($end - 1)] + @(")
    warning_index = release.index('"## Windows 安裝器提醒"')
    assert download_index < changelog_index < warning_index
    assert (
        "目前 Windows 執行檔未簽章；若出現 Unknown publisher／SmartScreen，請核對 "
        "SHA256SUMS.txt，無須關閉 Defender。" in release
    )
    assert "本預發行版包含每位使用者安裝的 Windows Setup" not in release
    assert "未來可以加入程式碼簽章" not in release
    assert "Important Windows installer notice" not in release
    assert "Do not disable Microsoft Defender" not in release
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
