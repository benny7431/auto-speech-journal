from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tarfile
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import __version__
from .config import ModelConfig

ProgressCallback = Callable[[str, int, int], None]
FileDownloader = Callable[[str, Path, ProgressCallback | None], None]
SnapshotDownloader = Callable[..., str]
ModelConverter = Callable[[Path, Path, "HuggingFaceModelSpec"], None]


class ModelDownloadError(RuntimeError):
    pass


class ModelVerificationError(ModelDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class DirectModelSpec:
    key: str
    revision: str
    url: str
    size: int
    digest_algorithm: str
    digest: str
    install_path: str
    archive: bool = False
    required_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HuggingFaceModelSpec:
    key: str
    revision: str
    repo_id: str
    install_path: str
    source_files: tuple[str, ...]
    source_model_file: str
    source_model_size: int
    source_model_sha256: str
    converted_files: tuple[str, ...]
    quantization: str


ModelSpec = DirectModelSpec | HuggingFaceModelSpec


PREVIEW_SPEC = DirectModelSpec(
    key="sherpa-onnx-streaming-paraformer-bilingual-zh-en-int8",
    revision="github-release:asr-models:asset-155855418",
    url=(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
    ),
    size=1_047_319_737,
    # GitHub predates release-asset digests for this upload; this SHA-256 was
    # computed over the pinned 1,047,319,737-byte official asset.
    digest_algorithm="sha256",
    digest="5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f",
    install_path="sherpa-onnx-streaming-paraformer-bilingual-zh-en",
    archive=True,
    required_files=("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"),
)

VAD_SPEC = DirectModelSpec(
    key="sherpa-onnx-silero-vad",
    revision="github-release:asr-models:asset-271935959",
    url=(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "silero_vad.onnx"
    ),
    size=643_854,
    digest_algorithm="sha256",
    digest="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    install_path="silero-vad/silero_vad.onnx",
)

FINAL_SPEC = HuggingFaceModelSpec(
    key="openai/whisper-large-v3-turbo",
    revision="41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
    repo_id="openai/whisper-large-v3-turbo",
    install_path="faster-whisper-large-v3-turbo",
    source_files=(
        "added_tokens.json",
        "config.json",
        "generation_config.json",
        "merges.txt",
        "model.safetensors",
        "normalizer.json",
        "preprocessor_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
    ),
    source_model_file="model.safetensors",
    source_model_size=1_617_824_864,
    source_model_sha256="542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1",
    converted_files=(
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    ),
    quantization="float16",
)

MODEL_MANIFEST: Mapping[str, ModelSpec] = MappingProxyType(
    {
        PREVIEW_SPEC.key: PREVIEW_SPEC,
        VAD_SPEC.key: VAD_SPEC,
        FINAL_SPEC.key: FINAL_SPEC,
    }
)


@dataclass(frozen=True, slots=True)
class ModelPaths:
    preview_dir: Path
    vad_model: Path
    final_dir: Path

    @property
    def preview_encoder(self) -> Path:
        return self.preview_dir / "encoder.int8.onnx"

    @property
    def preview_decoder(self) -> Path:
        return self.preview_dir / "decoder.int8.onnx"

    @property
    def preview_tokens(self) -> Path:
        return self.preview_dir / "tokens.txt"


def resolve_model_paths(models_dir: Path) -> ModelPaths:
    return ModelPaths(
        preview_dir=models_dir / PREVIEW_SPEC.install_path,
        vad_model=models_dir / VAD_SPEC.install_path,
        final_dir=models_dir / FINAL_SPEC.install_path,
    )


def _hash_file(path: Path, algorithm: str) -> str:
    digest = (
        hashlib.md5(usedforsecurity=False)
        if algorithm == "md5"
        else hashlib.new(algorithm)
    )
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_file(path: Path, *, size: int, algorithm: str, digest: str) -> None:
    if not path.is_file():
        raise ModelVerificationError(f"model file is missing: {path}")
    actual_size = path.stat().st_size
    if actual_size != size:
        raise ModelVerificationError(
            f"wrong size for {path.name}: expected {size}, got {actual_size}"
        )
    actual_digest = _hash_file(path, algorithm)
    if actual_digest.lower() != digest.lower():
        raise ModelVerificationError(
            f"wrong {algorithm} for {path.name}: expected {digest}, got {actual_digest}"
        )


def _default_download_file(
    url: str,
    destination: Path,
    progress: ProgressCallback | None,
) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"AutoSpeechJournal/{__version__}"},
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        completed = 0
        while block := response.read(1024 * 1024):
            output.write(block)
            completed += len(block)
            if progress is not None:
                progress(destination.name, completed, total)
        output.flush()
        os.fsync(output.fileno())


def _default_snapshot_download(**kwargs: Any) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise ModelDownloadError("huggingface-hub is required to download Whisper") from exc
    return str(snapshot_download(**kwargs))


