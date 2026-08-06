"""Create and validate the frozen runtime inventory and CycloneDX SBOM."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import tomllib
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.markers import Marker

PROJECT_NAME = "auto-speech-journal"
FORBIDDEN_EXTRAS = ("cuda", "dev", "model-build")
FORBIDDEN_BUILD_PACKAGES = frozenset(
    {
        "altgraph",
        "coverage",
        "pillow",
        "pre-commit",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "pytest",
        "pytest-cov",
        "pytest-qt",
        "pywin32-ctypes",
        "ruff",
        "safetensors",
        "torch",
        "transformers",
    }
)
FORBIDDEN_PAYLOAD_PACKAGES = frozenset({"nvidia", "safetensors", "torch", "transformers"})
FORBIDDEN_MODEL_SUFFIXES = frozenset({".onnx", ".pt", ".pth", ".safetensors"})
NVIDIA_DLL_PREFIXES = ("cublas", "cudnn", "nvcuda", "nvjitlink", "nvrtc")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _marker_environment(*, extra: str = "") -> dict[str, str]:
    return {
        "implementation_name": "cpython",
        "implementation_version": "3.11.0",
        "os_name": "nt",
        "platform_machine": "AMD64",
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Windows",
        "platform_version": "",
        "python_full_version": "3.11.0",
        "python_version": "3.11",
        "sys_platform": "win32",
        "extra": extra,
    }


def _marker_matches(dependency: Mapping[str, Any], *, extra: str = "") -> bool:
    marker = dependency.get("marker")
    if marker is None:
        return True
    return Marker(str(marker)).evaluate(_marker_environment(extra=extra))


def _lock_packages(lock: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw in _list(lock.get("package"), label="uv.lock package"):
        package = _mapping(raw, label="uv.lock package entry")
        name = canonical_name(str(package.get("name", "")))
        if not name:
            raise ValueError("uv.lock contains a package without a name")
        if name in result:
            raise ValueError(f"uv.lock contains multiple package records for {name!r}")
        result[name] = package
    return result


def _dependency_names(raw: object, *, extra: str = "") -> set[str]:
    dependencies = _list(raw, label="dependency list")
    result = set()
    for item in dependencies:
        dependency = _mapping(item, label="dependency")
        if _marker_matches(dependency, extra=extra):
            name = canonical_name(str(dependency.get("name", "")))
            if not name:
                raise ValueError("dependency has no name")
            result.add(name)
    return result


def _closure(
    roots: Iterable[str],
    packages: Mapping[str, Mapping[str, Any]],
    *,
    extra: str = "",
) -> set[str]:
    pending = deque(canonical_name(name) for name in roots)
    result: set[str] = set()
    while pending:
        name = pending.popleft()
        if name in result:
            continue
        package = packages.get(name)
        if package is None:
            raise ValueError(f"uv.lock is missing dependency package {name!r}")
        result.add(name)
        for dependency in _dependency_names(package.get("dependencies", []), extra=extra):
            if dependency not in result:
                pending.append(dependency)
    return result


def locked_runtime_inventory(
    lock: Mapping[str, Any],
) -> tuple[dict[str, str], set[str], set[str]]:
    packages = _lock_packages(lock)
    project = packages.get(PROJECT_NAME)
    if project is None:
        raise ValueError(f"uv.lock does not contain {PROJECT_NAME!r}")
    direct = _dependency_names(project.get("dependencies", []))
    runtime = _closure(direct, packages)

    optional = _mapping(project.get("optional-dependencies", {}), label="optional-dependencies")
    forbidden: set[str] = set(FORBIDDEN_BUILD_PACKAGES)
    for extra in FORBIDDEN_EXTRAS:
        roots = _dependency_names(optional.get(extra, []), extra=extra)
        forbidden.update(_closure(roots, packages, extra=extra) - runtime)
    forbidden.update(name for name in packages if name.startswith("nvidia-"))

    versions = {name: str(packages[name].get("version", "")) for name in runtime}
    if any(not version for version in versions.values()):
        raise ValueError("uv.lock runtime package is missing a version")
    return versions, direct, forbidden


def _top_level_module(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    return normalized.split("/", 1)[0].split(".", 1)[0]


def _validate_release_entries(entries: Iterable[str], *, label: str) -> list[str]:
    normalized_entries = sorted({str(value).replace("\\", "/") for value in entries})
    if not normalized_entries:
        raise ValueError(f"{label} is empty")
    for entry in normalized_entries:
        path = PurePosixPath(entry)
        if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
            raise ValueError(f"{label} contains an unsafe path: {entry!r}")
        lowered_parts = tuple(part.casefold() for part in path.parts)
        top_level = _top_level_module(entry).casefold()
        leaked_package = next(
            (
                package
                for package in FORBIDDEN_PAYLOAD_PACKAGES
                if top_level == package
                or any(
                    part == package
                    or part.startswith(f"{package}-")
                    or part.startswith(f"{package}_")
                    for part in lowered_parts
                )
            ),
            None,
        )
        if leaked_package is not None:
            raise ValueError(f"{label} contains forbidden package {leaked_package!r}: {entry!r}")
        filename = lowered_parts[-1]
        if PurePosixPath(filename).suffix in FORBIDDEN_MODEL_SUFFIXES or filename in {
            "model.bin",
            "pytorch_model.bin",
        }:
            raise ValueError(f"{label} contains a bundled model artifact: {entry!r}")
        if filename.endswith(".dll") and filename.startswith(NVIDIA_DLL_PREFIXES):
            raise ValueError(f"{label} contains an NVIDIA runtime DLL: {entry!r}")
    return normalized_entries


def write_frozen_inventory(
    module_names: Iterable[str],
    destination: Path,
    *,
    project_name: str,
    project_version: str,
) -> dict[str, object]:
    """Write the distribution inventory represented by PyInstaller Analysis entries."""

    analysis_entries = _validate_release_entries(module_names, label="PyInstaller Analysis")
    modules = sorted(
        {
            module
            for value in analysis_entries
            if (module := _top_level_module(str(value))) and not module.startswith("_")
        }
    )
    package_map = importlib.metadata.packages_distributions()
    distributions: dict[str, str] = {}
    for module in modules:
        for distribution in package_map.get(module, ()):
            name = canonical_name(distribution)
            version = importlib.metadata.version(distribution)
            previous = distributions.setdefault(name, version)
            if previous != version:
                raise ValueError(f"frozen distribution {name!r} has conflicting versions")

    payload = {
        "schema_version": 1,
        "project": {
            "name": canonical_name(project_name),
            "version": project_version,
        },
        "analysis_entries": analysis_entries,
        "modules": modules,
        "distributions": [
            {"name": name, "version": version}
            for name, version in sorted(distributions.items())
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _component_versions(components: object, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in _list(components, label=label):
        component = _mapping(raw, label=f"{label} entry")
        name = canonical_name(str(component.get("name", "")))
        version = str(component.get("version", ""))
        if not name or not version:
            raise ValueError(f"{label} contains a component without name/version")
        previous = result.setdefault(name, version)
        if previous != version:
            raise ValueError(f"{label} has conflicting versions for {name!r}")
    return result


def validate_runtime_inventory(
    sbom_path: Path,
    inventory_path: Path,
    lock_path: Path,
    pyproject_path: Path,
    payload_path: Path | None = None,
) -> dict[str, object]:
    sbom = _mapping(json.loads(sbom_path.read_text(encoding="utf-8")), label="SBOM")
    inventory = _mapping(
        json.loads(inventory_path.read_text(encoding="utf-8")), label="frozen inventory"
    )
    lock = _mapping(tomllib.loads(lock_path.read_text(encoding="utf-8")), label="uv.lock")
    pyproject = _mapping(
        tomllib.loads(pyproject_path.read_text(encoding="utf-8")), label="pyproject.toml"
    )
    project = _mapping(pyproject.get("project"), label="pyproject project")
    project_name = canonical_name(str(project.get("name", "")))
    project_version = str(project.get("version", ""))
    if project_name != PROJECT_NAME or not project_version:
        raise ValueError("pyproject.toml has an unexpected project name or version")

    if sbom.get("bomFormat") != "CycloneDX" or str(sbom.get("specVersion")) != "1.5":
        raise ValueError("SBOM must be CycloneDX 1.5")
    metadata = _mapping(sbom.get("metadata"), label="SBOM metadata")
    root = _mapping(metadata.get("component"), label="SBOM root component")
    if canonical_name(str(root.get("name", ""))) != project_name:
        raise ValueError("SBOM root component name does not match pyproject.toml")
    if str(root.get("version", "")) != project_version:
        raise ValueError("SBOM root component version does not match pyproject.toml")

    runtime_versions, direct_runtime, forbidden = locked_runtime_inventory(lock)
    sbom_versions = _component_versions(sbom.get("components"), label="SBOM components")
    if set(sbom_versions) != set(runtime_versions):
        missing = sorted(set(runtime_versions) - set(sbom_versions))
        extra = sorted(set(sbom_versions) - set(runtime_versions))
        raise ValueError(f"SBOM runtime closure mismatch: missing={missing}, extra={extra}")
    for name, expected in runtime_versions.items():
        if sbom_versions[name] != expected:
            raise ValueError(
                f"SBOM version for {name!r} is {sbom_versions[name]!r}, expected {expected!r}"
            )

    if int(inventory.get("schema_version", 0)) != 1:
        raise ValueError("frozen inventory schema_version must be 1")
    inventory_project = _mapping(inventory.get("project"), label="frozen inventory project")
    if canonical_name(str(inventory_project.get("name", ""))) != project_name:
        raise ValueError("frozen inventory project name does not match pyproject.toml")
    if str(inventory_project.get("version", "")) != project_version:
        raise ValueError("frozen inventory project version does not match pyproject.toml")
    analysis_entries = _validate_release_entries(
        _list(inventory.get("analysis_entries"), label="frozen analysis_entries"),
        label="frozen PyInstaller Analysis",
    )
    frozen_versions = _component_versions(
        inventory.get("distributions"), label="frozen distributions"
    )
    frozen_versions.pop(project_name, None)
    unknown_frozen = set(frozen_versions) - set(runtime_versions)
    if unknown_frozen:
        raise ValueError(f"frozen payload contains non-runtime packages: {sorted(unknown_frozen)}")
    missing_direct = direct_runtime - set(frozen_versions)
    if missing_direct:
        raise ValueError(
            f"frozen payload is missing direct runtime packages: {sorted(missing_direct)}"
        )
    for name, version in frozen_versions.items():
        if runtime_versions[name] != version:
            raise ValueError(
                f"frozen version for {name!r} is {version!r}, expected {runtime_versions[name]!r}"
            )

    prohibited = (set(sbom_versions) | set(frozen_versions)) & forbidden
    if prohibited:
        raise ValueError(
            f"build/CUDA/model-only packages leaked into release: {sorted(prohibited)}"
        )

    payload_file_count = None
    if payload_path is not None:
        if not payload_path.is_dir():
            raise ValueError(f"frozen payload directory is missing: {payload_path}")
        for required_executable in ("AutoSpeechJournal.exe", "AutoSpeechJournal.CLI.exe"):
            if not (payload_path / required_executable).is_file():
                raise ValueError(f"frozen payload is missing {required_executable}")
        payload_entries = [
            path.relative_to(payload_path).as_posix()
            for path in payload_path.rglob("*")
            if path.is_file()
        ]
        payload_file_count = len(
            _validate_release_entries(payload_entries, label="frozen payload files")
        )

    return {
        "schema_version": 1,
        "project": {"name": project_name, "version": project_version},
        "sbom_component_count": len(sbom_versions),
        "frozen_distribution_count": len(frozen_versions),
        "analysis_entry_count": len(analysis_entries),
        "payload_file_count": payload_file_count,
        "direct_runtime": sorted(direct_runtime),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--sbom", required=True, type=Path)
    validate.add_argument("--inventory", required=True, type=Path)
    validate.add_argument("--lock", required=True, type=Path)
    validate.add_argument("--pyproject", required=True, type=Path)
    validate.add_argument("--payload", type=Path)
    args = parser.parse_args(argv)
    result = validate_runtime_inventory(
        args.sbom.resolve(),
        args.inventory.resolve(),
        args.lock.resolve(),
        args.pyproject.resolve(),
        args.payload.resolve() if args.payload else None,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
