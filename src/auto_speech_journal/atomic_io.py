"""Crash-safe file replacement shared by config, export, and update-check writes.

This module is deliberately a leaf: it imports nothing from the package, so any
module may depend on it without creating an import cycle.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text`` via a unique sibling temporary file.

    The temporary name is hidden (dot-prefixed) and carries a UUID so concurrent
    writers never collide, and it is removed even when the write, the flush, or
    the replacement fails.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: Mapping[str, Any], *, ensure_ascii: bool) -> None:
    """Atomically store ``payload`` as two-space indented JSON with a final newline."""

    write_text_atomic(path, json.dumps(payload, ensure_ascii=ensure_ascii, indent=2) + "\n")


__all__ = ["write_json_atomic", "write_text_atomic"]
