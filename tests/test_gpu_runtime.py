from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from auto_speech_journal.gpu_runtime import (
    GpuDetection,
    GpuProbe,
    GpuRuntimeError,
    GpuRuntimeManifest,
    GpuWheel,
    detect_nvidia_gpu,
    install_gpu_runtime,
    load_gpu_manifest,
)


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _wheel_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("nvidia/cublas/bin/cublas64_12.dll", b"dll-payload")
        bundle.writestr("nvidia/cublas/include/header.h", b"not-installed")
    return output.getvalue()


def test_detect_nvidia_gpu_checks_pinned_minimum_driver() -> None:
    compatible = detect_nvidia_gpu(
        min_driver_version="576.02",
        runner=lambda _command: _completed("580.10, Test GPU\n"),
        os_name="nt",
    )
    old = detect_nvidia_gpu(
        min_driver_version="576.02",
        runner=lambda _command: _completed("572.99, Test GPU\n"),
        os_name="nt",
    )

    assert compatible.available is True
    assert compatible.compatible is True
    assert compatible.gpu_names == ("Test GPU",)
    assert old.compatible is False
    assert "older" in old.reason


def test_packaging_gpu_manifest_matches_runtime_schema() -> None:
    path = Path(__file__).resolve().parents[1] / "packaging" / "manifests" / "cuda-runtime-v1.json"
    manifest = load_gpu_manifest(path)

    assert manifest.release == "cuda-runtime-v1"
    assert manifest.min_driver_version == "576.02"
    assert len(manifest.assets) == 3


def test_incompatible_gpu_uses_cpu_without_downloading(tmp_path) -> None:
    manifest = GpuRuntimeManifest(1, "cuda-test", "576.02", ())
    detection = GpuDetection(True, False, "572.99", ("GPU",), "driver too old")

    result = install_gpu_runtime(
        tmp_path,
        manifest=manifest,
        detector=lambda **_kwargs: detection,
        downloader=lambda *_args: (_ for _ in ()).throw(AssertionError("downloaded")),
    )

    assert result.active_device == "cpu"
    assert result.installed is False
    assert (tmp_path / "gpu-runtime-status.json").is_file()


def test_gpu_install_extracts_only_dlls_and_keeps_cpu_fallback(tmp_path) -> None:
    payload = _wheel_payload()
    wheel = GpuWheel(
        name="tiny-wheel",
        url="https://example.test/tiny.whl",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )
    manifest = GpuRuntimeManifest(1, "cuda-test", "1.0", (wheel,))
    detection = GpuDetection(True, True, "999.0", ("GPU",), "compatible")
    events = []

    def downloader(_wheel, destination: Path, progress) -> None:
        destination.write_bytes(payload)
        progress(len(payload), len(payload))

    result = install_gpu_runtime(
        tmp_path,
        manifest=manifest,
        detector=lambda **_kwargs: detection,
        downloader=downloader,
        prober=lambda _runtime, _model: GpuProbe(False, 0, (), "probe failed"),
        progress=events.append,
    )

    runtime = tmp_path / "gpu-runtime"
    assert result.active_device == "cpu"
    assert result.installed is True
    assert (runtime / "cublas64_12.dll").read_bytes() == b"dll-payload"
    assert not (runtime / "header.h").exists()
    assert (runtime / "gpu-runtime.json").is_file()
    assert events[-1].status == "complete"


def test_gpu_preflight_uses_manifest_installed_size(tmp_path) -> None:
    payload = _wheel_payload()
    wheel = GpuWheel(
        name="large-installed-wheel",
        url="https://example.test/large.whl",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        installed_size=100_000,
    )
    manifest = GpuRuntimeManifest(1, "cuda-test", "1.0", (wheel,))
    detection = GpuDetection(True, True, "999.0", ("GPU",), "compatible")
    DiskUsage = namedtuple("DiskUsage", "total used free")
    old_formula_space = int(len(payload) * 2 * 1.2) + 1

    with pytest.raises(GpuRuntimeError, match="insufficient disk space"):
        install_gpu_runtime(
            tmp_path,
            manifest=manifest,
            detector=lambda **_kwargs: detection,
            downloader=lambda *_args: (_ for _ in ()).throw(AssertionError("downloaded")),
            disk_usage=lambda _path: DiskUsage(200_000, 0, old_formula_space),
        )


def test_corrupt_gpu_part_is_removed_so_next_repair_succeeds(tmp_path) -> None:
    payload = _wheel_payload()
    corrupt = bytearray(payload)
    corrupt[len(corrupt) // 2] ^= 0xFF
    wheel = GpuWheel(
        name="tiny-wheel",
        url="https://example.test/tiny.whl",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )
    manifest = GpuRuntimeManifest(1, "cuda-test", "1.0", (wheel,))
    detection = GpuDetection(True, True, "999.0", ("GPU",), "compatible")
    use_valid_payload = False

    def downloader(_wheel, destination: Path, _progress) -> None:
        destination.write_bytes(payload if use_valid_payload else bytes(corrupt))

    with pytest.raises(Exception, match="SHA-256"):
        install_gpu_runtime(
            tmp_path,
            manifest=manifest,
            detector=lambda **_kwargs: detection,
            downloader=downloader,
        )
    part = tmp_path / ".downloads" / "cuda-test" / "tiny-wheel.part"
    assert not part.exists()

    use_valid_payload = True
    result = install_gpu_runtime(
        tmp_path,
        manifest=manifest,
        detector=lambda **_kwargs: detection,
        downloader=downloader,
        prober=lambda _runtime, _model: GpuProbe(False, 0, (), "probe failed"),
    )
    assert result.installed is True
    assert result.active_device == "cpu"
