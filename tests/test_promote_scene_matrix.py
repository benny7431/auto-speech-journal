from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.promote_scene_matrix import promote_scene_matrix


def _validator(*, strict: bool, root: Path) -> list[str]:
    assert strict is True
    if not (root / "manifest.json").is_file():
        return ["manifest missing"]
    if (root / "invalid").exists():
        return ["invalid marker"]
    return []


def test_promote_scene_matrix_preserves_original_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "package" / "scenes"
    backup = tmp_path / "artifacts" / "runtime-backup-v1" / "scenes"
    source.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (source / "manifest.json").write_text("v2", encoding="utf-8")
    (source / "01-listening-workspace.webp").write_bytes(b"new")
    (runtime / "manifest.json").write_text("v1", encoding="utf-8")
    (runtime / "01-listening.webp").write_bytes(b"old")

    assert promote_scene_matrix(source, runtime, backup, validator=_validator) == backup

    assert (runtime / "manifest.json").read_text(encoding="utf-8") == "v2"
    assert (runtime / "01-listening-workspace.webp").read_bytes() == b"new"
    assert (backup / "manifest.json").read_text(encoding="utf-8") == "v1"
    assert (backup / "01-listening.webp").read_bytes() == b"old"


def test_promote_scene_matrix_refuses_invalid_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    backup = tmp_path / "backup"
    source.mkdir()
    runtime.mkdir()
    (source / "manifest.json").write_text("v2", encoding="utf-8")
    (source / "invalid").touch()
    (runtime / "manifest.json").write_text("v1", encoding="utf-8")

    with pytest.raises(ValueError, match="strict validation"):
        promote_scene_matrix(source, runtime, backup, validator=_validator)

    assert runtime.is_dir()
    assert not backup.exists()


def test_promote_scene_matrix_never_overwrites_existing_backup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    backup = tmp_path / "backup"
    for directory in (source, runtime, backup):
        directory.mkdir()
        (directory / "manifest.json").write_text("data", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        promote_scene_matrix(source, runtime, backup, validator=_validator)


def test_promote_scene_matrix_rolls_back_a_failed_post_swap_gate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "package" / "scenes"
    backup = tmp_path / "artifacts" / "runtime-backup-v1" / "scenes"
    source.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (source / "manifest.json").write_text("v2", encoding="utf-8")
    (source / "new").touch()
    (runtime / "manifest.json").write_text("v1", encoding="utf-8")
    (runtime / "old").touch()

    def fail_after_swap(*, strict: bool, root: Path) -> list[str]:
        assert strict is True
        if root == runtime.resolve() and (root / "new").exists():
            return ["post-swap failure"]
        return []

    with pytest.raises(ValueError, match="post-swap failure"):
        promote_scene_matrix(source, runtime, backup, validator=fail_after_swap)

    assert (runtime / "old").is_file()
    assert not (runtime / "new").exists()
    assert not backup.exists()
    failed = tuple(backup.parent.glob("failed-v2-*"))
    assert len(failed) == 1
    assert (failed[0] / "new").is_file()
