from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from auto_speech_journal.model_download import FINAL_SPEC, PREVIEW_SPEC, VAD_SPEC

ROOT = Path(__file__).resolve().parents[1]


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None
    return match.group(0).lower().replace("_", "-")


def test_third_party_notices_cover_direct_dependencies_and_models() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    dependencies = list(project["project"]["dependencies"])
    for extra in ("cuda",):
        dependencies.extend(project["project"]["optional-dependencies"][extra])

    missing = [
        name
        for requirement in dependencies
        if (name := _requirement_name(requirement)) not in notices
    ]
    assert missing == []
    for model in (PREVIEW_SPEC, VAD_SPEC, FINAL_SPEC):
        assert model.install_path.lower() in notices


def test_client_environment_has_no_model_conversion_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "model-build" not in project["project"]["optional-dependencies"]
    requirements = list(project["project"]["dependencies"])
    for extra in project["project"]["optional-dependencies"].values():
        requirements.extend(extra)

    names = {_requirement_name(requirement) for requirement in requirements}
    assert names.isdisjoint({"torch", "transformers", "safetensors"})

    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert not any(
        re.search(rf'^name = "{re.escape(name)}"$', lock, flags=re.MULTILINE)
        for name in ("torch", "transformers", "safetensors")
    )

    downloader = (
        ROOT / "src" / "auto_speech_journal" / "model_download.py"
    ).read_text(encoding="utf-8")
    for conversion_api in ("snapshot_download", "ct2-transformers-converter", "safetensors"):
        assert conversion_api not in downloader


def test_public_docs_describe_hugging_face_only_runtime_model_supply() -> None:
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "GitHub Releases 與 Hugging Face" not in privacy
    assert "Python、PyTorch 或 NVIDIA" not in privacy
    assert "固定完整" in privacy
    assert "commit" in privacy
    assert "runtime-models-v1.json" in architecture
    assert "supply-chain authority" in architecture
    assert "pinned in `model_download.py`" not in architecture
    assert "預設關閉" in privacy
    assert "不會下載或安裝更新" in privacy


def test_tracked_sources_do_not_restore_github_models_release_dependencies() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    tracked = [Path(value) for value in result.stdout.decode().split("\0") if value]
    forbidden = (
        "gh release " + "download models-v1",
        "gh release " + "create models-v1",
        "/releases/" + "tags/models-v1",
        "/releases/" + "download/models-v1",
        "models-v1" + ".sha256",
        "packaging/manifests/" + "models-v1.json",
    )
    offenders: list[str] = []
    for relative in tracked:
        if relative.parts and relative.parts[0] == "tests":
            continue
        path = ROOT / relative
        if not path.is_file() or path.suffix.lower() not in {
            ".json",
            ".md",
            ".ps1",
            ".py",
            ".qml",
            ".toml",
            ".yml",
            ".yaml",
        }:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace").casefold()
        if any(pattern.casefold() in text for pattern in forbidden):
            offenders.append(relative.as_posix())

    assert offenders == []


def test_dependency_names_are_normalized_for_dependabot() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = list(project["project"]["dependencies"])
    for extra_dependencies in project["project"]["optional-dependencies"].values():
        dependencies.extend(extra_dependencies)

    non_normalized = []
    for requirement in dependencies:
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        assert match is not None
        if match.group(0) != _requirement_name(requirement):
            non_normalized.append(match.group(0))

    assert non_normalized == []


def test_repository_declares_line_ending_contract() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("*.py text eol=lf", "*.qml text eol=lf", "*.md text eol=lf"):
        assert pattern in attributes
    assert "*.ps1 text eol=crlf" in attributes


@pytest.mark.parametrize("name", ["install.ps1", "uninstall.ps1"])
def test_powershell_scripts_keep_utf8_bom_and_crlf(name: str) -> None:
    raw = (ROOT / name).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:]
    assert b"\r\n" in body
    assert b"\n" not in body.replace(b"\r\n", b"")


def test_public_repository_documents_and_templates_exist() -> None:
    expected = (
        "README.en.md",
        "PRIVACY.md",
        "THIRD_PARTY_NOTICES.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "docs/ARCHITECTURE.md",
        "docs/TROUBLESHOOTING.md",
        "docs/BUILDING.md",
        "docs/RELEASING.md",
        ".pre-commit-config.yaml",
        ".github/dependabot.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/release.yml",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/pull_request_template.md",
    )
    assert [path for path in expected if not (ROOT / path).is_file()] == []


def test_project_enforces_public_release_coverage_threshold() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["coverage"]["report"]["fail_under"] == 75


def test_release_workflow_excludes_uv_dist_gitignore() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert '$_.Extension -eq ".whl" -or $_.Name.EndsWith(".tar.gz")' in workflow
    assert '"default.gitignore"' in workflow
    assert "Expected one wheel and one source distribution" in workflow
