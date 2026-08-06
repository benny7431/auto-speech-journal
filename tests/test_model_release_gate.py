from __future__ import annotations

import hashlib
import json
import runpy
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL_BUNDLE = runpy.run_path(str(ROOT / "packaging" / "models" / "build_model_bundle.py"))
MODEL_GATE = runpy.run_path(
    str(ROOT / "packaging" / "models" / "verify_model_release_assets.py")
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_files(root: Path, names: tuple[str, ...], prefix: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        (root / name).write_bytes(prefix + bytes([index]))


def _build_fake_release(tmp_path: Path) -> tuple[Path, Path]:
    models = tmp_path / "source-models"
    preview = models / MODEL_BUNDLE["PREVIEW_DIRECTORY"]
    whisper = models / MODEL_BUNDLE["WHISPER_DIRECTORY"]
    _write_files(
        preview,
        (*MODEL_BUNDLE["PREVIEW_FILES"], ".model-manifest.json"),
        b"preview",
    )
    _write_files(
        whisper,
        (*MODEL_BUNDLE["WHISPER_FILES"], ".model-manifest.json"),
        b"whisper",
    )
    vad = models / MODEL_BUNDLE["VAD_PATH"]
    vad.parent.mkdir(parents=True)
    vad.write_bytes(b"vad")
    licenses = tmp_path / "licenses"
    _write_files(
        licenses,
        ("paraformer-LICENSE.txt", "silero-vad-LICENSE.txt", "whisper-LICENSE.txt"),
        b"license",
    )
    assets = tmp_path / "assets"
    manifest = MODEL_BUNDLE["build_bundle"](
        models,
        licenses,
        assets,
        "https://github.com/benny7431/auto-speech-journal/releases/download/models-v1",
    )
    return assets, manifest


def _ready_reference(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    fixture = repository / "tests" / "fixtures" / "reference.flac"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"reviewed-reference-audio")
    preview_text = "測試預覽文字"
    final_text = "測試最終文字"
    metadata = {
        "schema_version": 1,
        "status": "ready",
        "audio": {
            "path": "tests/fixtures/reference.flac",
            "sha256": _sha256_bytes(fixture.read_bytes()),
            "sample_rate": 16000,
            "channels": 1,
            "minimum_duration_ms": 1000,
            "maximum_duration_ms": 30000,
        },
        "expected": {
            "preview_text": preview_text,
            "preview_text_sha256": MODEL_GATE["transcript_sha256"](preview_text),
            "final_text": final_text,
            "final_text_sha256": MODEL_GATE["transcript_sha256"](final_text),
        },
    }
    reference = repository / "reference-audio-gate.json"
    reference.write_text(json.dumps(metadata), encoding="utf-8")
    return repository, reference, preview_text, final_text


def test_final_gate_reextracts_all_assets_and_runs_reference_inference(tmp_path: Path) -> None:
    assets, manifest = _build_fake_release(tmp_path)
    repository, reference, preview_text, final_text = _ready_reference(tmp_path)
    observed: dict[str, object] = {}

    def inference(models_root: Path, audio_path: Path, _metadata) -> dict[str, object]:
        observed["audio"] = audio_path
        observed["models"] = models_root
        assert (models_root / MODEL_BUNDLE["PREVIEW_DIRECTORY"] / "encoder.int8.onnx").is_file()
        assert (models_root / MODEL_BUNDLE["WHISPER_DIRECTORY"] / "model.bin").is_file()
        assert (models_root / MODEL_BUNDLE["VAD_PATH"]).is_file()
        assert (models_root / "licenses" / "models-v1" / "MODEL-PROVENANCE.json").is_file()
        return {
            "preview_loaded": True,
            "vad_loaded": True,
            "preview_text": preview_text,
            "final_text": final_text,
            "final_device": "cpu",
            "final_compute_type": "int8",
        }

    result = MODEL_GATE["verify_model_release_assets"](
        manifest,
        assets,
        reference,
        repository,
        inference_runner=inference,
        work_dir=tmp_path,
    )

    assert result["release"] == "models-v1"
    assert result["final_device"] == "cpu"
    assert len(result["assets_verified"]) == 4
    assert Path(observed["audio"]).is_file()
    assert not Path(observed["models"]).exists()


def test_missing_reference_fixture_is_an_explicit_hard_block(tmp_path: Path) -> None:
    assets, manifest = _build_fake_release(tmp_path)
    reference = ROOT / "packaging" / "models" / "reference-audio-gate.json"

    with pytest.raises(
        MODEL_GATE["ReleaseAssetVerificationError"],
        match="reference audio gate is blocked",
    ):
        MODEL_GATE["verify_model_release_assets"](
            manifest,
            assets,
            reference,
            ROOT,
            inference_runner=lambda *_args: pytest.fail("blocked gate ran inference"),
            work_dir=tmp_path,
        )


def test_final_gate_rejects_corrupt_release_asset_before_inference(tmp_path: Path) -> None:
    assets, manifest = _build_fake_release(tmp_path)
    repository, reference, _preview_text, _final_text = _ready_reference(tmp_path)
    corrupt = assets / "models-v1-paraformer-int8.zip"
    corrupt.write_bytes(corrupt.read_bytes() + b"corrupt")

    with pytest.raises(MODEL_GATE["ProvisioningError"], match="wrong size"):
        MODEL_GATE["verify_model_release_assets"](
            manifest,
            assets,
            reference,
            repository,
            inference_runner=lambda *_args: pytest.fail("corrupt assets ran inference"),
            work_dir=tmp_path,
        )


def test_final_gate_rejects_reference_transcript_mismatch(tmp_path: Path) -> None:
    assets, manifest = _build_fake_release(tmp_path)
    repository, reference, _preview_text, final_text = _ready_reference(tmp_path)

    with pytest.raises(
        MODEL_GATE["ReleaseAssetVerificationError"],
        match="reference preview_text mismatch",
    ):
        MODEL_GATE["verify_model_release_assets"](
            manifest,
            assets,
            reference,
            repository,
            inference_runner=lambda *_args: {
                "preview_loaded": True,
                "vad_loaded": True,
                "preview_text": "錯誤預覽",
                "final_text": final_text,
                "final_device": "cpu",
                "final_compute_type": "int8",
            },
            work_dir=tmp_path,
        )


@pytest.mark.parametrize("member", ["../escape/", "../escape.bin"])
def test_exact_zip_extraction_rejects_traversal_in_files_and_directories(
    tmp_path: Path,
    member: str,
) -> None:
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"payload")

    with pytest.raises(
        MODEL_GATE["ReleaseAssetVerificationError"],
        match="unsafe ZIP member",
    ):
        MODEL_GATE["_extract_exact_zip"](archive_path, tmp_path / "output", ())
