from __future__ import annotations

import re
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
    for extra in ("cuda", "model-build"):
        dependencies.extend(project["project"]["optional-dependencies"][extra])

    missing = [
        name
        for requirement in dependencies
        if (name := _requirement_name(requirement)) not in notices
    ]
    assert missing == []
    for model in (PREVIEW_SPEC, VAD_SPEC, FINAL_SPEC):
        assert model.key.lower() in notices


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
