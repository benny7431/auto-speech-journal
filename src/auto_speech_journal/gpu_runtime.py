from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .provisioning import (
    ProgressCallback,
    ProvisionAsset,
    ProvisionEvent,
    ProvisioningError,
    VerificationError,
    download_resumable,
    sha256_file,
    verify_file,
)

GPU_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MIN_DRIVER_VERSION = "576.02"
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GPU_DLL_HANDLES: list[Any] = []


class GpuRuntimeError(ProvisioningError):
    pass


@dataclass(frozen=True, slots=True)
class GpuWheel:
    name: str
    url: str
    sha256: str
    size: int
    installed_size: int | None = None

    @property
    def effective_installed_size(self) -> int:
        return self.size if self.installed_size is None else self.installed_size

    def as_provision_asset(self) -> ProvisionAsset:
        return ProvisionAsset(
            name=self.name,
            url=self.url,
            sha256=self.sha256,
            size=self.size,
            installed_size=self.effective_installed_size,
            destination="gpu-runtime",
            archive="zip",
        )


@dataclass(frozen=True, slots=True)
class GpuRuntimeManifest:
    schema_version: int
    release: str
    min_driver_version: str
    assets: tuple[GpuWheel, ...]
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class GpuDetection:
    available: bool
    compatible: bool
    driver_version: str | None
    gpu_names: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class GpuProbe:
    passed: bool
    device_count: int
    compute_types: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class GpuInstallResult:
    active_device: str
    installed: bool
    detection: GpuDetection
    probe: GpuProbe | None
    diagnostic: str