def _default_convert_model(
    source: Path,
    destination: Path,
    spec: HuggingFaceModelSpec,
) -> None:
    try:
        importlib.import_module("torch")
        importlib.import_module("transformers")
        importlib.import_module("safetensors")
        from ctranslate2.converters import TransformersConverter
    except ImportError as exc:  # pragma: no cover - optional build dependency
        raise ModelDownloadError(
            "model conversion requires ctranslate2, transformers, torch, and safetensors"
        ) from exc
    converter = TransformersConverter(
        str(source),
        copy_files=["tokenizer.json", "preprocessor_config.json"],
        load_as_float16=True,
    )
    converter.convert(str(destination), quantization=spec.quantization)


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with tarfile.open(archive, mode="r:bz2") as bundle:
        members = bundle.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination_root):
                raise ModelVerificationError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise ModelVerificationError(f"archive links are not accepted: {member.name}")
            if not member.isfile() and not member.isdir():
                raise ModelVerificationError(f"unsupported archive member: {member.name}")
        bundle.extractall(destination, members=members)


def _find_model_root(stage: Path, required_files: tuple[str, ...]) -> Path:
    first = required_files[0]
    candidates = sorted(path.parent for path in stage.rglob(first))
    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in required_files):
            return candidate
    raise ModelVerificationError(f"archive does not contain {', '.join(required_files)}")


def _atomic_install_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    old: Path | None = None
    if destination.exists():
        old = destination.with_name(f"{destination.name}.old-{uuid.uuid4().hex}")
        os.replace(destination, old)
    try:
        os.replace(source, destination)
    except Exception:
        if old is not None and old.exists() and not destination.exists():
            os.replace(old, destination)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def _write_marker(destination: Path, spec: ModelSpec) -> None:
    marker = destination / ".model-manifest.json" if destination.is_dir() else None
    if marker is None:
        return
    if isinstance(spec, DirectModelSpec):
        tracked_files = spec.required_files
    else:
        tracked_files = spec.converted_files
    files = {
        filename: {
            "size": (destination / filename).stat().st_size,
            "sha256": _hash_file(destination / filename, "sha256"),
        }
        for filename in tracked_files
        if (destination / filename).is_file()
    }
    payload = {
        "key": spec.key,
        "revision": spec.revision,
        "files": files,
    }
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, marker)


def _verify_marker_files(destination: Path, spec: ModelSpec) -> None:
    marker = destination / ".model-manifest.json"
    if not marker.is_file():
        raise ModelVerificationError(f"model manifest is missing: {marker}")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelVerificationError(f"invalid model manifest: {marker}") from exc
    if payload.get("key") != spec.key or payload.get("revision") != spec.revision:
        raise ModelVerificationError(f"model manifest revision does not match: {marker}")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ModelVerificationError(f"model manifest has no file digests: {marker}")
    expected_files = (
        spec.required_files if isinstance(spec, DirectModelSpec) else spec.converted_files
    )
    for filename in expected_files:
        metadata = files.get(filename)
        if not isinstance(metadata, dict):
            raise ModelVerificationError(f"model manifest is missing a digest for {filename}")
        _verify_file(
            destination / filename,
            size=int(metadata.get("size", -1)),
            algorithm="sha256",
            digest=str(metadata.get("sha256", "")),
        )


