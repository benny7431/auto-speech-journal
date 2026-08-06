"""Promote a validated v2 scene matrix into the runtime package reversibly."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_speech_journal.scene_assets import validate_runtime_scenes  # noqa: E402

DEFAULT_SOURCE = ROOT / "artifacts" / "today-river-production-v2" / "matrix"
DEFAULT_RUNTIME = ROOT / "src" / "auto_speech_journal" / "assets" / "scenes"
DEFAULT_BACKUP = (
    ROOT
    / "artifacts"
    / "today-river-production-v2"
    / "runtime-backup-v1"
    / "scenes"
)
Validator = Callable[..., list[str]]


def _require_valid(directory: Path, validator: Validator) -> None:
    errors = validator(strict=True, root=directory)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"scene matrix failed strict validation:\n{details}")


def promote_scene_matrix(
    source: Path = DEFAULT_SOURCE,
    runtime: Path = DEFAULT_RUNTIME,
    backup: Path = DEFAULT_BACKUP,
    *,
    validator: Validator = validate_runtime_scenes,
) -> Path:
    source = source.resolve()
    runtime = runtime.resolve()
    backup = backup.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if not runtime.is_dir():
        raise FileNotFoundError(runtime)
    if backup.exists():
        raise FileExistsError(f"refusing to overwrite runtime backup: {backup}")

    _require_valid(source, validator)
    token = uuid4().hex
    staging = runtime.parent / f".{runtime.name}-v2-staging-{token}"
    failed = backup.parent / f"failed-v2-{token}"
    shutil.copytree(source, staging)
    _require_valid(staging, validator)

    backup.parent.mkdir(parents=True, exist_ok=True)
    runtime.rename(backup)
    try:
        staging.rename(runtime)
        _require_valid(runtime, validator)
    except Exception:
        if runtime.exists():
            runtime.rename(failed)
        backup.rename(runtime)
        raise
    return backup


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    args = parser.parse_args(argv)
    try:
        backup = promote_scene_matrix(args.source, args.runtime, args.backup)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as error:
        print(f"Scene promotion failed: {error}")
        return 1
    print(f"Scene promotion passed; original runtime preserved at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
