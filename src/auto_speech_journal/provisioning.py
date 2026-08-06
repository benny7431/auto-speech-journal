from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse

MANIFEST_SCHEMA_VERSION = 1
_BUFFER_SIZE = 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ProvisioningError(RuntimeError):
    pass


class ManifestError(ProvisioningError):
    pass


class DownloadError(ProvisioningError):
    pass


class VerificationError(ProvisioningError):
    pass


@dataclass(frozen=True, slots=True)
class RequiredFile:
    path: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionAsset:
    name: str
    url: str
    sha256: str
    size: int
    installed_size: int
    destination: str
    archive: str = "file"
    strip_prefix: str | None = None
    required_files: tuple[RequiredFile, ...] = ()


@dataclass(frozen=True, slots=True)
class ProvisionManifest:
    schema_version: int
    release: str
    assets: tuple[ProvisionAsset, ...]
    source_path: Path | None = None

    @property
    def download_size(self) -> int:
        return sum(asset.size for asset in self.assets)

    @property
    def installed_size(self) -> int:
        return sum(asset.installed_size for asset in self.assets)


@dataclass(frozen=True, slots=True)
class ProvisionEvent:
    status: str
    release: str
    asset: str | None
    completed: int
    total: int
    eta_seconds: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    release: str
    installed: tuple[str, ...]
    reused: tuple[str, ...]
    required_disk_bytes: int


ProgressCallback = Callable[[ProvisionEvent], None]
AssetDownloader = Callable[[ProvisionAsset, Path, Callable[[int, int], None]], None]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


class Response(Protocol):
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes: ...

    def getcode(self) -> int: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...


UrlOpener = Callable[[urllib.request.Request, float], Response]


def _relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ManifestError(f"{field} must stay inside its destination root")
    return path.as_posix()


