"""Rebuild and infer from the final models-v1 release assets before publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from auto_speech_journal.provisioning import (
    ProvisionAsset,
    ProvisioningError,
    RequiredFile,
    load_manifest,
    verify_file,
)

EXPECTED_ASSETS = frozenset(
    {
        "models-v1-licenses.zip",
        "models-v1-paraformer-int8.zip",
        "models-v1-silero-vad.onnx",
        "models-v1-whisper-large-v3-turbo-float16.zip",
    }
)
PREVIEW_DIRECTORY = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
WHISPER_DIRECTORY = "faster-whisper-large-v3-turbo"
VAD_PATH = "silero-vad/silero_vad.onnx"


class ReleaseAssetVerificationError(RuntimeError):
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
        raise ReleaseAssetVerificationError(f"unsafe release destination: {relative!r}")
    destination = root.joinpath(*parts).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ReleaseAssetVerificationError(
            f"release destination escapes staging root: {relative!r}"
        )
    return destination


def _verify_required(root: Path, required_files: tuple[RequiredFile, ...]) -> None:
    for required in required_files:
        path = _safe_destination(root, required.path)
        verify_file(path, size=required.size, sha256=required.sha256)


def _extract_exact_zip(
    archive_path: Path,
    destination: Path,
    required_files: tuple[RequiredFile, ...],
) -> None:
    expected = {PurePosixPath(item.path).as_posix() for item in required_files}
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        actual = set()
        for member in archive.infolist():
            member_path = PurePosixPath(member.filename)
            if member_path.is_absolute() or any(part in {"", ".."} for part in member_path.parts):
                raise ReleaseAssetVerificationError(f"unsafe ZIP member: {member.filename!r}")
            if member.is_dir():
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ReleaseAssetVerificationError(f"ZIP links are forbidden: {member.filename!r}")
            target = destination.joinpath(*member_path.parts).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ReleaseAssetVerificationError(
                    f"ZIP member escapes staging: {member.filename!r}"
                )
            normalized_name = member_path.as_posix()
            if normalized_name in actual:
                raise ReleaseAssetVerificationError(
                    f"duplicate ZIP member is forbidden: {member.filename!r}"
                )
            actual.add(normalized_name)
        if actual != expected:
            raise ReleaseAssetVerificationError(
                f"ZIP inventory mismatch for {archive_path.name}: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        archive.extractall(destination)
    _verify_required(destination, required_files)


def _install_final_asset(asset: ProvisionAsset, asset_path: Path, models_root: Path) -> None:
    verify_file(asset_path, size=asset.size, sha256=asset.sha256)
    destination = _safe_destination(models_root, asset.destination)
    if asset.archive == "zip":
        _extract_exact_zip(asset_path, destination, asset.required_files)
        return
    if asset.archive != "file":
        raise ReleaseAssetVerificationError(
            f"models-v1 final gate does not support archive type {asset.archive!r}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(asset_path, destination)
    verify_file(destination, size=asset.installed_size, sha256=asset.sha256)
    expected_required = {(item.path, item.size, item.sha256) for item in asset.required_files}
    actual_required = {(destination.name, destination.stat().st_size, sha256_file(destination))}
    if expected_required != actual_required:
        raise ReleaseAssetVerificationError(
            f"file asset inventory does not describe installed {destination.name!r}"
        )


def _load_reference_gate(
    reference_spec_path: Path,
    repository_root: Path,
) -> tuple[Path, Mapping[str, Any]]:
    try:
        payload = json.loads(reference_spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseAssetVerificationError("reference audio gate metadata is unreadable") from exc
    if not isinstance(payload, Mapping) or int(payload.get("schema_version", 0)) != 1:
        raise ReleaseAssetVerificationError("reference audio gate schema_version must be 1")
    status_value = str(payload.get("status", ""))
    if status_value != "ready":
        reason = str(payload.get("reason", "no reviewed reference fixture is configured"))
        raise ReleaseAssetVerificationError(f"reference audio gate is blocked: {reason}")

    audio = payload.get("audio")
    expected = payload.get("expected")
    if not isinstance(audio, Mapping) or not isinstance(expected, Mapping):
        raise ReleaseAssetVerificationError(
            "ready reference gate requires audio and expected objects"
        )
    relative = str(audio.get("path", ""))
    audio_path = _safe_destination(repository_root.resolve(), relative)
    if not audio_path.is_file():
        raise ReleaseAssetVerificationError(f"reference audio fixture is missing: {audio_path}")
    expected_audio_hash = str(audio.get("sha256", "")).casefold()
    if len(expected_audio_hash) != 64 or sha256_file(audio_path) != expected_audio_hash:
        raise ReleaseAssetVerificationError("reference audio SHA-256 does not match metadata")

    for key in ("preview_text", "final_text"):
        text = normalize_transcript(str(expected.get(key, "")))
        digest = str(expected.get(f"{key}_sha256", "")).casefold()
        if not text or len(digest) != 64 or transcript_sha256(text) != digest:
            raise ReleaseAssetVerificationError(f"reference expected {key} metadata is invalid")
    return audio_path, payload


def run_reference_inference(
    models_root: Path,
    audio_path: Path,
    reference: Mapping[str, Any],
) -> dict[str, object]:
    """Run CPU preview, VAD, and final transcription against the extracted artifacts."""

    import numpy as np
    import opencc
    import soundfile as sf

    from auto_speech_journal.config import AppConfig
    from auto_speech_journal.finalizer_engine import FasterWhisperFinalizer
    from auto_speech_journal.preview_engine import SherpaPreviewEngine
    from auto_speech_journal.workers import probe_realtime_models

    audio_metadata = reference["audio"]
    if not isinstance(audio_metadata, Mapping):
        raise ReleaseAssetVerificationError("reference audio metadata is invalid")
    samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    channels = int(samples.shape[1])
    expected_rate = int(audio_metadata.get("sample_rate", 0))
    expected_channels = int(audio_metadata.get("channels", 0))
    if sample_rate != expected_rate or channels != expected_channels:
        raise ReleaseAssetVerificationError(
            f"reference audio format mismatch: {sample_rate} Hz/{channels} channels"
        )
    if expected_rate != 16_000 or expected_channels != 1:
        raise ReleaseAssetVerificationError("reference audio must be 16 kHz mono")
    duration_ms = round(len(samples) * 1000 / sample_rate)
    minimum = int(audio_metadata.get("minimum_duration_ms", 1))
    maximum = int(audio_metadata.get("maximum_duration_ms", 0))
    if duration_ms < minimum or maximum <= 0 or duration_ms > maximum:
        raise ReleaseAssetVerificationError(
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


def verify_model_release_assets(
    manifest_path: Path,
    assets_dir: Path,
    reference_spec_path: Path,
    repository_root: Path,
    *,
    inference_runner: InferenceRunner = run_reference_inference,
    work_dir: Path | None = None,
) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    actual_assets = {asset.name for asset in manifest.assets}
    if manifest.release != "models-v1" or actual_assets != EXPECTED_ASSETS:
        raise ReleaseAssetVerificationError("final gate requires the exact models-v1 asset set")

    audio_path, reference = _load_reference_gate(reference_spec_path, repository_root)
    parent = work_dir.resolve() if work_dir is not None else assets_dir.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="models-v1-final-gate-", dir=parent) as temporary:
        models_root = Path(temporary) / "models"
        models_root.mkdir()
        for asset in manifest.assets:
            asset_path = assets_dir / asset.name
            _install_final_asset(asset, asset_path, models_root)
        inference = dict(inference_runner(models_root, audio_path, reference))

    expected = reference["expected"]
    if not isinstance(expected, Mapping):
        raise ReleaseAssetVerificationError("reference expected metadata is invalid")
    for key in ("preview_text", "final_text"):
        actual = normalize_transcript(str(inference.get(key, "")))
        wanted = normalize_transcript(str(expected.get(key, "")))
        wanted_hash = str(expected.get(f"{key}_sha256", "")).casefold()
        if not actual or actual != wanted or transcript_sha256(actual) != wanted_hash:
            raise ReleaseAssetVerificationError(
                f"reference {key} mismatch: expected hash {wanted_hash}, "
                f"got {transcript_sha256(actual)}"
            )
    if not inference.get("preview_loaded") or not inference.get("vad_loaded"):
        raise ReleaseAssetVerificationError("re-extracted Preview/VAD inference did not load")
    if inference.get("final_device") != "cpu":
        raise ReleaseAssetVerificationError("reference final inference must prove the CPU fallback")

    return {
        "schema_version": 1,
        "release": "models-v1",
        "assets_verified": sorted(EXPECTED_ASSETS),
        "reference_audio_sha256": sha256_file(audio_path),
        "preview_text_sha256": transcript_sha256(str(inference["preview_text"])),
        "final_text_sha256": transcript_sha256(str(inference["final_text"])),
        "final_device": inference["final_device"],
        "final_compute_type": inference.get("final_compute_type"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--reference-spec", required=True, type=Path)
    parser.add_argument("--repository-root", default=Path.cwd(), type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    result = verify_model_release_assets(
        args.manifest.resolve(),
        args.assets_dir.resolve(),
        args.reference_spec.resolve(),
        args.repository_root.resolve(),
        work_dir=args.work_dir.resolve() if args.work_dir else None,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseAssetVerificationError, ProvisioningError, ValueError, OSError) as error:
        print(f"models-v1 final asset gate failed: {error}", file=os.sys.stderr)
        raise SystemExit(1) from error
