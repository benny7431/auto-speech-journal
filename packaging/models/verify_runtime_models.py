"""Verify pinned Hugging Face runtime models and the reviewed reference transcript."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from auto_speech_journal.model_download import (
    ModelVerificationError,
    load_runtime_model_manifest,
    verify_installed_runtime_models,
)

PREVIEW_DIRECTORY = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
WHISPER_DIRECTORY = "faster-whisper-large-v3-turbo"


class RuntimeModelVerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_transcript(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split()).strip()


def transcript_sha256(value: str) -> str:
    return hashlib.sha256(normalize_transcript(value).encode("utf-8")).hexdigest()


def _safe_destination(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    unsafe = (
        not parts
        or PurePosixPath(relative).is_absolute()
        or any(part in {"", ".."} for part in parts)
    )
    if unsafe:
        raise RuntimeModelVerificationError(f"unsafe reference destination: {relative!r}")
    destination = root.joinpath(*parts).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise RuntimeModelVerificationError(
            f"reference destination escapes repository root: {relative!r}"
        )
    return destination


def _load_reference_gate(
    reference_spec_path: Path,
    repository_root: Path,
) -> tuple[Path, Mapping[str, Any]]:
    try:
        payload = json.loads(reference_spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeModelVerificationError(
            "reference audio gate metadata is unreadable"
        ) from error
    if not isinstance(payload, Mapping) or int(payload.get("schema_version", 0)) != 1:
        raise RuntimeModelVerificationError("reference audio gate schema_version must be 1")
    if str(payload.get("status", "")) != "ready":
        reason = str(payload.get("reason", "no reviewed reference fixture is configured"))
        raise RuntimeModelVerificationError(f"reference audio gate is blocked: {reason}")

    audio = payload.get("audio")
    expected = payload.get("expected")
    if not isinstance(audio, Mapping) or not isinstance(expected, Mapping):
        raise RuntimeModelVerificationError(
            "ready reference gate requires audio and expected objects"
        )
    audio_path = _safe_destination(repository_root.resolve(), str(audio.get("path", "")))
    if not audio_path.is_file():
        raise RuntimeModelVerificationError(f"reference audio fixture is missing: {audio_path}")
    expected_audio_hash = str(audio.get("sha256", "")).casefold()
    if len(expected_audio_hash) != 64 or sha256_file(audio_path) != expected_audio_hash:
        raise RuntimeModelVerificationError("reference audio SHA-256 does not match metadata")
    repository = str(audio.get("repository", ""))
    revision = str(audio.get("revision", ""))
    raw_source_path = str(audio.get("source_path", "")).replace("\\", "/")
    parsed_source_path = PurePosixPath(raw_source_path)
    source_path = parsed_source_path.as_posix()
    source_url = str(audio.get("source_url", ""))
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository)
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or parsed_source_path.is_absolute()
        or not parsed_source_path.parts
        or raw_source_path != source_path
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
            for part in parsed_source_path.parts
        )
        or str(audio.get("license", "")) != "Apache-2.0"
        or source_url
        != f"https://huggingface.co/{repository}/blob/{revision}/{source_path}"
    ):
        raise RuntimeModelVerificationError("reference audio provenance metadata is invalid")

    for key in ("preview_text", "final_text"):
        text = normalize_transcript(str(expected.get(key, "")))
        digest = str(expected.get(f"{key}_sha256", "")).casefold()
        if not text or len(digest) != 64 or transcript_sha256(text) != digest:
            raise RuntimeModelVerificationError(f"reference expected {key} metadata is invalid")
    return audio_path, payload


def run_reference_inference(
    models_root: Path,
    audio_path: Path,
    reference: Mapping[str, Any],
) -> dict[str, object]:
    """Run CPU Preview, VAD, and final transcription from installed runtime files."""

    import numpy as np
    import opencc
    import soundfile as sf

    from auto_speech_journal.config import AppConfig
    from auto_speech_journal.finalizer_engine import FasterWhisperFinalizer
    from auto_speech_journal.preview_engine import SherpaPreviewEngine
    from auto_speech_journal.workers import probe_realtime_models

    audio_metadata = reference["audio"]
    if not isinstance(audio_metadata, Mapping):
        raise RuntimeModelVerificationError("reference audio metadata is invalid")
    samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    channels = int(samples.shape[1])
    expected_rate = int(audio_metadata.get("sample_rate", 0))
    expected_channels = int(audio_metadata.get("channels", 0))
    if sample_rate != expected_rate or channels != expected_channels:
        raise RuntimeModelVerificationError(
            f"reference audio format mismatch: {sample_rate} Hz/{channels} channels"
        )
    if expected_rate != 16_000 or expected_channels != 1:
        raise RuntimeModelVerificationError("reference audio must be 16 kHz mono")
    duration_ms = round(len(samples) * 1000 / sample_rate)
    minimum = int(audio_metadata.get("minimum_duration_ms", 1))
    maximum = int(audio_metadata.get("maximum_duration_ms", 0))
    if duration_ms < minimum or maximum <= 0 or duration_ms > maximum:
        raise RuntimeModelVerificationError(
            f"reference audio duration {duration_ms} ms is outside [{minimum}, {maximum}]"
        )
    mono = np.ascontiguousarray(samples[:, 0], dtype=np.float32)

    config = AppConfig()
    realtime = probe_realtime_models(config, models_root)
    preview = SherpaPreviewEngine(
        models_root / PREVIEW_DIRECTORY,
        sample_rate=sample_rate,
        provider="cpu",
    )
    preview_text = ""
    try:
        chunk_size = sample_rate // 4
        for offset in range(0, len(mono), chunk_size):
            hypothesis = preview.accept(mono[offset : offset + chunk_size], sample_rate=sample_rate)
            preview_text = hypothesis.normalized_text or preview_text
        finished = preview.finish()
        preview_text = finished.normalized_text or preview_text
    finally:
        preview.close()

    finalizer = FasterWhisperFinalizer(
        models_root / WHISPER_DIRECTORY,
        language="zh",
        prefer_cuda=False,
        cpu_compute_type=config.model.cpu_compute_type,
        deadline_ms=120_000,
    )
    try:
        final_probe = finalizer.probe(audio_path)
    finally:
        finalizer.close()
    converter = opencc.OpenCC("s2tw")
    final_text = converter.convert(final_probe.text).strip()
    return {
        "preview_loaded": realtime.preview_loaded,
        "vad_loaded": realtime.vad_loaded,
        "preview_text": normalize_transcript(preview_text),
        "final_text": normalize_transcript(final_text),
        "final_device": final_probe.active_device,
        "final_compute_type": final_probe.compute_type,
        "duration_ms": duration_ms,
    }


InferenceRunner = Callable[[Path, Path, Mapping[str, Any]], Mapping[str, Any]]


def verify_runtime_models(
    manifest_path: Path,
    models_dir: Path,
    reference_spec_path: Path,
    repository_root: Path,
    *,
    inference_runner: InferenceRunner = run_reference_inference,
) -> dict[str, object]:
    manifest = load_runtime_model_manifest(manifest_path)
    if manifest.release != "runtime-models-v1":
        raise RuntimeModelVerificationError("reference gate requires runtime-models-v1")
    verify_installed_runtime_models(manifest, models_dir)
    audio_path, reference = _load_reference_gate(reference_spec_path, repository_root)
    inference = dict(inference_runner(models_dir, audio_path, reference))

    expected = reference["expected"]
    if not isinstance(expected, Mapping):
        raise RuntimeModelVerificationError("reference expected metadata is invalid")
    for key in ("preview_text", "final_text"):
        actual = normalize_transcript(str(inference.get(key, "")))
        wanted = normalize_transcript(str(expected.get(key, "")))
        wanted_hash = str(expected.get(f"{key}_sha256", "")).casefold()
        if not actual or actual != wanted or transcript_sha256(actual) != wanted_hash:
            raise RuntimeModelVerificationError(
                f"reference {key} mismatch: expected hash {wanted_hash}, "
                f"got {transcript_sha256(actual)}"
            )
    if not inference.get("preview_loaded") or not inference.get("vad_loaded"):
        raise RuntimeModelVerificationError("Preview/VAD reference inference did not load")
    if inference.get("final_device") != "cpu":
        raise RuntimeModelVerificationError("reference final inference must prove CPU fallback")

    return {
        "schema_version": 1,
        "release": manifest.release,
        "models_verified": sorted(model.name for model in manifest.models),
        "reference_audio_sha256": sha256_file(audio_path),
        "preview_text_sha256": transcript_sha256(str(inference["preview_text"])),
        "final_text_sha256": transcript_sha256(str(inference["final_text"])),
        "final_device": inference["final_device"],
        "final_compute_type": inference.get("final_compute_type"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--models-dir", required=True, type=Path)
    parser.add_argument("--reference-spec", required=True, type=Path)
    parser.add_argument("--repository-root", default=Path.cwd(), type=Path)
    args = parser.parse_args(argv)
    result = verify_runtime_models(
        args.manifest.resolve(),
        args.models_dir.resolve(),
        args.reference_spec.resolve(),
        args.repository_root.resolve(),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeModelVerificationError, ModelVerificationError, ValueError, OSError) as error:
        print(f"runtime model reference gate failed: {error}", file=os.sys.stderr)
        raise SystemExit(1) from error
