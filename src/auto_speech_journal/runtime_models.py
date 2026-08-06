from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from .provisioning import (
    ProgressCallback,
    ProvisionEvent,
    ProvisioningError,
    ProvisionResult,
    download_resumable,
    find_manifest,
    verify_file,
)
from .single_instance import NamedMutex

RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION = 1
RUNTIME_MODEL_MANIFEST_FILENAME = "runtime-models-v1.json"
RUNTIME_MODEL_MARKER = ".asj-runtime-model.json"
_LEGACY_DOWNLOAD_RELEASE = "models-v1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_HF_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MINIMUM_DISK_HEADROOM = 16 * 1024 * 1024


class RuntimeModelManifestError(ProvisioningError):
    pass


def _reject_unknown(
    value: Mapping[str, object],
    allowed: set[str],
    *,
    context: str,
) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise RuntimeModelManifestError(f"{context} has unexpected fields: {unexpected}")


@dataclass(frozen=True, slots=True)
class RuntimeModelLicense:
    spdx: str
    url: str


@dataclass(frozen=True, slots=True)
class RuntimeModelSource:
    url: str
    description: str


@dataclass(frozen=True, slots=True)
class RuntimeModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    name: str
    repository: str
    revision: str
    format: str
    destination: str
    license: RuntimeModelLicense
    source: RuntimeModelSource
    files: tuple[RuntimeModelFile, ...]

    @property
    def installed_size(self) -> int:
        return sum(file.size for file in self.files)


@dataclass(frozen=True, slots=True)
class RuntimeModelManifest:
    schema_version: int
    release: str
    provider: str
    models: tuple[RuntimeModel, ...]
    source_path: Path | None = None

    @property
    def download_size(self) -> int:
        return sum(model.installed_size for model in self.models)

    @property
    def installed_size(self) -> int:
        return self.download_size


RuntimeModelDownloader = Callable[
    [RuntimeModel, RuntimeModelFile, Path, Callable[[int, int], None]], None
]
DiskUsage = Callable[[Path], shutil._ntuple_diskusage]
Clock = Callable[[], float]


def _relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeModelManifestError(f"{field} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or normalized != path.as_posix()
        or any(not _SAFE_NAME.fullmatch(part) for part in path.parts)
    ):
        raise RuntimeModelManifestError(f"{field} must stay inside the model root")
    return path.as_posix()


