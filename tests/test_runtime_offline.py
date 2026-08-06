from __future__ import annotations

import ast
import inspect
from pathlib import Path

from auto_speech_journal.setup_wizard import run_setup

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_speech_journal"
NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "httpx",
        "huggingface_hub",
        "requests",
        "socket",
        "urllib",
    }
)
RUNTIME_MODULES = frozenset(
    {
        "audio.py",
        "config.py",
        "controller.py",
        "exporter.py",
        "finalizer_engine.py",
        "logging_setup.py",
        "paths.py",
        "preview_engine.py",
        "single_instance.py",
        "storage.py",
        "types.py",
        "ui.py",
        "vocabulary.py",
        "workers.py",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _network_imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        names: list[str]
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            root = name.split(".", 1)[0]
            if root in NETWORK_MODULES:
                imported.add(name)
    return imported


def _relative_imports(path: Path, module: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == module:
            imported.update(alias.name for alias in node.names)
    return imported


def test_network_capable_imports_are_confined_to_explicit_download_services() -> None:
    imports = {
        path.name: found
        for path in PACKAGE_ROOT.glob("*.py")
        if (found := _network_imports(path))
    }

    assert imports == {
        "gpu_runtime.py": {"urllib.parse"},
        "model_download.py": {"huggingface_hub", "urllib.request"},
        "provisioning.py": {"urllib.error", "urllib.parse", "urllib.request"},
        "update_check.py": {
            "urllib.error",
            "urllib.parse",
            "urllib.request",
        },
    }
    assert all(_network_imports(PACKAGE_ROOT / name) == set() for name in RUNTIME_MODULES)


def test_runtime_uses_only_offline_model_path_resolution() -> None:
    assert _relative_imports(PACKAGE_ROOT / "workers.py", "model_download") == {
        "resolve_model_paths"
    }
    assert _relative_imports(PACKAGE_ROOT / "setup_wizard.py", "model_download") == {
        "ensure_models"
    }
    assert _relative_imports(PACKAGE_ROOT / "workers.py", "provisioning") == set()
    assert _relative_imports(PACKAGE_ROOT / "workers.py", "update_check") == set()
    assert _relative_imports(PACKAGE_ROOT / "workers.py", "gpu_runtime") == set()
    assert inspect.signature(run_setup).parameters["download_models"].default is False


def test_run_entrypoint_never_runs_setup_or_model_download() -> None:
    tree = _tree(PACKAGE_ROOT / "cli.py")
    run_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_application"
    )
    setup_calls = [
        node
        for node in ast.walk(run_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_setup"
    ]

    assert setup_calls == []
