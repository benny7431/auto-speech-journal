from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from huggingface_hub import hf_hub_download

from .config import ModelConfig

RUNTIME_MODEL_MANIFEST_FILENAME = "runtime-models-v1.json"
_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ProgressCallback = Callable[[str, int, int], None]
HubDownloader = Callable[..., str]


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

@dataclass(frozen=True, slots=True)
class RuntimeModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeModelLicense:
    spdx: str
    url: str


@dataclass(frozen=True, slots=True)
class RuntimeModelSource:
    url: str
    description: str


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    name: str
    repository: str
    revision: str
    format: str
    destination: str
    license: RuntimeModelLicense
    source: RuntimeModelSource
    files: tuple[RuntimeModelFile, ...]


@dataclass(frozen=True, slots=True)
class RuntimeModelManifest:
    schema_version: int
    release: str
    provider: str
    models: tuple[RuntimeModel, ...]
    source_path: Path

    @property
    def download_size(self) -> int:
        return sum(file.size for model in self.models for file in model.files)


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


def default_runtime_model_manifest() -> Path:
    return Path(__file__).with_name(RUNTIME_MODEL_MANIFEST_FILENAME)


def _safe_relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelVerificationError(f"{field} must be a relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ModelVerificationError(f"{field} must stay inside the model directory")
    return normalized


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelVerificationError(f"{field} is required")
    return value.strip()


def _load_file(value: object, *, model: str) -> RuntimeModelFile:
    if not isinstance(value, Mapping):
        raise ModelVerificationError(f"model {model!r} has an invalid file entry")
    path = _safe_relative_path(value.get("path"), field=f"{model}.files.path")
    try:
        size = int(value.get("size", 0))
    except (TypeError, ValueError) as error:
        raise ModelVerificationError(f"model file {path!r} has an invalid size") from error
    digest = str(value.get("sha256", "")).casefold()
    if size <= 0 or not _SHA256.fullmatch(digest) or set(digest) == {"0"}:
        raise ModelVerificationError(f"model file {path!r} has invalid size or SHA-256")
    return RuntimeModelFile(path=path, size=size, sha256=digest)


def _load_model(value: object) -> RuntimeModel:
    if not isinstance(value, Mapping):
        raise ModelVerificationError("runtime model entries must be objects")
    name = _required_text(value.get("name"), field="model.name")
    repository = _required_text(value.get("repository"), field=f"{name}.repository")
    revision = str(value.get("revision", "")).casefold()
    destination = _safe_relative_path(value.get("destination"), field=f"{name}.destination")
    if not _REPOSITORY.fullmatch(repository):
        raise ModelVerificationError(f"model {name!r} has an invalid Hugging Face repository")
    if not _COMMIT_REVISION.fullmatch(revision):
        raise ModelVerificationError(
            f"model {name!r} revision must be a full 40-character commit hash"
        )
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ModelVerificationError(f"model {name!r} must list runtime files")
    files = tuple(_load_file(item, model=name) for item in raw_files)
    file_names = [item.path.casefold() for item in files]
    if len(file_names) != len(set(file_names)):
        raise ModelVerificationError(f"model {name!r} has duplicate files")

    raw_license = value.get("license")
    raw_source = value.get("source")
    if not isinstance(raw_license, Mapping) or not isinstance(raw_source, Mapping):
        raise ModelVerificationError(f"model {name!r} must record license and source")
    return RuntimeModel(
        name=name,
        repository=repository,
        revision=revision,
        format=_required_text(value.get("format"), field=f"{name}.format"),
        destination=destination,
        license=RuntimeModelLicense(
            spdx=_required_text(raw_license.get("spdx"), field=f"{name}.license.spdx"),
            url=_required_text(raw_license.get("url"), field=f"{name}.license.url"),
        ),
        source=RuntimeModelSource(
            url=_required_text(raw_source.get("url"), field=f"{name}.source.url"),
            description=_required_text(
                raw_source.get("description"), field=f"{name}.source.description"
            ),
        ),
        files=files,
    )


def load_runtime_model_manifest(path: Path | None = None) -> RuntimeModelManifest:
    source = (path or default_runtime_model_manifest()).resolve()
    try:
        raw: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ModelVerificationError(f"unable to read runtime model manifest: {source}") from error
    if not isinstance(raw, Mapping):
        raise ModelVerificationError("runtime model manifest must be an object")
    if raw.get("schema_version") != 1 or raw.get("provider") != "huggingface":
        raise ModelVerificationError("unsupported runtime model manifest")
    release = _required_text(raw.get("release"), field="manifest.release")
    if release != "runtime-models-v1":
        raise ModelVerificationError("runtime model manifest release must be runtime-models-v1")
    raw_models = raw.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ModelVerificationError("runtime model manifest must list models")
    models = tuple(_load_model(item) for item in raw_models)
    destinations = [model.destination.casefold() for model in models]
    if len(destinations) != len(set(destinations)):
        raise ModelVerificationError("runtime model destinations must be unique")
    return RuntimeModelManifest(1, release, "huggingface", models, source)


def _validate_requested_models(config: ModelConfig) -> None:
    if config != ModelConfig():
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
        if (
            model.repository != spec.key
            or model.revision != spec.revision
            or model.format != spec.runtime_format
            or tuple(file.path for file in model.files) != spec.required_files
        ):
            raise ModelVerificationError(
                f"runtime model manifest does not match {destination}"
            )


def _model_file(models_dir: Path, model: RuntimeModel, file: RuntimeModelFile) -> Path:
    return models_dir.joinpath(
        *PurePosixPath(model.destination).parts,
        *PurePosixPath(file.path).parts,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_file(path: Path, expected: RuntimeModelFile) -> None:
    if not path.is_file():
        raise ModelVerificationError(f"required model file is missing: {path}")
    size = path.stat().st_size
    if size != expected.size:
        raise ModelVerificationError(
            f"model file has wrong size: {path} ({size} != {expected.size})"
        )
    digest = _sha256_file(path)
    if digest != expected.sha256:
        raise ModelVerificationError(f"model file SHA-256 mismatch: {path}")


def verify_installed_runtime_models(
    manifest: RuntimeModelManifest,
    models_dir: Path,
) -> None:
    _validate_manifest_contract(manifest)
    for model in manifest.models:
        for file in model.files:
            _verify_file(_model_file(models_dir, model, file), file)


def ensure_models(
    config: ModelConfig,
    models_dir: Path,
    progress: ProgressCallback | None = None,
    *,
    manifest_path: Path | None = None,
    downloader: HubDownloader | None = None,
) -> ModelPaths:
    """Download ready-to-run model files with the official Hugging Face client."""

    _validate_requested_models(config)
    manifest = load_runtime_model_manifest(manifest_path)
    _validate_manifest_contract(manifest)
    selected_downloader = downloader or hf_hub_download
    completed = 0
    total = manifest.download_size
    models_dir.mkdir(parents=True, exist_ok=True)

    for model in manifest.models:
        destination = models_dir.joinpath(*PurePosixPath(model.destination).parts)
        destination.mkdir(parents=True, exist_ok=True)
        for file in model.files:
            target = _model_file(models_dir, model, file)
            force_download = False
            if target.exists():
                try:
                    _verify_file(target, file)
                except ModelVerificationError:
                    target.unlink(missing_ok=True)
                    force_download = True
                else:
                    completed += file.size
                    if progress is not None:
                        progress(file.path, completed, total)
                    continue
            if progress is not None:
                progress(file.path, completed, total)
            try:
                downloaded = Path(
                    selected_downloader(
                        repo_id=model.repository,
                        filename=file.path,
                        revision=model.revision,
                        local_dir=destination,
                        force_download=force_download,
                        token=False,
                    )
                )
                _verify_file(downloaded, file)
            except ModelDownloadError:
                raise
            except Exception as error:
                raise ModelDownloadError(
                    f"Hugging Face download failed for {model.repository}/{file.path}: {error}"
                ) from error
            completed += file.size
            if progress is not None:
                progress(file.path, completed, total)

    verify_installed_runtime_models(manifest, models_dir)
    return resolve_model_paths(models_dir)


def verify_models(
    config: ModelConfig,
    models_dir: Path,
    *,
    deep: bool = False,
    manifest_path: Path | None = None,
) -> ModelPaths:
    _validate_requested_models(config)
    paths = resolve_model_paths(models_dir)
    required = (
        paths.preview_encoder,
        paths.preview_decoder,
        paths.preview_tokens,
        paths.vad_model,
        *(paths.final_dir / name for name in FINAL_SPEC.required_files),
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise ModelVerificationError(
            "required model files are missing: " + ", ".join(str(path) for path in missing)
        )
    if deep:
        verify_installed_runtime_models(load_runtime_model_manifest(manifest_path), models_dir)
    return paths


__all__ = [
    "FINAL_SPEC",
    "PREVIEW_SPEC",
    "RUNTIME_MODEL_MANIFEST_FILENAME",
    "VAD_SPEC",
    "ModelDownloadError",
    "ModelPaths",
    "ModelSpec",
    "ModelVerificationError",
    "RuntimeModel",
    "RuntimeModelFile",
    "RuntimeModelManifest",
    "default_runtime_model_manifest",
    "ensure_models",
    "load_runtime_model_manifest",
    "resolve_model_paths",
    "verify_installed_runtime_models",
    "verify_models",
]
