"""Create deterministic, immutable models-v1 release assets and a provision manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

GITHUB_ASSET_LIMIT = 2 * 1024 * 1024 * 1024
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
PREVIEW_DIRECTORY = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
WHISPER_DIRECTORY = "faster-whisper-large-v3-turbo"
VAD_PATH = "silero-vad/silero_vad.onnx"
PREVIEW_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")
WHISPER_FILES = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)


def distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def required_files(root: Path, names: Iterable[str]) -> list[dict[str, object]]:
    result = []
    for name in names:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"model bundle input is missing: {path}")
        result.append({"path": name, "size": path.stat().st_size, "sha256": sha256(path)})
    return result


def deterministic_zip(destination: Path, root: Path, names: Iterable[str]) -> None:
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for name in sorted(names):
            source = root / name
            if not source.is_file():
                raise FileNotFoundError(f"model bundle input is missing: {source}")
            info = zipfile.ZipInfo(name.replace("\\", "/"), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with source.open("rb") as handle, archive.open(info, "w", force_zip64=True) as output:
                shutil.copyfileobj(handle, output, length=1024 * 1024)


def asset_record(
    path: Path,
    *,
    base_url: str,
    destination: str,
    archive: str,
    installed_size: int,
    required: list[dict[str, object]],
) -> dict[str, object]:
    size = path.stat().st_size
    if size >= GITHUB_ASSET_LIMIT:
        raise ValueError(f"{path.name} is {size} bytes and exceeds GitHub's 2 GiB asset limit")
    return {
        "name": path.name,
        "url": f"{base_url.rstrip('/')}/{path.name}",
        "sha256": sha256(path),
        "size": size,
        "installed_size": installed_size,
        "destination": destination,
        "archive": archive,
        "required_files": required,
    }


def build_bundle(
    models_dir: Path,
    license_dir: Path,
    output_dir: Path,
    base_url: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_root = models_dir / PREVIEW_DIRECTORY
    whisper_root = models_dir / WHISPER_DIRECTORY
    vad = models_dir / VAD_PATH

    preview_names = (*PREVIEW_FILES, ".model-manifest.json")
    whisper_names = (*WHISPER_FILES, ".model-manifest.json")
    preview_required = required_files(preview_root, preview_names)
    whisper_required = required_files(whisper_root, whisper_names)
    if not vad.is_file():
        raise FileNotFoundError(f"model bundle input is missing: {vad}")

    preview_zip = output_dir / "models-v1-paraformer-int8.zip"
    whisper_zip = output_dir / "models-v1-whisper-large-v3-turbo-float16.zip"
    vad_asset = output_dir / "models-v1-silero-vad.onnx"
    licenses_zip = output_dir / "models-v1-licenses.zip"
    deterministic_zip(preview_zip, preview_root, preview_names)
    deterministic_zip(whisper_zip, whisper_root, whisper_names)
    shutil.copyfile(vad, vad_asset)

    license_names = ("paraformer-LICENSE.txt", "silero-vad-LICENSE.txt", "whisper-LICENSE.txt")
    license_required = required_files(license_dir, license_names)
    provenance = {
        "schema_version": 1,
        "release": "models-v1",
        "sources": {
            "preview": {
                "url": (
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                    "sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
                ),
                "revision": "github-release:asr-models:asset-155855418",
                "sha256": "5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f",
                "retained_files": preview_required,
            },
            "vad": {
                "url": (
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                    "silero_vad.onnx"
                ),
                "revision": "github-release:asr-models:asset-271935959",
                "sha256": sha256(vad),
            },
            "whisper": {
                "repository": "openai/whisper-large-v3-turbo",
                "revision": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
                "source_model_sha256": (
                    "542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1"
                ),
                "converted_files": whisper_required,
            },
        },
        "transforms": {
            "preview": "retained encoder.int8.onnx, decoder.int8.onnx, and tokens.txt",
            "vad": "copied verified upstream ONNX file",
            "whisper": "CTranslate2 float16 conversion",
        },
        "tools": {
            "python": platform.python_version(),
            "ctranslate2": distribution_version("ctranslate2"),
            "huggingface-hub": distribution_version("huggingface-hub"),
            "torch": distribution_version("torch"),
            "transformers": distribution_version("transformers"),
        },
    }
    provenance_path = license_dir / "MODEL-PROVENANCE.json"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    license_names = (*license_names, provenance_path.name)
    license_required = required_files(license_dir, license_names)
    deterministic_zip(licenses_zip, license_dir, license_names)

    assets = [
        asset_record(
            preview_zip,
            base_url=base_url,
            destination=PREVIEW_DIRECTORY,
            archive="zip",
            installed_size=sum(int(item["size"]) for item in preview_required),
            required=preview_required,
        ),
        asset_record(
            whisper_zip,
            base_url=base_url,
            destination=WHISPER_DIRECTORY,
            archive="zip",
            installed_size=sum(int(item["size"]) for item in whisper_required),
            required=whisper_required,
        ),
        asset_record(
            vad_asset,
            base_url=base_url,
            destination=VAD_PATH,
            archive="file",
            installed_size=vad_asset.stat().st_size,
            required=[
                {
                    "path": "silero_vad.onnx",
                    "size": vad_asset.stat().st_size,
                    "sha256": sha256(vad_asset),
                }
            ],
        ),
        asset_record(
            licenses_zip,
            base_url=base_url,
            destination="licenses/models-v1",
            archive="zip",
            installed_size=sum(int(item["size"]) for item in license_required),
            required=license_required,
        ),
    ]
    manifest = {
        "schema_version": 1,
        "release": "models-v1",
        "assets": assets,
    }
    manifest_path = output_dir / "models-v1.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", required=True, type=Path)
    parser.add_argument("--license-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--base-url",
        default=(
            "https://github.com/benny7431/auto-speech-journal/"
            "releases/download/models-v1"
        ),
    )
    args = parser.parse_args(argv)
    manifest = build_bundle(
        args.models_dir.resolve(),
        args.license_dir.resolve(),
        args.output_dir.resolve(),
        args.base_url,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
