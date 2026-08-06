from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .config import ModelConfig
from .provisioning import ProvisionEvent, ProvisioningError
from .runtime_models import (
    RUNTIME_MODEL_MANIFEST_FILENAME,
    RuntimeModelDownloader,
    RuntimeModelManifest,
    find_runtime_model_manifest,
    load_runtime_model_manifest,
    provision_runtime_models,
    verify_installed_runtime_models,
)

ProgressCallback = Callable[[str, int, int], None]


class ModelDownloadError(RuntimeError):
    pass


class ModelVerificationError(ModelDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    revision: str
    install_path: str
    required_files: tuple[str, ...]
    runtime_format: str


PREVIEW_SPEC = ModelSpec(
    key="csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en",
    revision="8e40c43232a1c5c66c82111efc5820d3accca11b",
    install_path="sherpa-onnx-streaming-paraformer-bilingual-zh-en",
    required_files=("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"),
    runtime_format="sherpa-onnx-paraformer-int8",
)

VAD_SPEC = ModelSpec(
    key="R4kSo1997/sherpa-onnx-silero-vad-v5",
    revision="4a6e5a75370a3ca741c950f8feda0dbed11c18ac",
    install_path="silero-vad",
    required_files=("silero_vad.onnx",),
    runtime_format="sherpa-onnx-silero-vad-v4",
)

FINAL_SPEC = ModelSpec(
    key="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    install_path="faster-whisper-large-v3-turbo",
    required_files=(
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    ),
    runtime_format="ctranslate2-float16",
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
        vad_model=models_dir / VAD_SPEC.install_path / VAD_SPEC.required_files[0],
        final_dir=models_dir / FINAL_SPEC.install_path,
    )


def _validate_requested_models(config: ModelConfig) -> None:
    expected = ModelConfig()
    if config != expected:
        raise ModelVerificationError(
            "configured model identities or compute profiles do not match schema v4"
        )


def _validate_manifest_contract(manifest: RuntimeModelManifest) -> None:
    expected = {
        PREVIEW_SPEC.install_path: PREVIEW_SPEC,
        VAD_SPEC.install_path: VAD_SPEC,
        FINAL_SPEC.install_path: FINAL_SPEC,
    }
    actual = {model.destination: model for model in manifest.models}
    if set(actual) != set(expected):
        raise ModelVerificationError(
            "runtime model manifest destinations do not match the application contract"
        )
    for destination, spec in expected.items():
        model = actual[destination]
        paths = tuple(file.path for file in model.files)
        if (
            paths != spec.required_files
            or model.format != spec.runtime_format
            or model.repository != spec.key
            or model.revision != spec.revision
        ):
            raise ModelVerificationError(
                f"runtime model manifest files or format do not match {destination}"
            )


def _resolve_manifest(models_dir: Path, manifest_path: Path | None) -> RuntimeModelManifest:
    selected = manifest_path or find_runtime_model_manifest(runtime_root=models_dir.parent)
    if selected is None:
        raise ModelDownloadError(
            f"unable to find {RUNTIME_MODEL_MANIFEST_FILENAME}; pass the packaged manifest"
        )
    try:
        manifest = load_runtime_model_manifest(selected)
    except ProvisioningError as error:
        raise ModelDownloadError(str(error)) from error
    _validate_manifest_contract(manifest)
    return manifest


def ensure_models(
    config: ModelConfig,
    models_dir: Path,
    progress: ProgressCallback | None = None,
    *,
    manifest_path: Path | None = None,
    downloader: RuntimeModelDownloader | None = None,
) -> ModelPaths:
    """Install ready-to-run Hugging Face artifacts without local model conversion."""

    _validate_requested_models(config)
    manifest = _resolve_manifest(models_dir, manifest_path)

    def report(event: ProvisionEvent) -> None:
        if progress is not None:
            progress(event.asset or event.release, event.completed, event.total)

    try:
        if downloader is None:
            provision_runtime_models(
                manifest,
                models_dir,
                progress=report if progress is not None else None,
            )
        else:
            provision_runtime_models(
                manifest,
                models_dir,
                progress=report if progress is not None else None,
                downloader=downloader,
            )
    except ProvisioningError as error:
        raise ModelDownloadError(str(error)) from error
    return verify_models(config, models_dir, deep=True, manifest_path=manifest.source_path)


def verify_models(
    config: ModelConfig,
    models_dir: Path,
    *,
    deep: bool = False,
    manifest_path: Path | None = None,
) -> ModelPaths:
    _validate_requested_models(config)
    paths = resolve_model_paths(models_dir)
    missing = [
        path
        for path in (
            paths.preview_encoder,
            paths.preview_decoder,
            paths.preview_tokens,
            paths.vad_model,
            *(paths.final_dir / filename for filename in FINAL_SPEC.required_files),
        )
        if not path.is_file()
    ]
    if missing:
        raise ModelVerificationError(
            "required model files are missing: " + ", ".join(str(path) for path in missing)
        )
    if deep:
        manifest = _resolve_manifest(models_dir, manifest_path)
        try:
            verify_installed_runtime_models(manifest, models_dir)
        except ProvisioningError as error:
            raise ModelVerificationError(str(error)) from error
    return paths


__all__ = [
    "FINAL_SPEC",
    "MODEL_MANIFEST",
    "PREVIEW_SPEC",
    "VAD_SPEC",
    "ModelDownloadError",
    "ModelPaths",
    "ModelSpec",
    "ModelVerificationError",
    "ensure_models",
    "resolve_model_paths",
    "verify_models",
]