def _paths_overlap(paths: list[str]) -> bool:
    split_paths = [PurePosixPath(path).parts for path in paths]
    return any(
        left == right[: len(left)] or right == left[: len(right)]
        for index, left in enumerate(split_paths)
        for right in split_paths[index + 1 :]
    )


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeModelManifestError(f"{field} must be a positive integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise RuntimeModelManifestError(f"{field} must be a positive integer") from error
    if parsed <= 0:
        raise RuntimeModelManifestError(f"{field} must be a positive integer")
    return parsed


def _https_url(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise RuntimeModelManifestError(f"{field} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeModelManifestError(f"{field} must be an HTTPS URL without credentials")
    return value


def _load_license(value: object, *, model_name: str) -> RuntimeModelLicense:
    if not isinstance(value, Mapping):
        raise RuntimeModelManifestError(f"model {model_name!r} must declare license metadata")
    _reject_unknown(value, {"spdx", "url"}, context=f"{model_name}.license")
    spdx = value.get("spdx")
    if not isinstance(spdx, str) or not spdx.strip():
        raise RuntimeModelManifestError(f"model {model_name!r} has no SPDX license identifier")
    return RuntimeModelLicense(
        spdx=spdx.strip(),
        url=_https_url(value.get("url"), field=f"{model_name}.license.url"),
    )


def _load_source(value: object, *, model_name: str) -> RuntimeModelSource:
    if not isinstance(value, Mapping):
        raise RuntimeModelManifestError(f"model {model_name!r} must declare source metadata")
    _reject_unknown(value, {"url", "description"}, context=f"{model_name}.source")
    description = value.get("description")
    if not isinstance(description, str) or not description.strip():
        raise RuntimeModelManifestError(f"model {model_name!r} has no source description")
    return RuntimeModelSource(
        url=_https_url(value.get("url"), field=f"{model_name}.source.url"),
        description=description.strip(),
    )


def _load_file(value: object, *, model_name: str) -> RuntimeModelFile:
    if not isinstance(value, Mapping):
        raise RuntimeModelManifestError(f"model {model_name!r} has an invalid file entry")
    _reject_unknown(value, {"path", "size", "sha256"}, context=f"{model_name}.files")
    path = _relative_path(value.get("path"), field=f"{model_name}.files.path")
    digest = str(value.get("sha256", "")).lower()
    if not _SHA256.fullmatch(digest) or set(digest) == {"0"}:
        raise RuntimeModelManifestError(f"model file {path!r} has an invalid SHA-256")
    return RuntimeModelFile(
        path=path,
        size=_positive_int(value.get("size"), field=f"{model_name}.{path}.size"),
        sha256=digest,
    )


def _load_model(value: object) -> RuntimeModel:
    if not isinstance(value, Mapping):
        raise RuntimeModelManifestError("each runtime model must be an object")
    _reject_unknown(
        value,
        {
            "name",
            "repository",
            "revision",
            "format",
            "destination",
            "license",
            "source",
            "files",
        },
        context="runtime model",
    )
    name = value.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise RuntimeModelManifestError(f"invalid runtime model name: {name!r}")
    repository = value.get("repository")
    if not isinstance(repository, str) or not _HF_REPOSITORY.fullmatch(repository):
        raise RuntimeModelManifestError(f"model {name!r} has an invalid Hugging Face repository")
    revision = str(value.get("revision", "")).lower()
    if not _COMMIT_REVISION.fullmatch(revision):
        raise RuntimeModelManifestError(
            f"model {name!r} revision must be a full 40-character commit hash"
        )
    model_format = value.get("format")
    if not isinstance(model_format, str) or not _SAFE_NAME.fullmatch(model_format):
        raise RuntimeModelManifestError(f"model {name!r} has an invalid runtime format")
    destination = _relative_path(value.get("destination"), field=f"{name}.destination")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeModelManifestError(f"model {name!r} must list runtime files")
    files = tuple(_load_file(file, model_name=name) for file in raw_files)
    paths = [file.path.casefold() for file in files]
    if len(paths) != len(set(paths)):
        raise RuntimeModelManifestError(f"model {name!r} contains duplicate file paths")
    if _paths_overlap(paths):
        raise RuntimeModelManifestError(f"model {name!r} contains overlapping file paths")
    return RuntimeModel(
        name=name,
        repository=repository,
        revision=revision,
        format=model_format,
        destination=destination,
        license=_load_license(value.get("license"), model_name=name),
        source=_load_source(value.get("source"), model_name=name),
        files=files,
    )


def load_runtime_model_manifest(path: Path) -> RuntimeModelManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeModelManifestError(f"unable to read runtime model manifest {path}") from error
    if not isinstance(raw, Mapping):
        raise RuntimeModelManifestError("runtime model manifest root must be an object")
    _reject_unknown(
        raw,
        {"schema_version", "release", "provider", "models"},
        context="runtime model manifest",
    )
    if raw.get("schema_version") != RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION:
        raise RuntimeModelManifestError(
            "unsupported runtime model manifest schema; expected "
            f"{RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION}"
        )
    release = raw.get("release")
    if (
        not isinstance(release, str)
        or not _SAFE_NAME.fullmatch(release)
        or not release.startswith("runtime-models-v")
    ):
        raise RuntimeModelManifestError("runtime model release must be versioned")
    if raw.get("provider") != "huggingface":
        raise RuntimeModelManifestError("runtime model provider must be huggingface")
    raw_models = raw.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise RuntimeModelManifestError("runtime model manifest must contain models")
    models = tuple(_load_model(model) for model in raw_models)
    names = [model.name.casefold() for model in models]
    destinations = [model.destination.casefold() for model in models]
    if len(names) != len(set(names)):
        raise RuntimeModelManifestError("runtime model names must be unique")
    if len(destinations) != len(set(destinations)):
        raise RuntimeModelManifestError("runtime model destinations must be unique")
    if _paths_overlap(destinations):
        raise RuntimeModelManifestError("runtime model destinations must not overlap")
    return RuntimeModelManifest(
        schema_version=RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION,
        release=release,
        provider="huggingface",
        models=models,
        source_path=path,
    )


def huggingface_download_url(model: RuntimeModel, file: RuntimeModelFile) -> str:
    encoded_path = quote(file.path, safe="/")
    return f"https://huggingface.co/{model.repository}/resolve/{model.revision}/{encoded_path}"


def _marker_payload(manifest: RuntimeModelManifest, model: RuntimeModel) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION,
        "release": manifest.release,
        "provider": manifest.provider,
        "name": model.name,
        "repository": model.repository,
        "revision": model.revision,
        "format": model.format,
        "license": asdict(model.license),
        "source": asdict(model.source),
        "files": [asdict(file) for file in model.files],
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _model_path(root: Path, model: RuntimeModel) -> Path:
    return root.joinpath(*PurePosixPath(model.destination).parts)


def _file_path(root: Path, file: RuntimeModelFile) -> Path:
    return root.joinpath(*PurePosixPath(file.path).parts)


def verify_runtime_model(
    manifest: RuntimeModelManifest,
    model: RuntimeModel,
    destination_root: Path,
) -> None:
    destination = _model_path(destination_root, model)
    if not destination.is_dir():
        raise ProvisioningError(f"runtime model directory is missing: {destination}")
    expected = {file.path for file in model.files}
    actual = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and path.name != RUNTIME_MODEL_MARKER
    }
    if actual != expected:
        raise ProvisioningError(
            f"runtime model inventory mismatch for {model.name}: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    for file in model.files:
        verify_file(_file_path(destination, file), size=file.size, sha256=file.sha256)
    marker_path = destination / RUNTIME_MODEL_MARKER
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProvisioningError(f"runtime model marker is invalid: {marker_path}") from error
    if marker != _marker_payload(manifest, model):
        raise ProvisioningError(f"runtime model marker does not match {model.name}")


def verify_installed_runtime_models(
    manifest: RuntimeModelManifest,
    destination_root: Path,
) -> None:
    for model in manifest.models:
        verify_runtime_model(manifest, model, destination_root)


def runtime_model_is_installed(
    manifest: RuntimeModelManifest,
    model: RuntimeModel,
    destination_root: Path,
) -> bool:
    try:
        verify_runtime_model(manifest, model, destination_root)
    except (OSError, ValueError, ProvisioningError):
        return False
    return True


def _part_path(group_root: Path, file: RuntimeModelFile) -> Path:
    final = _file_path(group_root, file)
    return final.with_name(f"{final.name}.part")


def _clean_download_group(group_root: Path, model: RuntimeModel) -> None:
    if not group_root.is_dir():
        return
    allowed = {RUNTIME_MODEL_MARKER}
    for file in model.files:
        final = _file_path(group_root, file)
        allowed.add(final.relative_to(group_root).as_posix())
        allowed.add(_part_path(group_root, file).relative_to(group_root).as_posix())
    for path in sorted(group_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and path.relative_to(group_root).as_posix() not in allowed:
            path.unlink()
        elif path.is_dir():
            with suppress(OSError):
                path.rmdir()


def _file_is_valid(path: Path, file: RuntimeModelFile) -> bool:
    try:
        verify_file(path, size=file.size, sha256=file.sha256)
    except (OSError, ProvisioningError):
        return False
    return True


def _seed_from_installed(source: Path, destination: Path, file: RuntimeModelFile) -> bool:
    if not _file_is_valid(source, file):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        return False
    return True


def _default_downloader(
    model: RuntimeModel,
    file: RuntimeModelFile,
    destination: Path,
    progress: Callable[[int, int], None],
) -> None:
    download_resumable(
        huggingface_download_url(model, file),
        destination,
        expected_size=file.size,
        progress=progress,
    )


def _remaining_download_bytes(
    manifest: RuntimeModelManifest,
    destination_root: Path,
    *,
    installed_status: Mapping[str, bool] | None = None,
) -> int:
    downloads_root = destination_root / ".downloads" / manifest.release
    remaining = 0
    for model in manifest.models:
        installed = (
            installed_status.get(model.name, False)
            if installed_status is not None
            else runtime_model_is_installed(manifest, model, destination_root)
        )
        if installed:
            continue
        group_root = _model_path(downloads_root, model)
        for file in model.files:
            final = _file_path(group_root, file)
            if _file_is_valid(final, file):
                continue
            part = _part_path(group_root, file)
            current = part.stat().st_size if part.is_file() else 0
            if current == file.size and not _file_is_valid(part, file):
                current = 0
            remaining += file.size - current if 0 <= current <= file.size else file.size
    return remaining


def calculate_runtime_model_disk_bytes(
    manifest: RuntimeModelManifest,
    destination_root: Path,
    *,
    installed_status: Mapping[str, bool] | None = None,
) -> int:
    remaining = _remaining_download_bytes(
        manifest,
        destination_root,
        installed_status=installed_status,
    )
    return max(_MINIMUM_DISK_HEADROOM, (remaining * 120 + 99) // 100)


def _atomic_install_directory(
    source: Path,
    destination: Path,
    *,
    verify: Callable[[], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(f"{destination.name}.old-{uuid.uuid4().hex}")
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, previous)
    try:
        os.replace(source, destination)
        verify()
    except Exception:
        if destination.exists():
            try:
                os.replace(destination, source)
            except OSError:
                if destination.is_dir():
                    shutil.rmtree(destination, ignore_errors=True)
                else:
                    destination.unlink(missing_ok=True)
        if had_previous and previous.exists():
            os.replace(previous, destination)
        raise
    if previous.is_dir():
        shutil.rmtree(previous, ignore_errors=True)
    else:
        previous.unlink(missing_ok=True)


def _remove_swap_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _swap_backups(destination: Path) -> list[Path]:
    return sorted(
        destination.parent.glob(f"{destination.name}.old-*"),
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )


def _cleanup_swap_backups(destination: Path) -> None:
    for backup in _swap_backups(destination):
        _remove_swap_path(backup)


def _recover_interrupted_swap(
    manifest: RuntimeModelManifest,
    model: RuntimeModel,
    destination_root: Path,
    downloads_root: Path,
) -> None:
    destination = _model_path(destination_root, model)
    backups = _swap_backups(destination)
    if destination.exists():
        if runtime_model_is_installed(manifest, model, destination_root):
            _cleanup_swap_backups(destination)
        return

    staged = _model_path(downloads_root, model)
    if staged.is_dir():
        try:
            verify_runtime_model(manifest, model, downloads_root)
            os.replace(staged, destination)
            verify_runtime_model(manifest, model, destination_root)
        except (OSError, ProvisioningError):
            if destination.exists() and not staged.exists():
                with suppress(OSError):
                    os.replace(destination, staged)
        else:
            _cleanup_swap_backups(destination)
            return

    if backups:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backups[0], destination)
        for backup in backups[1:]:
            _remove_swap_path(backup)


def _runtime_model_lock(destination_root: Path) -> NamedMutex:
    normalized = os.path.normcase(str(destination_root.resolve())).encode("utf-8")
    identity = hashlib.sha256(normalized).hexdigest()[:24]
    return NamedMutex(rf"Local\AutoSpeechJournal.RuntimeModels.{identity}")


def _remove_legacy_download_cache(destination_root: Path) -> None:
    legacy = destination_root / ".downloads" / _LEGACY_DOWNLOAD_RELEASE
    if legacy.is_dir():
        shutil.rmtree(legacy, ignore_errors=True)


def _provision_runtime_models_locked(
    manifest: RuntimeModelManifest,
    destination_root: Path,
    *,
    progress: ProgressCallback | None = None,
    downloader: RuntimeModelDownloader = _default_downloader,
    disk_usage: DiskUsage = shutil.disk_usage,
    clock: Clock = time.monotonic,
) -> ProvisionResult:
    destination_root.mkdir(parents=True, exist_ok=True)
    _remove_legacy_download_cache(destination_root)
    downloads_root = destination_root / ".downloads" / manifest.release
    downloads_root.mkdir(parents=True, exist_ok=True)
    for model in manifest.models:
        _recover_interrupted_swap(manifest, model, destination_root, downloads_root)
    installed_status = {
        model.name: runtime_model_is_installed(manifest, model, destination_root)
        for model in manifest.models
    }
    required_disk = calculate_runtime_model_disk_bytes(
        manifest,
        destination_root,
        installed_status=installed_status,
    )
    free = disk_usage(destination_root).free
    if free < required_disk:
        raise ProvisioningError(
            f"insufficient disk space: need {required_disk} bytes, have {free} bytes"
        )

    total = manifest.download_size
    completed = 0
    network_transferred = 0
    started = clock()
    installed: list[str] = []
    reused: list[str] = []

    def emit(status: str, asset: str | None, value: int, message: str | None = None) -> None:
        if progress is None:
            return
        elapsed = max(clock() - started, 0.0)
        eta: int | None = None
        if 0 < network_transferred < total and elapsed > 0:
            eta = max(0, int((total - value) / (network_transferred / elapsed)))
        progress(
            ProvisionEvent(
                status=status,
                release=manifest.release,
                asset=asset,
                completed=value,
                total=total,
                eta_seconds=eta,
                message=message,
            )
        )

    emit("preflight", None, completed, f"required_disk_bytes={required_disk}")
    for model in manifest.models:
        if installed_status[model.name]:
            completed += model.installed_size
            reused.append(model.name)
            emit("reused", model.name, completed)
            continue

        group_root = _model_path(downloads_root, model)
        installed_root = _model_path(destination_root, model)
        group_root.mkdir(parents=True, exist_ok=True)
        _clean_download_group(group_root, model)
        group_completed = 0
        for file in model.files:
            final = _file_path(group_root, file)
            part = _part_path(group_root, file)
            if _file_is_valid(final, file):
                part.unlink(missing_ok=True)
                group_completed += file.size
                emit("reused", f"{model.name}/{file.path}", completed + group_completed)
                continue
            final.unlink(missing_ok=True)
            if _seed_from_installed(_file_path(installed_root, file), final, file):
                group_completed += file.size
                emit("reused", f"{model.name}/{file.path}", completed + group_completed)
                continue
            if part.is_file() and part.stat().st_size > file.size:
                part.unlink()

            for verification_attempt in range(2):
                last_download_done = part.stat().st_size if part.is_file() else 0

                def file_progress(
                    done: int,
                    _total: int,
                    *,
                    base: int = completed + group_completed,
                    asset_name: str = f"{model.name}/{file.path}",
                ) -> None:
                    nonlocal last_download_done, network_transferred
                    network_transferred += max(done - last_download_done, 0)
                    last_download_done = done
                    emit("downloading", asset_name, base + done)

                downloader(model, file, part, file_progress)
                emit(
                    "verifying",
                    f"{model.name}/{file.path}",
                    completed + group_completed + file.size,
                )
                try:
                    verify_file(part, size=file.size, sha256=file.sha256)
                except ProvisioningError:
                    part.unlink(missing_ok=True)
                    if verification_attempt == 0:
                        emit(
                            "retrying",
                            f"{model.name}/{file.path}",
                            completed + group_completed,
                            "discarded corrupt partial download",
                        )
                        continue
                    raise
                break
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, final)
            group_completed += file.size

        _atomic_write_json(group_root / RUNTIME_MODEL_MARKER, _marker_payload(manifest, model))
        verify_runtime_model(manifest, model, downloads_root)
        emit("installing", model.name, completed + group_completed)
        _atomic_install_directory(
            group_root,
            installed_root,
            verify=lambda selected=model: verify_runtime_model(
                manifest, selected, destination_root
            ),
        )
        _cleanup_swap_backups(installed_root)
        completed += model.installed_size
        installed.append(model.name)
        emit("installed", model.name, completed)

    emit("complete", None, total)
    return ProvisionResult(
        release=manifest.release,
        installed=tuple(installed),
        reused=tuple(reused),
        required_disk_bytes=required_disk,
    )


def provision_runtime_models(
    manifest: RuntimeModelManifest,
    destination_root: Path,
    *,
    progress: ProgressCallback | None = None,
    downloader: RuntimeModelDownloader = _default_downloader,
    disk_usage: DiskUsage = shutil.disk_usage,
    clock: Clock = time.monotonic,
) -> ProvisionResult:
    """Provision one model root at a time across GUI, CLI, and Setup processes."""

    destination_root.mkdir(parents=True, exist_ok=True)
    lock = _runtime_model_lock(destination_root)
    if not lock.acquire():
        raise ProvisioningError(
            "runtime model provisioning is already in progress for this installation"
        )
    try:
        return _provision_runtime_models_locked(
            manifest,
            destination_root,
            progress=progress,
            downloader=downloader,
            disk_usage=disk_usage,
            clock=clock,
        )
    finally:
        lock.release()


def find_runtime_model_manifest(
    *,
    runtime_root: Path,
    executable: Path | None = None,
) -> Path | None:
    found = find_manifest(
        RUNTIME_MODEL_MANIFEST_FILENAME,
        runtime_root=runtime_root,
        executable=executable,
    )
    if found is not None:
        return found
    repository = Path.cwd() / "packaging" / "manifests" / RUNTIME_MODEL_MANIFEST_FILENAME
    return repository if repository.is_file() else None


__all__ = [
    "RUNTIME_MODEL_MANIFEST_FILENAME",
    "RUNTIME_MODEL_MANIFEST_SCHEMA_VERSION",
    "RUNTIME_MODEL_MARKER",
    "RuntimeModel",
    "RuntimeModelDownloader",
    "RuntimeModelFile",
    "RuntimeModelLicense",
    "RuntimeModelManifest",
    "RuntimeModelManifestError",
    "RuntimeModelSource",
    "calculate_runtime_model_disk_bytes",
    "find_runtime_model_manifest",
    "huggingface_download_url",
    "load_runtime_model_manifest",
    "provision_runtime_models",
    "runtime_model_is_installed",
    "verify_installed_runtime_models",
    "verify_runtime_model",
]
