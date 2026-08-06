from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_ONNXRUNTIME_DLL_HANDLES: list[Any] = []


def register_onnxruntime_dll_directory() -> None:
    """Prefer the locked wheel's ORT over Windows' older System32 runtime."""

    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    with _LOCK:
        if _ONNXRUNTIME_DLL_HANDLES:
            return
        spec = importlib.util.find_spec("onnxruntime")
        if spec is None or spec.origin is None:
            raise RuntimeError("onnxruntime is required by sherpa-onnx on Windows")
        directory = Path(spec.origin).resolve().parent / "capi"
        runtime = directory / "onnxruntime.dll"
        if not runtime.is_file():
            raise RuntimeError(f"onnxruntime DLL is missing: {runtime}")
        _ONNXRUNTIME_DLL_HANDLES.append(os.add_dll_directory(str(directory)))


__all__ = ["register_onnxruntime_dll_directory"]
