from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CUDA_VALIDATOR = runpy.run_path(
    str(ROOT / "packaging" / "manifests" / "validate_cuda_manifest.py")
)
RUNTIME_INVENTORY = runpy.run_path(
    str(ROOT / "packaging" / "windows" / "runtime_inventory.py")
)


def test_repository_cuda_manifest_exactly_matches_uv_lock() -> None:
    result = CUDA_VALIDATOR["validate_cuda_manifest"](
        ROOT / "packaging" / "manifests" / "cuda-runtime-v1.json",
        ROOT / "uv.lock",
    )

    assert result["release"] == "cuda-runtime-v1"
    assert len(result["packages"]) == 3
    assert {item["package"] for item in result["packages"]} == {
        "nvidia-cublas-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cudnn-cu12",
    }


@pytest.mark.parametrize("field", ["url", "sha256", "size"])
def test_cuda_manifest_rejects_any_wheel_drift(tmp_path: Path, field: str) -> None:
    source = ROOT / "packaging" / "manifests" / "cuda-runtime-v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    asset = payload["assets"][0]
    asset[field] = asset[field] + 1 if field == "size" else str(asset[field]) + "-drift"
    manifest = tmp_path / "cuda.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from uv.lock"):
        CUDA_VALIDATOR["validate_cuda_manifest"](manifest, ROOT / "uv.lock")


def _runtime_fixture(tmp_path: Path) -> tuple[Path, Path]:
    lock = RUNTIME_INVENTORY["tomllib"].loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions, direct, _forbidden = RUNTIME_INVENTORY["locked_runtime_inventory"](lock)
    project_version = "0.2.0"
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {
                "type": "application",
                "name": "auto-speech-journal",
                "version": project_version,
            }
        },
        "components": [
            {"type": "library", "name": name, "version": version}
            for name, version in sorted(versions.items())
        ],
    }
    inventory = {
        "schema_version": 1,
        "project": {"name": "auto-speech-journal", "version": project_version},
        "analysis_entries": sorted(direct),
        "modules": [],
        "distributions": [
            {"name": "auto-speech-journal", "version": project_version},
            *(
                {"name": name, "version": versions[name]}
                for name in sorted(direct)
            ),
        ],
    }
    sbom_path = tmp_path / "runtime.cdx.json"
    inventory_path = tmp_path / "frozen-runtime-inventory.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return sbom_path, inventory_path


def test_sbom_matches_locked_runtime_and_frozen_direct_dependencies(tmp_path: Path) -> None:
    sbom, inventory = _runtime_fixture(tmp_path)

    result = RUNTIME_INVENTORY["validate_runtime_inventory"](
        sbom,
        inventory,
        ROOT / "uv.lock",
        ROOT / "pyproject.toml",
    )

    assert result["project"] == {"name": "auto-speech-journal", "version": "0.2.0"}
    assert result["sbom_component_count"] == 37
    assert "faster-whisper" in result["direct_runtime"]


def test_sbom_rejects_dev_cuda_and_model_build_components(tmp_path: Path) -> None:
    sbom, inventory = _runtime_fixture(tmp_path)
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    payload["components"].append(
        {"type": "library", "name": "nvidia-cudnn-cu12", "version": "9.24.0.43"}
    )
    sbom.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="runtime closure mismatch"):
        RUNTIME_INVENTORY["validate_runtime_inventory"](
            sbom,
            inventory,
            ROOT / "uv.lock",
            ROOT / "pyproject.toml",
        )


def test_frozen_inventory_rejects_missing_direct_runtime_package(tmp_path: Path) -> None:
    sbom, inventory = _runtime_fixture(tmp_path)
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["distributions"] = [
        item for item in payload["distributions"] if item["name"] != "faster-whisper"
    ]
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing direct runtime packages"):
        RUNTIME_INVENTORY["validate_runtime_inventory"](
            sbom,
            inventory,
            ROOT / "uv.lock",
            ROOT / "pyproject.toml",
        )


def test_inventory_writer_maps_pyinstaller_modules_to_distributions(tmp_path: Path) -> None:
    destination = tmp_path / "inventory.json"
    result = RUNTIME_INVENTORY["write_frozen_inventory"](
        [
            "auto_speech_journal.cli",
            "faster_whisper.transcribe",
            "PySide6/QtCore.pyd",
            "encodings.utf_8",
        ],
        destination,
        project_name="auto-speech-journal",
        project_version="0.2.0",
    )

    distributions = {item["name"] for item in result["distributions"]}
    assert {"auto-speech-journal", "faster-whisper", "pyside6"} <= distributions
    assert "PySide6/QtCore.pyd" in result["analysis_entries"]
    assert json.loads(destination.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    "entry",
    [
        "torch._C",
        "_internal/nvidia/cudnn/bin/cudnn64_9.dll",
        "_internal/models/preview.onnx",
        "_internal/transformers-4.0.dist-info/METADATA",
    ],
)
def test_inventory_writer_rejects_forbidden_payload_entries(
    tmp_path: Path,
    entry: str,
) -> None:
    with pytest.raises(ValueError, match="forbidden|model artifact|NVIDIA runtime"):
        RUNTIME_INVENTORY["write_frozen_inventory"](
            ["auto_speech_journal.cli", entry],
            tmp_path / "inventory.json",
            project_name="auto-speech-journal",
            project_version="0.2.0",
        )


def test_payload_scan_rejects_forbidden_native_runtime(tmp_path: Path) -> None:
    sbom, inventory = _runtime_fixture(tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "AutoSpeechJournal.exe").write_bytes(b"gui")
    (payload / "AutoSpeechJournal.CLI.exe").write_bytes(b"cli")
    leaked = payload / "_internal" / "nvidia" / "cudnn" / "bin" / "cudnn64_9.dll"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"forbidden")

    with pytest.raises(ValueError, match="forbidden package|NVIDIA runtime"):
        RUNTIME_INVENTORY["validate_runtime_inventory"](
            sbom,
            inventory,
            ROOT / "uv.lock",
            ROOT / "pyproject.toml",
            payload,
        )