PINNED_GPU_MANIFEST = GpuRuntimeManifest(
    schema_version=GPU_MANIFEST_SCHEMA_VERSION,
    release="cuda-runtime-v1",
    min_driver_version=DEFAULT_MIN_DRIVER_VERSION,
    assets=(
        GpuWheel(
            name="nvidia-cublas-cu12",
            url=(
                "https://files.pythonhosted.org/packages/20/e2/"
                "fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
                "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
            ),
            sha256="623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
            size=553_162_896,
        ),
        GpuWheel(
            name="nvidia-cuda-nvrtc-cu12",
            url=(
                "https://files.pythonhosted.org/packages/52/de/"
                "823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
                "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl"
            ),
            sha256="72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349",
            size=76_408_187,
        ),
        GpuWheel(
            name="nvidia-cudnn-cu12",
            url=(
                "https://files.pythonhosted.org/packages/29/28/"
                "2c9a2a97a8b3fedcf74a14f38fd5edfae12274380a829fdc6b16ce29be4c/"
                "nvidia_cudnn_cu12-9.24.0.43-py3-none-win_amd64.whl"
            ),
            sha256="cbd41a0ab084422c936dc9fb2fc89be5ea9a85bc421c6f23d0243bdfc945fbef",
            size=737_103_728,
        ),
    ),
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
WheelDownloader = Callable[[GpuWheel, Path, Callable[[int, int], None]], None]
GpuProber = Callable[[Path, Path | None], GpuProbe]


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", value)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def detect_nvidia_gpu(
    *,
    min_driver_version: str = DEFAULT_MIN_DRIVER_VERSION,
    runner: CommandRunner = _default_runner,
    os_name: str = os.name,
) -> GpuDetection:
    if os_name != "nt":
        return GpuDetection(False, False, None, (), "NVIDIA runtime is supported on Windows only")
    try:
        result = runner(
            (
                "nvidia-smi.exe",
                "--query-gpu=driver_version,name",
                "--format=csv,noheader,nounits",
            )
        )
    except OSError as error:
        return GpuDetection(False, False, None, (), f"nvidia-smi unavailable: {error}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "nvidia-smi failed").strip()
        return GpuDetection(False, False, None, (), detail)

    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    parsed: list[tuple[str, str]] = []
    for row in rows:
        driver, separator, name = row.partition(",")
        if separator and _version_tuple(driver):
            parsed.append((driver.strip(), name.strip()))
    if not parsed:
        return GpuDetection(False, False, None, (), "nvidia-smi returned no usable GPU rows")
    driver = min((item[0] for item in parsed), key=_version_tuple)
    compatible = _version_tuple(driver) >= _version_tuple(min_driver_version)
    reason = (
        f"driver {driver} satisfies minimum {min_driver_version}"
        if compatible
        else f"driver {driver} is older than required {min_driver_version}"
    )
    return GpuDetection(True, compatible, driver, tuple(item[1] for item in parsed), reason)


def _parse_wheel(raw: object) -> GpuWheel:
    if not isinstance(raw, dict):
        raise GpuRuntimeError("each GPU manifest asset must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise GpuRuntimeError(f"invalid GPU wheel name: {name!r}")
    url = raw.get("url")
    if not isinstance(url, str) or url.startswith("PLACEHOLDER"):
        raise GpuRuntimeError(f"GPU wheel {name!r} does not have a published URL")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise GpuRuntimeError(f"GPU wheel {name!r} URL must use HTTPS")
    digest = str(raw.get("sha256", "")).lower()
    if not _SHA256.fullmatch(digest) or set(digest) == {"0"}:
        raise GpuRuntimeError(f"GPU wheel {name!r} has an invalid SHA-256")
    try:
        size = int(raw.get("size"))
    except (TypeError, ValueError) as error:
        raise GpuRuntimeError(f"GPU wheel {name!r} size must be an integer") from error
    if size <= 0:
        raise GpuRuntimeError(f"GPU wheel {name!r} size must be positive")
    try:
        installed_size = int(raw.get("installed_size", size))
    except (TypeError, ValueError) as error:
        raise GpuRuntimeError(
            f"GPU wheel {name!r} installed_size must be an integer"
        ) from error
    if installed_size <= 0:
        raise GpuRuntimeError(f"GPU wheel {name!r} installed_size must be positive")
    return GpuWheel(
        name=name,
        url=url,
        sha256=digest,
        size=size,
        installed_size=installed_size,
    )


def load_gpu_manifest(path: Path) -> GpuRuntimeManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GpuRuntimeError(f"unable to read GPU manifest {path}: {error}") from error
    if not isinstance(raw, dict):
        raise GpuRuntimeError("GPU manifest root must be an object")
    if raw.get("schema_version") != GPU_MANIFEST_SCHEMA_VERSION:
        raise GpuRuntimeError(
            f"unsupported GPU manifest schema {raw.get('schema_version')!r}"
        )
    release = raw.get("release")
    if not isinstance(release, str) or not _SAFE_NAME.fullmatch(release):
        raise GpuRuntimeError(f"invalid GPU manifest release: {release!r}")
    minimum = str(raw.get("min_driver_version", ""))
    if not _version_tuple(minimum):
        raise GpuRuntimeError("GPU manifest min_driver_version is invalid")
    assets_raw = raw.get("assets")
    if not isinstance(assets_raw, list) or not assets_raw:
        raise GpuRuntimeError("GPU manifest assets must be a non-empty array")
    assets = tuple(_parse_wheel(asset) for asset in assets_raw)
    if len({asset.name.casefold() for asset in assets}) != len(assets):
        raise GpuRuntimeError("GPU manifest wheel names must be unique")
    return GpuRuntimeManifest(
        schema_version=GPU_MANIFEST_SCHEMA_VERSION,
        release=release,
        min_driver_version=minimum,
        assets=assets,
        source_path=path,
    )


def _default_wheel_downloader(
    wheel: GpuWheel,
    destination: Path,
    progress: Callable[[int, int], None],
) -> None:
    download_resumable(
        wheel.url,
        destination,
        expected_size=wheel.size,
        progress=progress,
    )


def _safe_dll_name(member: str) -> str | None:
    normalized = PurePosixPath(member.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise GpuRuntimeError(f"unsafe wheel member: {member}")
    lowered = tuple(part.casefold() for part in normalized.parts)
    if "bin" not in lowered or normalized.suffix.casefold() != ".dll":
        return None
    name = normalized.name
    if not name or name in {".", ".."}:
        raise GpuRuntimeError(f"unsafe DLL name in wheel: {member}")
    return name


def _extract_runtime_dlls(wheels: Sequence[tuple[GpuWheel, Path]], destination: Path) -> None:
    destination.mkdir(parents=True)
    extracted_by_wheel: dict[str, int] = {}
    known_hashes: dict[str, str] = {}
    for wheel, archive in wheels:
        count = 0
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                dll_name = _safe_dll_name(member.filename)
                if dll_name is None:
                    continue
                target = destination / dll_name
                with bundle.open(member) as source:
                    digest = hashlib.sha256()
                    temporary = target.with_suffix(target.suffix + ".tmp")
                    with temporary.open("wb") as output:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                            digest.update(block)
                        output.flush()
                        os.fsync(output.fileno())
                actual = digest.hexdigest()
                existing = known_hashes.get(dll_name.casefold())
                if existing is not None and existing != actual:
                    temporary.unlink(missing_ok=True)
                    raise GpuRuntimeError(f"conflicting DLL payload for {dll_name}")
                if existing is None:
                    os.replace(temporary, target)
                    known_hashes[dll_name.casefold()] = actual
                else:
                    temporary.unlink(missing_ok=True)
                count += 1
        if count == 0:
            raise GpuRuntimeError(f"GPU wheel {wheel.name!r} contains no runtime DLLs")
        extracted_by_wheel[wheel.name] = count

    files = {
        path.name: {"size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(destination.glob("*.dll"))
    }
    _write_json(
        destination / "gpu-runtime.json",
        {"schema_version": 1, "wheels": extracted_by_wheel, "files": files},
    )


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
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


def activate_gpu_runtime(runtime_dir: Path) -> bool:
    if os.name != "nt" or not runtime_dir.is_dir():
        return False
    value = str(runtime_dir.resolve())
    current = os.environ.get("PATH", "")
    entries = [entry for entry in current.split(os.pathsep) if entry]
    if value.casefold() not in {entry.casefold() for entry in entries}:
        os.environ["PATH"] = os.pathsep.join([value, *entries])
    if hasattr(os, "add_dll_directory") and not _GPU_DLL_HANDLES:
        _GPU_DLL_HANDLES.append(os.add_dll_directory(value))
    return True


def probe_gpu_runtime(runtime_dir: Path, model_dir: Path | None = None) -> GpuProbe:
    if not activate_gpu_runtime(runtime_dir):
        return GpuProbe(False, 0, (), "GPU runtime directory is unavailable")
    try:
        ctranslate2 = importlib.import_module("ctranslate2")
        device_count = int(ctranslate2.get_cuda_device_count())
        if device_count < 1:
            return GpuProbe(False, 0, (), "CTranslate2 reports no CUDA device")
        compute_types = tuple(sorted(ctranslate2.get_supported_compute_types("cuda")))
        if model_dir is not None and (model_dir / "model.bin").is_file():
            translator = ctranslate2.Translator(
                str(model_dir),
                device="cuda",
                compute_type="int8_float16",
            )
            del translator
        return GpuProbe(True, device_count, compute_types, "CTranslate2 CUDA probe passed")
    except Exception as error:
        return GpuProbe(False, 0, (), f"CTranslate2 CUDA probe failed: {error}")


def _atomic_replace_directory(source: Path, destination: Path) -> None:
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
    shutil.rmtree(previous, ignore_errors=True)


def install_gpu_runtime(
    runtime_root: Path,
    *,
    manifest: GpuRuntimeManifest = PINNED_GPU_MANIFEST,
    force: bool = False,
    progress: ProgressCallback | None = None,
    downloader: WheelDownloader = _default_wheel_downloader,
    detector: Callable[..., GpuDetection] = detect_nvidia_gpu,
    prober: GpuProber = probe_gpu_runtime,
    model_dir: Path | None = None,
    disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
    clock: Callable[[], float] = time.monotonic,
) -> GpuInstallResult:
    runtime_root.mkdir(parents=True, exist_ok=True)
    target = runtime_root / "gpu-runtime"
    detection = detector(min_driver_version=manifest.min_driver_version)
    if not detection.compatible and not force:
        result = GpuInstallResult(
            active_device="cpu",
            installed=False,
            detection=detection,
            probe=None,
            diagnostic=f"CPU fallback: {detection.reason}",
        )
        _write_json(runtime_root / "gpu-runtime-status.json", asdict(result))
        return result

    downloads = runtime_root / ".downloads" / manifest.release
    downloads.mkdir(parents=True, exist_ok=True)
    existing_size = (
        sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
        if target.exists()
        else 0
    )
    remaining = sum(
        max(wheel.size - min((downloads / f"{wheel.name}.part").stat().st_size, wheel.size), 0)
        if (downloads / f"{wheel.name}.part").is_file()
        else wheel.size
        for wheel in manifest.assets
    )
    installed_size = sum(wheel.effective_installed_size for wheel in manifest.assets)
    required = int((remaining + installed_size + existing_size) * 1.2)
    if disk_usage(runtime_root).free < required:
        raise GpuRuntimeError(f"insufficient disk space for GPU runtime: need {required} bytes")

    total = sum(wheel.size for wheel in manifest.assets)
    completed_before = 0
    started = clock()

    def emit(status: str, asset: str | None, completed: int, message: str | None = None) -> None:
        if progress is None:
            return
        elapsed = max(clock() - started, 0.0)
        eta = None
        if elapsed > 0 and 0 < completed < total:
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

    wheel_paths: list[tuple[GpuWheel, Path]] = []
    for wheel in manifest.assets:
        part = downloads / f"{wheel.name}.part"

        def wheel_progress(
            done: int,
            _total: int,
            *,
            name: str = wheel.name,
            base: int = completed_before,
        ) -> None:
            emit("downloading", name, base + done)

        for verification_attempt in range(2):
            downloader(wheel, part, wheel_progress)
            emit("verifying", wheel.name, completed_before + wheel.size)
            try:
                verify_file(part, size=wheel.size, sha256=wheel.sha256)
            except (OSError, VerificationError):
                part.unlink(missing_ok=True)
                if verification_attempt == 0:
                    emit(
                        "retrying",
                        wheel.name,
                        completed_before,
                        "discarded corrupt partial download",
                    )
                    continue
                raise
            break
        wheel_paths.append((wheel, part))
        completed_before += wheel.size

    staging = runtime_root / ".staging" / f"gpu-{uuid.uuid4().hex}"
    payload = staging / "payload"
    try:
        emit("installing", None, total)
        _extract_runtime_dlls(wheel_paths, payload)
        marker = json.loads((payload / "gpu-runtime.json").read_text(encoding="utf-8"))
        marker.update(
            {
                "release": manifest.release,
                "min_driver_version": manifest.min_driver_version,
                "assets": [asdict(wheel) for wheel in manifest.assets],
            }
        )
        _write_json(payload / "gpu-runtime.json", marker)
        _atomic_replace_directory(payload, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    probe = prober(target, model_dir)
    active_device = "cuda" if probe.passed else "cpu"
    diagnostic = probe.detail if probe.passed else f"CPU fallback: {probe.detail}"
    result = GpuInstallResult(
        active_device=active_device,
        installed=True,
        detection=detection,
        probe=probe,
        diagnostic=diagnostic,
    )
    _write_json(runtime_root / "gpu-runtime-status.json", asdict(result))
    for _wheel, part in wheel_paths:
        part.unlink(missing_ok=True)
    emit("complete", None, total, diagnostic)
    return result


__all__ = [
    "DEFAULT_MIN_DRIVER_VERSION",
    "GPU_MANIFEST_SCHEMA_VERSION",
    "PINNED_GPU_MANIFEST",
    "GpuDetection",
    "GpuInstallResult",
    "GpuProbe",
    "GpuRuntimeError",
    "GpuRuntimeManifest",
    "GpuWheel",
    "activate_gpu_runtime",
    "detect_nvidia_gpu",
    "install_gpu_runtime",
    "load_gpu_manifest",
    "probe_gpu_runtime",
]
