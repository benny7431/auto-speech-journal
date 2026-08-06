from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from auto_speech_journal.provisioning import ProvisioningError
from auto_speech_journal.runtime_models import provision_runtime_models

ROOT = Path(__file__).resolve().parents[1]
MODEL_GATE = runpy.run_path(str(ROOT / "packaging" / "models" / "verify_runtime_models.py"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _installed_models(tmp_path: Path) -> tuple[Path, Path]:
    payload = b"ready-runtime-model"
    manifest_path = tmp_path / "runtime-models-v1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release": "runtime-models-v1",
                "provider": "huggingface",
                "models": [
                    {
                        "name": "reference-test-model",
                        "repository": "owner/ready-model",
                        "revision": "a" * 40,
                        "format": "onnx",
                        "destination": "reference-model",
                        "license": {
                            "spdx": "MIT",
                            "url": "https://example.test/license",
                        },
                        "source": {
                            "url": "https://example.test/source",
                            "description": "Ready-to-run reference fixture.",
                        },
                        "files": [
                            {
                                "path": "model.onnx",
                                "size": len(payload),
                                "sha256": _sha256_bytes(payload),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    from auto_speech_journal.runtime_models import load_runtime_model_manifest

    manifest = load_runtime_model_manifest(manifest_path)
    models_dir = tmp_path / "models"

    def downloader(_model, _file, part: Path, progress) -> None:
        part.write_bytes(payload)
        progress(len(payload), len(payload))

    provision_runtime_models(manifest, models_dir, downloader=downloader)
    return manifest_path, models_dir


def _ready_reference(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    fixture = repository / "tests" / "fixtures" / "reference.flac"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"reviewed-reference-audio")
    preview_text = "reviewed preview transcript"
    final_text = "reviewed final transcript"
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
            "repository": "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en",
            "revision": "8e40c43232a1c5c66c82111efc5820d3accca11b",
            "source_path": "test_wavs/2.wav",
            "license": "Apache-2.0",
            "source_url": (
                "https://huggingface.co/csukuangfj/"
                "sherpa-onnx-streaming-paraformer-bilingual-zh-en/blob/"
                "8e40c43232a1c5c66c82111efc5820d3accca11b/test_wavs/2.wav"
            ),
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


def test_final_gate_verifies_installed_files_and_runs_reference_inference(tmp_path: Path) -> None:
    manifest, models_dir = _installed_models(tmp_path)
    repository, reference, preview_text, final_text = _ready_reference(tmp_path)
    observed: dict[str, object] = {}

    def inference(models_root: Path, audio_path: Path, _metadata) -> dict[str, object]:
        observed["audio"] = audio_path
        observed["models"] = models_root
        assert (models_root / "reference-model" / "model.onnx").is_file()
        return {
            "preview_loaded": True,
            "vad_loaded": True,
            "preview_text": preview_text,
            "final_text": final_text,
            "final_device": "cpu",
            "final_compute_type": "int8",
        }

    result = MODEL_GATE["verify_runtime_models"](
        manifest,
        models_dir,
        reference,
        repository,
        inference_runner=inference,
    )

    assert result["release"] == "runtime-models-v1"
    assert result["models_verified"] == ["reference-test-model"]
    assert result["final_device"] == "cpu"
    assert Path(observed["audio"]).is_file()
    assert observed["models"] == models_dir


def test_missing_reference_fixture_is_an_explicit_hard_block(tmp_path: Path) -> None:
    reference = tmp_path / "blocked-reference.json"
    reference.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "blocked",
                "reason": "review has not completed",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="reference audio gate is blocked: review has not completed",
    ):
        MODEL_GATE["_load_reference_gate"](reference, tmp_path)


def test_committed_reference_fixture_has_pinned_provenance_and_digest() -> None:
    reference = ROOT / "packaging" / "models" / "reference-audio-gate.json"
    audio_path, metadata = MODEL_GATE["_load_reference_gate"](reference, ROOT)
    audio = metadata["audio"]

    assert audio["repository"] == (
        "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
    )
    assert audio["revision"] == "8e40c43232a1c5c66c82111efc5820d3accca11b"
    assert audio["source_path"] == "test_wavs/2.wav"
    assert audio["license"] == "Apache-2.0"
    assert audio["revision"] in audio["source_url"]
    assert _sha256_bytes(audio_path.read_bytes()) == audio["sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda audio: audio.__setitem__("repository", "invalid-repository"),
        lambda audio: audio.__setitem__("revision", "main"),
        lambda audio: audio.__setitem__("license", "UNKNOWN"),
        lambda audio: audio.__setitem__("source_url", "https://example.test/floating"),
        lambda audio: (
            audio.__setitem__("source_path", "test_wavs/../private.wav"),
            audio.__setitem__(
                "source_url",
                "https://huggingface.co/"
                f"{audio['repository']}/blob/{audio['revision']}/"
                "test_wavs/../private.wav",
            ),
        ),
    ],
)
def test_reference_gate_rejects_unpinned_or_unsafe_provenance(
    tmp_path: Path,
    mutation,
) -> None:
    canonical = json.loads(
        (ROOT / "packaging" / "models" / "reference-audio-gate.json").read_text(
            encoding="utf-8"
        )
    )
    mutation(canonical["audio"])
    reference = tmp_path / "mutated-reference.json"
    reference.write_text(json.dumps(canonical), encoding="utf-8")

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="provenance metadata is invalid",
    ):
        MODEL_GATE["_load_reference_gate"](reference, ROOT)


def test_reference_gate_rejects_audio_hash_mismatch(tmp_path: Path) -> None:
    canonical = json.loads(
        (ROOT / "packaging" / "models" / "reference-audio-gate.json").read_text(
            encoding="utf-8"
        )
    )
    canonical["audio"]["sha256"] = "0" * 64
    reference = tmp_path / "wrong-hash-reference.json"
    reference.write_text(json.dumps(canonical), encoding="utf-8")

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="audio SHA-256 does not match",
    ):
        MODEL_GATE["_load_reference_gate"](reference, ROOT)


def test_final_gate_rejects_corrupt_model_before_inference(tmp_path: Path) -> None:
    manifest, models_dir = _installed_models(tmp_path)
    repository, reference, _preview_text, _final_text = _ready_reference(tmp_path)
    (models_dir / "reference-model" / "model.onnx").write_bytes(b"corrupt")

    with pytest.raises(ProvisioningError, match="wrong size"):
        MODEL_GATE["verify_runtime_models"](
            manifest,
            models_dir,
            reference,
            repository,
            inference_runner=lambda *_args: pytest.fail("corrupt model ran inference"),
        )


def test_final_gate_rejects_reference_transcript_mismatch(tmp_path: Path) -> None:
    manifest, models_dir = _installed_models(tmp_path)
    repository, reference, _preview_text, final_text = _ready_reference(tmp_path)

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="reference preview_text mismatch",
    ):
        MODEL_GATE["verify_runtime_models"](
            manifest,
            models_dir,
            reference,
            repository,
            inference_runner=lambda *_args: {
                "preview_loaded": True,
                "vad_loaded": True,
                "preview_text": "unreviewed transcript",
                "final_text": final_text,
                "final_device": "cpu",
                "final_compute_type": "int8",
            },
        )


def test_final_gate_requires_cpu_fallback_result(tmp_path: Path) -> None:
    manifest, models_dir = _installed_models(tmp_path)
    repository, reference, preview_text, final_text = _ready_reference(tmp_path)

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="must prove CPU fallback",
    ):
        MODEL_GATE["verify_runtime_models"](
            manifest,
            models_dir,
            reference,
            repository,
            inference_runner=lambda *_args: {
                "preview_loaded": True,
                "vad_loaded": True,
                "preview_text": preview_text,
                "final_text": final_text,
                "final_device": "cuda",
                "final_compute_type": "float16",
            },
        )


@pytest.mark.parametrize("relative", ["../escape.flac", "/absolute.flac"])
def test_reference_audio_path_cannot_escape_repository(tmp_path: Path, relative: str) -> None:
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "audio": {"path": relative},
                "expected": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="unsafe reference destination|escapes repository root",
    ):
        MODEL_GATE["_load_reference_gate"](reference, tmp_path)


def test_reference_gate_keeps_real_preview_vad_and_cpu_whisper_probe() -> None:
    source = (
        ROOT / "packaging" / "models" / "verify_runtime_models.py"
    ).read_text(encoding="utf-8")

    assert "probe_realtime_models" in source
    assert "SherpaPreviewEngine" in source
    assert "FasterWhisperFinalizer" in source
    assert "prefer_cuda=False" in source
    assert "final_device\") != \"cpu\"" in source
