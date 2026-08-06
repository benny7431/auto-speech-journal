"""Validate the installer CUDA manifest against the locked Windows wheels."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

EXPECTED_PACKAGES = frozenset(
    {
        "nvidia-cublas-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cudnn-cu12",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DRIVER_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")


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


def _locked_cuda_roots(packages: Mapping[str, Mapping[str, Any]]) -> set[str]:
    project = packages.get("auto-speech-journal")
    if project is None:
        raise ValueError("uv.lock does not contain the auto-speech-journal project")
    optional = _mapping(project.get("optional-dependencies", {}), label="optional-dependencies")
    cuda = _list(optional.get("cuda"), label="optional-dependencies.cuda")
    return {
        canonical_name(str(_mapping(item, label="CUDA dependency").get("name", "")))
        for item in cuda
    }


def _windows_wheel(package: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    wheels = _list(package.get("wheels"), label=f"{name}.wheels")
    candidates = []
    for raw in wheels:
        wheel = _mapping(raw, label=f"{name} wheel")
        filename = PurePosixPath(urlparse(str(wheel.get("url", ""))).path).name.casefold()
        if filename.endswith("-win_amd64.whl"):
            candidates.append(wheel)
    if len(candidates) != 1:
        raise ValueError(f"{name} must have exactly one win_amd64 wheel in uv.lock")
    return candidates[0]


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def validate_cuda_manifest(manifest_path: Path, lock_path: Path) -> dict[str, object]:
    manifest = _mapping(
        json.loads(manifest_path.read_text(encoding="utf-8")),
        label="CUDA manifest",
    )
    lock = _mapping(tomllib.loads(lock_path.read_text(encoding="utf-8")), label="uv.lock")
    packages = _lock_packages(lock)

    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("CUDA manifest schema_version must be 1")
    if manifest.get("release") != "cuda-runtime-v1":
        raise ValueError("CUDA manifest release must be 'cuda-runtime-v1'")
    minimum_driver = str(manifest.get("min_driver_version", ""))
    if not _DRIVER_VERSION.fullmatch(minimum_driver):
        raise ValueError("CUDA manifest min_driver_version is invalid")

    cuda_roots = _locked_cuda_roots(packages)
    if cuda_roots != EXPECTED_PACKAGES:
        raise ValueError(
            "uv.lock CUDA extra must contain exactly the three approved NVIDIA packages"
        )

    expected_assets: dict[str, dict[str, object]] = {}
    for name in sorted(EXPECTED_PACKAGES):
        package = packages.get(name)
        if package is None:
            raise ValueError(f"uv.lock is missing CUDA package {name!r}")
        version = str(package.get("version", ""))
        if not version:
            raise ValueError(f"uv.lock CUDA package {name!r} has no version")
        wheel = _windows_wheel(package, name)
        url = str(wheel.get("url", ""))
        filename = PurePosixPath(urlparse(url).path).name
        digest = str(wheel.get("hash", "")).casefold().removeprefix("sha256:")
        if not _SHA256.fullmatch(digest) or digest == "0" * 64:
            raise ValueError(f"uv.lock CUDA wheel {filename!r} has an invalid SHA-256")
        expected_prefix = name.replace("-", "_") + "-" + version + "-"
        if not filename.casefold().startswith(expected_prefix.casefold()):
            raise ValueError(f"CUDA wheel filename does not encode locked {name} {version}")
        expected_assets[filename] = {
            "package": name,
            "version": version,
            "url": url,
            "sha256": digest,
            "size": _positive_int(wheel.get("size"), label=f"{filename}.size"),
        }

    raw_assets = _list(manifest.get("assets"), label="CUDA manifest assets")
    assets: dict[str, Mapping[str, Any]] = {}
    for raw in raw_assets:
        asset = _mapping(raw, label="CUDA manifest asset")
        filename = str(asset.get("name", ""))
        if not filename or filename in assets:
            raise ValueError("CUDA manifest has a missing or duplicate asset name")
        assets[filename] = asset
    if set(assets) != set(expected_assets):
        raise ValueError("CUDA manifest assets do not match the three locked Windows wheels")

    for filename, expected in expected_assets.items():
        asset = assets[filename]
        for field in ("url", "sha256", "size"):
            actual = asset.get(field)
            if field == "sha256":
                actual = str(actual).casefold()
            if field == "size":
                actual = _positive_int(actual, label=f"{filename}.size")
            if actual != expected[field]:
                raise ValueError(f"CUDA manifest {filename!r} {field} differs from uv.lock")
        if asset.get("archive") != "zip" or asset.get("destination") != "gpu-runtime":
            raise ValueError(f"CUDA manifest {filename!r} has an unsafe install contract")
        _positive_int(asset.get("installed_size"), label=f"{filename}.installed_size")

    return {
        "schema_version": 1,
        "release": "cuda-runtime-v1",
        "min_driver_version": minimum_driver,
        "packages": [expected_assets[name] for name in sorted(expected_assets)],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    args = parser.parse_args(argv)
    result = validate_cuda_manifest(args.manifest.resolve(), args.lock.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