def _ensure_direct_model(
    models_dir: Path,
    spec: DirectModelSpec,
    *,
    progress: ProgressCallback | None,
    download_file: FileDownloader,
) -> None:
    destination = models_dir / spec.install_path
    if spec.archive:
        if destination.is_dir() and all(
            (destination / filename).is_file() for filename in spec.required_files
        ):
            try:
                _verify_marker_files(destination, spec)
                return
            except ModelVerificationError:
                pass
    elif destination.is_file():
        try:
            _verify_file(
                destination,
                size=spec.size,
                algorithm=spec.digest_algorithm,
                digest=spec.digest,
            )
            return
        except ModelVerificationError:
            pass

    staging_root = models_dir / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    downloaded = staging_root / f"{spec.key.replace('/', '-')}-{token}.download"
    try:
        scoped_progress = None
        if progress is not None:
            def scoped_progress(_name: str, done: int, total: int) -> None:
                progress(spec.key, done, total)

        download_file(spec.url, downloaded, scoped_progress)
        _verify_file(
            downloaded,
            size=spec.size,
            algorithm=spec.digest_algorithm,
            digest=spec.digest,
        )

        if spec.archive:
            extracted = staging_root / f"extract-{token}"
            extracted.mkdir()
            _safe_extract(downloaded, extracted)
            model_root = _find_model_root(extracted, spec.required_files)
            _atomic_install_directory(model_root, destination)
            _write_marker(destination, spec)
            shutil.rmtree(extracted, ignore_errors=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(downloaded, destination)
    finally:
        downloaded.unlink(missing_ok=True)


def _ensure_huggingface_model(
    models_dir: Path,
    spec: HuggingFaceModelSpec,
    *,
    progress: ProgressCallback | None,
    snapshot_download: SnapshotDownloader,
    convert_model: ModelConverter,
) -> None:
    destination = models_dir / spec.install_path
    if destination.is_dir() and all(
        (destination / filename).is_file() for filename in spec.converted_files
    ):
        try:
            _verify_marker_files(destination, spec)
            return
        except ModelVerificationError:
            pass

    staging_root = models_dir / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stage = staging_root / f"hf-{token}"
    source = stage / "source"
    converted = stage / "converted"
    source.mkdir(parents=True)
    try:
        snapshot_download(
            repo_id=spec.repo_id,
            revision=spec.revision,
            local_dir=str(source),
            allow_patterns=list(spec.source_files),
        )
        for filename in spec.source_files:
            if not (source / filename).is_file():
                raise ModelVerificationError(f"Hugging Face snapshot is missing {filename}")
        _verify_file(
            source / spec.source_model_file,
            size=spec.source_model_size,
            algorithm="sha256",
            digest=spec.source_model_sha256,
        )
        convert_model(source, converted, spec)
        for filename in spec.converted_files:
            if not (converted / filename).is_file():
                raise ModelVerificationError(f"converted CTranslate2 model is missing {filename}")
        if progress is not None:
            progress(spec.key, spec.source_model_size, spec.source_model_size)
        _write_marker(converted, spec)
        _atomic_install_directory(converted, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _validate_requested_revisions(config: ModelConfig) -> None:
    requested = (
        ("preview", config.preview_revision, PREVIEW_SPEC.revision),
        ("final", config.final_revision, FINAL_SPEC.revision),
    )
    placeholders = {"", "pinned-by-model-manifest"}
    for label, revision, expected in requested:
        if revision not in placeholders and revision != expected:
            raise ModelVerificationError(
                f"{label} revision {revision!r} is not the pinned manifest revision {expected!r}"
            )


def ensure_models(
    config: ModelConfig,
    models_dir: Path,
    progress: ProgressCallback | None = None,
    *,
    download_file: FileDownloader | None = None,
    snapshot_download: SnapshotDownloader | None = None,
    convert_model: ModelConverter | None = None,
) -> ModelPaths:
    _validate_requested_revisions(config)
    if config.preview_model != PREVIEW_SPEC.key:
        raise ModelVerificationError(f"unsupported preview model: {config.preview_model}")
    if config.final_model != FINAL_SPEC.key:
        raise ModelVerificationError(f"unsupported final model: {config.final_model}")

    models_dir.mkdir(parents=True, exist_ok=True)
    file_downloader = download_file or _default_download_file
    hf_downloader = snapshot_download or _default_snapshot_download
    converter = convert_model or _default_convert_model
    _ensure_direct_model(
        models_dir,
        PREVIEW_SPEC,
        progress=progress,
        download_file=file_downloader,
    )
    _ensure_direct_model(
        models_dir,
        VAD_SPEC,
        progress=progress,
        download_file=file_downloader,
    )
    _ensure_huggingface_model(
        models_dir,
        FINAL_SPEC,
        progress=progress,
        snapshot_download=hf_downloader,
        convert_model=converter,
    )
    return verify_models(config, models_dir, deep=True)


def verify_models(config: ModelConfig, models_dir: Path, *, deep: bool = False) -> ModelPaths:
    _validate_requested_revisions(config)
    paths = resolve_model_paths(models_dir)
    missing = [
        path
        for path in (
            paths.preview_encoder,
            paths.preview_decoder,
            paths.preview_tokens,
            paths.vad_model,
            *(paths.final_dir / filename for filename in FINAL_SPEC.converted_files),
        )
        if not path.is_file()
    ]
    if missing:
        raise ModelVerificationError(
            "required model files are missing: " + ", ".join(str(path) for path in missing)
        )
    if deep:
        _verify_file(
            paths.vad_model,
            size=VAD_SPEC.size,
            algorithm=VAD_SPEC.digest_algorithm,
            digest=VAD_SPEC.digest,
        )
        _verify_marker_files(paths.preview_dir, PREVIEW_SPEC)
        _verify_marker_files(paths.final_dir, FINAL_SPEC)
    return paths


__all__ = [
    "FINAL_SPEC",
    "MODEL_MANIFEST",
    "PREVIEW_SPEC",
    "VAD_SPEC",
    "DirectModelSpec",
    "HuggingFaceModelSpec",
    "ModelDownloadError",
    "ModelPaths",
    "ModelVerificationError",
    "ensure_models",
    "resolve_model_paths",
    "verify_models",
]
