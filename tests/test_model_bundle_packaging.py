from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "packaging" / "models" / "build_model_bundle.py"
SPEC = importlib.util.spec_from_file_location("asj_model_bundle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
model_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model_bundle)
VALIDATOR_PATH = ROOT / "packaging" / "models" / "validate_model_manifest.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location("asj_model_validator", VALIDATOR_PATH)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
model_validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(model_validator)


def _write_files(root: Path, names: tuple[str, ...], prefix: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        (root / name).write_bytes(prefix + bytes([index]))


def _fixture_tree(tmp_path: Path) -> tuple[Path, Path]:
    models = tmp_path / "models"
    preview = models / model_bundle.PREVIEW_DIRECTORY
    whisper = models / model_bundle.WHISPER_DIRECTORY
    _write_files(preview, (*model_bundle.PREVIEW_FILES, ".model-manifest.json"), b"preview")
    _write_files(whisper, (*model_bundle.WHISPER_FILES, ".model-manifest.json"), b"whisper")
    (preview / "encoder.onnx").write_bytes(b"prohibited-full-precision")
    (preview / "decoder.onnx").write_bytes(b"prohibited-full-precision")
    vad = models / model_bundle.VAD_PATH
    vad.parent.mkdir(parents=True)
    vad.write_bytes(b"vad")

    licenses = tmp_path / "licenses"
    _write_files(
        licenses,
        ("paraformer-LICENSE.txt", "silero-vad-LICENSE.txt", "whisper-LICENSE.txt"),
        b"license",
    )
    return models, licenses


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_model_bundle_is_trimmed_deterministic_and_fully_manifested(tmp_path: Path) -> None:
    models, licenses = _fixture_tree(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = model_bundle.build_bundle(
        models, licenses, first, "https://example.test/releases/download/models-v1"
    )
    model_bundle.build_bundle(
        models, licenses, second, "https://example.test/releases/download/models-v1"
    )

    for name in (
        "models-v1-paraformer-int8.zip",
        "models-v1-whisper-large-v3-turbo-float16.zip",
        "models-v1-licenses.zip",
    ):
        assert _sha256(first / name) == _sha256(second / name)

    with zipfile.ZipFile(first / "models-v1-paraformer-int8.zip") as archive:
        assert set(archive.namelist()) == {*model_bundle.PREVIEW_FILES, ".model-manifest.json"}
        assert "encoder.onnx" not in archive.namelist()
        assert "decoder.onnx" not in archive.namelist()
    with zipfile.ZipFile(first / "models-v1-whisper-large-v3-turbo-float16.zip") as archive:
        assert set(archive.namelist()) == {*model_bundle.WHISPER_FILES, ".model-manifest.json"}
    with zipfile.ZipFile(first / "models-v1-licenses.zip") as archive:
        assert set(archive.namelist()) == {
            "MODEL-PROVENANCE.json",
            "paraformer-LICENSE.txt",
            "silero-vad-LICENSE.txt",
            "whisper-LICENSE.txt",
        }
        provenance = json.loads(archive.read("MODEL-PROVENANCE.json"))
        assert provenance["release"] == "models-v1"
        assert provenance["transforms"]["preview"].startswith("retained encoder.int8")

    manifest = json.loads(first_manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["release"] == "models-v1"
    assert len(manifest["assets"]) == 4
    for asset in manifest["assets"]:
        artifact = first / asset["name"]
        assert asset["size"] == artifact.stat().st_size
        assert asset["sha256"] == _sha256(artifact)
        assert asset["size"] < model_bundle.GITHUB_ASSET_LIMIT
        for required in asset["required_files"]:
            assert required["size"] >= 0
            assert len(required["sha256"]) == 64


def test_model_bundle_rejects_asset_at_github_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "asset.zip"
    artifact.write_bytes(b"xx")
    monkeypatch.setattr(model_bundle, "GITHUB_ASSET_LIMIT", 2)
    with pytest.raises(ValueError, match="2 GiB"):
        model_bundle.asset_record(
            artifact,
            base_url="https://example.test/models-v1",
            destination="model",
            archive="zip",
            installed_size=2,
            required=[],
        )


def test_release_model_manifest_requires_exact_inventory_url_and_pinned_digest(
    tmp_path: Path,
) -> None:
    models, licenses = _fixture_tree(tmp_path)
    output = tmp_path / "release"
    manifest = model_bundle.build_bundle(
        models,
        licenses,
        output,
        "https://github.com/benny7431/auto-speech-journal/releases/download/models-v1",
    )
    pin = tmp_path / "models-v1.sha256"
    pin.write_text(_sha256(manifest) + "\n", encoding="ascii")

    validated = model_validator.validate_model_manifest(
        manifest,
        expected_sha256_file=pin,
    )

    assert validated.release == "models-v1"
    pin.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(ValueError, match="not pinned"):
        model_validator.validate_model_manifest(manifest, expected_sha256_file=pin)
