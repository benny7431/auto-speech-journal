from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from auto_speech_journal.model_download import FINAL_SPEC, PREVIEW_SPEC, VAD_SPEC

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = ROOT / "src" / "auto_speech_journal" / "runtime-models-v1.json"


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None
    return match.group(0).lower().replace("_", "-")


def test_third_party_notices_cover_direct_dependencies_and_models() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    dependencies = list(project["project"]["dependencies"])
    dependencies.extend(project["project"]["optional-dependencies"]["cuda"])

    missing = [
        name
        for requirement in dependencies
        if (name := _requirement_name(requirement)) not in notices
    ]
    assert missing == []
    for model in (PREVIEW_SPEC, VAD_SPEC, FINAL_SPEC):
        assert model.install_path.lower() in notices


def test_client_environment_uses_official_hub_without_conversion_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(project["project"]["dependencies"])
    for extra in project["project"]["optional-dependencies"].values():
        requirements.extend(extra)

    names = {_requirement_name(requirement) for requirement in requirements}
    assert "huggingface-hub" in names
    assert names.isdisjoint({"torch", "transformers", "safetensors"})
    assert "model-build" not in project["project"]["optional-dependencies"]

    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert not any(
        re.search(rf'^name = "{re.escape(name)}"$', lock, flags=re.MULTILINE)
        for name in ("torch", "transformers", "safetensors")
    )
    downloader = (
        ROOT / "src" / "auto_speech_journal" / "model_download.py"
    ).read_text(encoding="utf-8")
    assert "from huggingface_hub import hf_hub_download" in downloader
    for conversion_api in ("snapshot_download", "ct2-transformers-converter", "safetensors"):
        assert conversion_api not in downloader


def test_runtime_manifest_is_packaged_without_legacy_release_assets() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))

    assert "src/auto_speech_journal/runtime-models-v1.json" in wheel_include
    assert manifest["provider"] == "huggingface"
    assert len(manifest["models"]) == 3
    assert not (ROOT / ".github" / "workflows" / "models.yml").exists()
    assert not (ROOT / "packaging" / "manifests" / "runtime-models-v1.json").exists()
    assert not (ROOT / "packaging" / "manifests" / "cuda-runtime-v1.json").exists()


def test_tracked_sources_do_not_reference_a_github_models_release() -> None:
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


def test_public_docs_describe_direct_hugging_face_downloads() -> None:
    documents = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("README.md", "README.en.md", "docs/BUILDING.md", "docs/RELEASING.md")
    )

    assert "Hugging Face" in documents
    assert "runtime-models-v1.json" in documents
    assert "models-v1 Release" not in documents


def test_repository_baseline_contracts_remain_enabled() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert project["tool"]["coverage"]["report"]["fail_under"] == 75
    assert "*.py text eol=lf" in attributes
    assert "*.qml text eol=lf" in attributes
    assert "*.ps1 text eol=crlf" in attributes
    for path in (
        "PRIVACY.md",
        "SECURITY.md",
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/release.yml",
    ):
        assert (ROOT / path).is_file()


@pytest.mark.parametrize("name", ["install.ps1", "uninstall.ps1"])
def test_powershell_scripts_keep_utf8_bom_and_crlf(name: str) -> None:
    raw = (ROOT / name).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:]
    assert b"\r\n" in body
    assert b"\n" not in body.replace(b"\r\n", b"")