def _positive_int(value: object, *, field: str, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ManifestError(f"{field} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ManifestError(f"{field} must be an integer") from error
    if parsed < 0 or (parsed == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ManifestError(f"{field} must be {qualifier}")
    return parsed


def _required_file(raw: object, *, asset_name: str) -> RequiredFile:
    if isinstance(raw, str):
        return RequiredFile(path=_relative_path(raw, field="required_files.path"))
    if not isinstance(raw, dict):
        raise ManifestError(f"asset {asset_name!r} has an invalid required_files entry")
    path = _relative_path(raw.get("path"), field="required_files.path")
    size_raw = raw.get("size")
    size = (
        None
        if size_raw is None
        else _positive_int(size_raw, field=f"{path}.size", allow_zero=True)
    )
    digest_raw = raw.get("sha256")
    digest = None if digest_raw is None else str(digest_raw).lower()
    if digest is not None and not _SHA256.fullmatch(digest):
        raise ManifestError(f"required file {path!r} has an invalid SHA-256")
    return RequiredFile(path=path, size=size, sha256=digest)


def _asset_from_dict(raw: object) -> ProvisionAsset:
    if not isinstance(raw, dict):
        raise ManifestError("each manifest asset must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ManifestError(f"invalid asset name: {name!r}")
    url = raw.get("url")
    if not isinstance(url, str) or url.startswith("PLACEHOLDER"):
        raise ManifestError(f"asset {name!r} does not have a published URL")
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ManifestError(f"asset {name!r} URL must use HTTPS")
    sha256 = str(raw.get("sha256", "")).lower()
    if not _SHA256.fullmatch(sha256) or set(sha256) == {"0"}:
        raise ManifestError(f"asset {name!r} has an invalid SHA-256")
    size = _positive_int(raw.get("size"), field=f"{name}.size")
    installed_size = _positive_int(
        raw.get("installed_size", size),
        field=f"{name}.installed_size",
    )
    archive = str(raw.get("archive", "file")).lower()
    if archive not in {"file", "zip", "tar"}:
        raise ManifestError(f"asset {name!r} has unsupported archive type {archive!r}")
    destination = _relative_path(raw.get("destination"), field=f"{name}.destination")
    strip_raw = raw.get("strip_prefix")
    strip_prefix = (
        None
        if strip_raw in {None, ""}
        else _relative_path(strip_raw, field=f"{name}.strip_prefix")
    )
    required_raw = raw.get("required_files", [])
    if not isinstance(required_raw, list):
        raise ManifestError(f"asset {name!r} required_files must be an array")
    required = tuple(_required_file(item, asset_name=name) for item in required_raw)
    required_paths = [item.path.casefold() for item in required]
    if len(required_paths) != len(set(required_paths)):
        raise ManifestError(f"asset {name!r} required_files paths must be unique")
    if archive != "file" and not required:
        raise ManifestError(f"archive asset {name!r} must declare required_files")
    return ProvisionAsset(
        name=name,
        url=url,
        sha256=sha256,
        size=size,
        installed_size=installed_size,
        destination=destination,
        archive=archive,
        strip_prefix=strip_prefix,
        required_files=required,
    )


def load_manifest(path: Path) -> ProvisionManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManifestError(f"unable to read provision manifest {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError("provision manifest root must be an object")
    schema_version = raw.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported provision manifest schema {schema_version!r}; "
            f"expected {MANIFEST_SCHEMA_VERSION}"
        )
    release = raw.get("release")
    if not isinstance(release, str) or not _SAFE_NAME.fullmatch(release):
        raise ManifestError(f"invalid manifest release: {release!r}")
    assets_raw = raw.get("assets")
    if not isinstance(assets_raw, list):
        raise ManifestError("provision manifest assets must be an array")
    assets = tuple(_asset_from_dict(asset) for asset in assets_raw)
    names = [asset.name.casefold() for asset in assets]
    destinations = [asset.destination.casefold() for asset in assets]
    if len(names) != len(set(names)):
        raise ManifestError("provision manifest asset names must be unique")
    if len(destinations) != len(set(destinations)):
        raise ManifestError("provision manifest destinations must be unique")
    return ProvisionManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        release=release,
        assets=assets,
        source_path=path,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_BUFFER_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file(path: Path, *, size: int | None, sha256: str | None) -> None:
    if not path.is_file():
        raise VerificationError(f"required file is missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise VerificationError(
            f"wrong size for {path}: expected {size}, got {path.stat().st_size}"
        )
    if sha256 is not None:
        actual = sha256_file(path)
        if actual.casefold() != sha256.casefold():
            raise VerificationError(
                f"wrong SHA-256 for {path.name}: expected {sha256}, got {actual}"
            )


def _default_opener(request: urllib.request.Request, timeout: float) -> Response:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[return-value]


def download_resumable(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    progress: Callable[[int, int], None] | None = None,
    opener: UrlOpener = _default_opener,
    sleep: Sleep = time.sleep,
    retries: int = 4,
    timeout: float = 60.0,
) -> None:
    """Download to a persistent .part file, resuming only on a valid 206 response."""
    if retries < 1:
        raise ValueError("retries must be at least one")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > expected_size:
        destination.unlink()

    last_error: BaseException | None = None
    for attempt in range(retries):
        offset = destination.stat().st_size if destination.exists() else 0
        if offset == expected_size:
            if progress is not None:
                progress(offset, expected_size)
            return
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": "AutoSpeechJournal-Installer/1",
        }
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener(request, timeout) as response:
                status = response.getcode()
                append = offset > 0 and status == 206
                if append:
                    content_range = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
                    start = int(match.group(1)) if match is not None else -1
                    end = int(match.group(2)) if match is not None else -1
                    total = (
                        int(match.group(3))
                        if match is not None and match.group(3) != "*"
                        else -1
                    )
                    if (
                        match is None
                        or start != offset
                        or end < start
                        or end != expected_size - 1
                        or total != expected_size
                    ):
                        destination.unlink(missing_ok=True)
                        raise DownloadError(
                            f"invalid Content-Range for resumed {destination.name}: "
                            f"{content_range!r}"
                        )
                if offset > 0 and not append:
                    offset = 0
                mode = "ab" if append else "wb"
                completed = offset
                with destination.open(mode) as output:
                    while block := response.read(_BUFFER_SIZE):
                        output.write(block)
                        completed += len(block)
                        if completed > expected_size:
                            raise DownloadError(
                                f"download exceeded declared size for {destination.name}"
                            )
                        if progress is not None:
                            progress(completed, expected_size)
                    output.flush()
                    os.fsync(output.fileno())
            actual_size = destination.stat().st_size
            if actual_size != expected_size:
                raise DownloadError(
                    f"incomplete download for {destination.name}: "
                    f"expected {expected_size}, got {actual_size}"
                )
            return
        except (OSError, urllib.error.URLError, DownloadError) as error:
            if offset and isinstance(error, urllib.error.HTTPError) and error.code == 416:
                destination.unlink(missing_ok=True)
            last_error = error
            if attempt + 1 < retries:
                sleep(min(2 ** attempt, 8))
    raise DownloadError(f"unable to download {url}: {last_error}") from last_error


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise VerificationError(f"unsafe ZIP member: {member.filename}")
            mode = member.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise VerificationError(f"ZIP links are not accepted: {member.filename}")
        bundle.extractall(destination)


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, mode="r:*") as bundle:
        members = bundle.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(root):
                raise VerificationError(f"unsafe TAR member: {member.name}")
            if member.issym() or member.islnk():
                raise VerificationError(f"TAR links are not accepted: {member.name}")
            if not member.isfile() and not member.isdir():
                raise VerificationError(f"unsupported TAR member: {member.name}")
        bundle.extractall(destination, members=members)


def _directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _asset_marker_path(destination: Path, asset: ProvisionAsset) -> Path:
    if asset.archive == "file":
        return destination.with_suffix(destination.suffix + ".asj-manifest.json")
    return destination / ".asj-manifest.json"


def _verify_required_files(root: Path, required_files: tuple[RequiredFile, ...]) -> None:
    for required in required_files:
        verify_file(
            root.joinpath(*PurePosixPath(required.path).parts),
            size=required.size,
            sha256=required.sha256,
        )


def _verify_archive_payload(root: Path, asset: ProvisionAsset) -> None:
    _verify_required_files(root, asset.required_files)
    expected = {PurePosixPath(item.path).as_posix() for item in asset.required_files}
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".asj-manifest.json"
    }
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise VerificationError(
            f"archive file list mismatch for {asset.name}: "
            f"missing={missing}, unexpected={unexpected}"
        )
    actual_size = sum((root / path).stat().st_size for path in actual)
    if actual_size != asset.installed_size:
        raise VerificationError(
            f"wrong installed size for {asset.name}: "
            f"expected {asset.installed_size}, got {actual_size}"
        )


def asset_is_installed(destination_root: Path, release: str, asset: ProvisionAsset) -> bool:
    destination = destination_root.joinpath(*PurePosixPath(asset.destination).parts)
    try:
        if asset.archive == "file":
            verify_file(destination, size=asset.installed_size, sha256=asset.sha256)
        else:
            if not destination.is_dir():
                return False
            _verify_archive_payload(destination, asset)
        marker_path = _asset_marker_path(destination, asset)
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        return (
            marker.get("schema_version") == MANIFEST_SCHEMA_VERSION
            and marker.get("release") == release
            and marker.get("asset") == asset.name
            and marker.get("sha256") == asset.sha256
        )
    except (OSError, ValueError, VerificationError):
        return False


def calculate_required_disk_bytes(
    manifest: ProvisionManifest,
    destination_root: Path,
    *,
    downloads_root: Path | None = None,
) -> int:
    downloads = downloads_root or destination_root / ".downloads" / manifest.release
    required = 0
    for asset in manifest.assets:
        if asset_is_installed(destination_root, manifest.release, asset):
            continue
        part = downloads / f"{asset.name}.part"
        present = min(part.stat().st_size, asset.size) if part.is_file() else 0
        target = destination_root.joinpath(*PurePosixPath(asset.destination).parts)
        required += asset.size - present
        required += asset.installed_size
        required += _directory_size(target)
    return int(required * 1.2)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProgressFile:
    """Atomically replace a single JSON status file for an installer to poll."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, event: ProvisionEvent) -> None:
        _atomic_write_json(self.path, asdict(event))

    def failed(self, release: str, error: BaseException) -> None:
        self(
            ProvisionEvent(
                status="error",
                release=release,
                asset=None,
                completed=0,
                total=0,
                message=str(error),
            )
        )


def _write_asset_marker(destination: Path, release: str, asset: ProvisionAsset) -> None:
    marker_path = _asset_marker_path(destination, asset)
    files: dict[str, dict[str, int | str]] = {}
    root = destination if asset.archive != "file" else destination.parent
    for required in asset.required_files:
        path = root.joinpath(*PurePosixPath(required.path).parts)
        if path.is_file():
            files[required.path] = {
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    _atomic_write_json(
        marker_path,
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "release": release,
            "asset": asset.name,
            "sha256": asset.sha256,
            "files": files,
        },
    )


def _atomic_install(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(f"{destination.name}.old-{uuid.uuid4().hex}")
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, previous)
    try:
        os.replace(source, destination)
    except Exception:
        if had_previous and previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    if previous.is_dir():
        shutil.rmtree(previous, ignore_errors=True)
    else:
        previous.unlink(missing_ok=True)


def _atomic_install_file_with_marker(
    source: Path,
    destination: Path,
    release: str,
    asset: ProvisionAsset,
) -> None:
    """Commit a file asset and its ownership marker as one rollback unit."""
    _write_asset_marker(source, release, asset)
    source_marker = _asset_marker_path(source, asset)
    destination_marker = _asset_marker_path(destination, asset)
    token = uuid.uuid4().hex
    previous = destination.with_name(f"{destination.name}.old-{token}")
    previous_marker = destination_marker.with_name(
        f"{destination_marker.name}.old-{token}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    had_previous = destination.exists()
    had_previous_marker = destination_marker.exists()
    try:
        if had_previous:
            os.replace(destination, previous)
        if had_previous_marker:
            os.replace(destination_marker, previous_marker)
        os.replace(source, destination)
        os.replace(source_marker, destination_marker)
    except Exception as install_error:
        try:
            destination.unlink(missing_ok=True)
            destination_marker.unlink(missing_ok=True)
            if had_previous and previous.exists():
                os.replace(previous, destination)
            if had_previous_marker and previous_marker.exists():
                os.replace(previous_marker, destination_marker)
        except Exception as rollback_error:
            raise ProvisioningError(
                f"file asset install failed and rollback also failed: {rollback_error}"
            ) from install_error
        raise
    finally:
        source.unlink(missing_ok=True)
        source_marker.unlink(missing_ok=True)
    previous.unlink(missing_ok=True)
    previous_marker.unlink(missing_ok=True)


def _install_downloaded_asset(
    downloaded: Path,
    destination_root: Path,
    staging_root: Path,
    release: str,
    asset: ProvisionAsset,
) -> None:
    destination = destination_root.joinpath(*PurePosixPath(asset.destination).parts)
    if asset.archive == "file":
        staged_file = staging_root / f"payload-{uuid.uuid4().hex}"
        shutil.copyfile(downloaded, staged_file)
        _atomic_install_file_with_marker(staged_file, destination, release, asset)
        return

    extract_root = staging_root / f"extract-{uuid.uuid4().hex}"
    extract_root.mkdir(parents=True)
    if asset.archive == "zip":
        _safe_extract_zip(downloaded, extract_root)
    else:
        _safe_extract_tar(downloaded, extract_root)
    source = extract_root
    if asset.strip_prefix:
        source = extract_root.joinpath(*PurePosixPath(asset.strip_prefix).parts)
        if not source.is_dir():
            raise VerificationError(
                f"asset {asset.name!r} does not contain strip_prefix {asset.strip_prefix!r}"
            )
    _verify_archive_payload(source, asset)
    payload = staging_root / f"payload-{uuid.uuid4().hex}"
    os.replace(source, payload)
    _write_asset_marker(payload, release, asset)
    _atomic_install(payload, destination)
    shutil.rmtree(extract_root, ignore_errors=True)


def _default_asset_downloader(
    asset: ProvisionAsset,
    destination: Path,
    progress: Callable[[int, int], None],
) -> None:
    download_resumable(
        asset.url,
        destination,
        expected_size=asset.size,
        progress=progress,
    )


def provision(
    manifest: ProvisionManifest,
    destination_root: Path,
    *,
    progress: ProgressCallback | None = None,
    downloader: AssetDownloader = _default_asset_downloader,
    disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
    clock: Clock = time.monotonic,
) -> ProvisionResult:
    destination_root.mkdir(parents=True, exist_ok=True)
    downloads_root = destination_root / ".downloads" / manifest.release
    staging_root = destination_root / ".staging" / manifest.release
    downloads_root.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    required_disk = calculate_required_disk_bytes(
        manifest,
        destination_root,
        downloads_root=downloads_root,
    )
    free = disk_usage(destination_root).free
    if free < required_disk:
        raise ProvisioningError(
            f"insufficient disk space: need {required_disk} bytes, have {free} bytes"
        )

    total = manifest.download_size
    completed_before = 0
    started = clock()
    installed: list[str] = []
    reused: list[str] = []

    def emit(status: str, asset: str | None, completed: int, message: str | None = None) -> None:
        if progress is None:
            return
        elapsed = max(clock() - started, 0.0)
        eta: int | None = None
        if completed > 0 and completed < total and elapsed > 0:
            eta = max(0, int((total - completed) / (completed / elapsed)))
        progress(
            ProvisionEvent(
                status=status,
                release=manifest.release,
                asset=asset,
                completed=completed,
                total=total,
                eta_seconds=eta,
                message=message,
            )
        )

    emit("preflight", None, 0, f"required_disk_bytes={required_disk}")
    for asset in manifest.assets:
        if asset_is_installed(destination_root, manifest.release, asset):
            completed_before += asset.size
            reused.append(asset.name)
            emit("reused", asset.name, completed_before)
            continue

        part = downloads_root / f"{asset.name}.part"

        def asset_progress(
            done: int,
            _asset_total: int,
            *,
            asset_name: str = asset.name,
            base: int = completed_before,
        ) -> None:
            emit("downloading", asset_name, base + done)

        for verification_attempt in range(2):
            downloader(asset, part, asset_progress)
            emit("verifying", asset.name, completed_before + asset.size)
            try:
                verify_file(part, size=asset.size, sha256=asset.sha256)
            except VerificationError:
                # A complete but corrupt .part would otherwise be treated as resumable
                # forever. Remove it and fetch once from byte zero before failing.
                part.unlink(missing_ok=True)
                if verification_attempt == 0:
                    emit(
                        "retrying",
                        asset.name,
                        completed_before,
                        "discarded corrupt partial download",
                    )
                    continue
                raise
            break
        emit("installing", asset.name, completed_before + asset.size)
        try:
            _install_downloaded_asset(
                part,
                destination_root,
                staging_root,
                manifest.release,
                asset,
            )
        except Exception:
            # Keep the verified download for Repair; staging never replaces a valid target
            # until all extraction and per-file checks have passed.
            raise
        part.unlink(missing_ok=True)
        completed_before += asset.size
        installed.append(asset.name)
        emit("installed", asset.name, completed_before)

    emit("complete", None, total)
    return ProvisionResult(
        release=manifest.release,
        installed=tuple(installed),
        reused=tuple(reused),
        required_disk_bytes=required_disk,
    )


def find_manifest(
    filename: str,
    *,
    runtime_root: Path,
    executable: Path | None = None,
) -> Path | None:
    executable = executable or Path(os.path.abspath(os.sys.executable))
    candidates = (
        executable.parent / "manifests" / filename,
        executable.parent.parent / "manifests" / filename,
        executable.parent.parent.parent / "manifests" / filename,
        runtime_root / "manifests" / filename,
    )
    return next((path for path in candidates if path.is_file()), None)


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "DownloadError",
    "ManifestError",
    "ProgressFile",
    "ProvisionAsset",
    "ProvisionEvent",
    "ProvisionManifest",
    "ProvisionResult",
    "ProvisioningError",
    "RequiredFile",
    "VerificationError",
    "asset_is_installed",
    "calculate_required_disk_bytes",
    "download_resumable",
    "find_manifest",
    "load_manifest",
    "provision",
    "sha256_file",
    "verify_file",
]
