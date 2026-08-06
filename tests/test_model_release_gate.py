from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from auto_speech_journal.model_download import default_runtime_model_manifest

ROOT = Path(__file__).resolve().parents[1]
MODEL_GATE = runpy.run_path(str(ROOT / "packaging" / "models" / "verify_runtime_models.py"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
            "repository": PREVIEW_REPOSITORY,
            "revision": PREVIEW_REVISION,
            "source_path": "test_wavs/2.wav",
            "license": "Apache-2.0",
            "source_url": (
                f"https://huggingface.co/{PREVIEW_REPOSITORY}/blob/"
                f"{PREVIEW_REVISION}/test_wavs/2.wav"
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


PREVIEW_REPOSITORY = "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
PREVIEW_REVISION = "8e40c43232a1c5c66c82111efc5820d3accca11b"


def test_reference_gate_runs_preview_vad_and_cpu_whisper_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, reference, preview_text, final_text = _ready_reference(tmp_path)
    monkeypatch.setitem(
        MODEL_GATE["verify_runtime_models"].__globals__,
        "verify_installed_runtime_models",
        lambda *_args: None,
    )

    result = MODEL_GATE["verify_runtime_models"](
        default_runtime_model_manifest(),
        tmp_path / "models",
        reference,
        repository,
        inference_runner=lambda *_args: {
            "preview_loaded": True,
            "vad_loaded": True,
            "preview_text": preview_text,
            "final_text": final_text,
            "final_device": "cpu",
            "final_compute_type": "int8",
        },
    )

    assert result["release"] == "runtime-models-v1"
    assert result["models_verified"] == [
        "paraformer-preview",
        "silero-vad",
        "whisper-large-v3-turbo",
    ]
    assert result["final_device"] == "cpu"


def test_committed_reference_fixture_has_pinned_provenance_and_digest() -> None:
    reference = ROOT / "packaging" / "models" / "reference-audio-gate.json"
    audio_path, metadata = MODEL_GATE["_load_reference_gate"](reference, ROOT)
    audio = metadata["audio"]

    assert audio["repository"] == PREVIEW_REPOSITORY
    assert audio["revision"] == PREVIEW_REVISION
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
    ],
)
def test_reference_gate_rejects_unpinned_provenance(
    tmp_path: Path,
    mutation,
) -> None:
    payload = json.loads(
        (ROOT / "packaging" / "models" / "reference-audio-gate.json").read_text(
            encoding="utf-8"
        )
    )
    mutation(payload["audio"])
    reference = tmp_path / "mutated-reference.json"
    reference.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="provenance metadata is invalid",
    ):
        MODEL_GATE["_load_reference_gate"](reference, ROOT)


def test_reference_gate_rejects_audio_hash_mismatch(tmp_path: Path) -> None:
    payload = json.loads(
        (ROOT / "packaging" / "models" / "reference-audio-gate.json").read_text(
            encoding="utf-8"
        )
    )
    payload["audio"]["sha256"] = "0" * 64
    reference = tmp_path / "wrong-hash-reference.json"
    reference.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="audio SHA-256 does not match",
    ):
        MODEL_GATE["_load_reference_gate"](reference, ROOT)


def test_reference_gate_rejects_transcript_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, reference, _preview_text, final_text = _ready_reference(tmp_path)
    monkeypatch.setitem(
        MODEL_GATE["verify_runtime_models"].__globals__,
        "verify_installed_runtime_models",
        lambda *_args: None,
    )

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="reference preview_text mismatch",
    ):
        MODEL_GATE["verify_runtime_models"](
            default_runtime_model_manifest(),
            tmp_path / "models",
            reference,
            repository,
            inference_runner=lambda *_args: {
                "preview_loaded": True,
                "vad_loaded": True,
                "preview_text": "unreviewed transcript",
                "final_text": final_text,
                "final_device": "cpu",
            },
        )


def test_reference_gate_requires_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, reference, preview_text, final_text = _ready_reference(tmp_path)
    monkeypatch.setitem(
        MODEL_GATE["verify_runtime_models"].__globals__,
        "verify_installed_runtime_models",
        lambda *_args: None,
    )

    with pytest.raises(
        MODEL_GATE["RuntimeModelVerificationError"],
        match="must prove CPU fallback",
    ):
        MODEL_GATE["verify_runtime_models"](
            default_runtime_model_manifest(),
            tmp_path / "models",
            reference,
            repository,
            inference_runner=lambda *_args: {
                "preview_loaded": True,
                "vad_loaded": True,
                "preview_text": preview_text,
                "final_text": final_text,
                "final_device": "cuda",
            },
        )


def test_reference_inference_implementation_keeps_real_engines() -> None:
    source = (
        ROOT / "packaging" / "models" / "verify_runtime_models.py"
    ).read_text(encoding="utf-8")

    assert "probe_realtime_models" in source
    assert "SherpaPreviewEngine" in source
    assert "FasterWhisperFinalizer" in source
    assert "prefer_cuda=False" in source
