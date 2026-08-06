"""Validate the pinned Hugging Face runtime-models-v1 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from auto_speech_journal.runtime_models import (
    RuntimeModelManifest,
    huggingface_download_url,
    load_runtime_model_manifest,
)

EXPECTED_MODELS: Mapping[str, Mapping[str, object]] = {
    "paraformer-preview": {
        "repository": "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "revision": "8e40c43232a1c5c66c82111efc5820d3accca11b",
        "format": "sherpa-onnx-paraformer-int8",
        "destination": "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "license": {
            "spdx": "Apache-2.0",
            "url": "https://www.apache.org/licenses/LICENSE-2.0",
        },
        "source_url": "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/tree/8e40c43232a1c5c66c82111efc5820d3accca11b",
        "source_description": (
            "Pinned sherpa-onnx INT8 export of the ModelScope streaming bilingual "
            "Paraformer model."
        ),
        "files": {
            "encoder.int8.onnx": (
                165462184,
                "81a70226a8934e6ed92aa1d4fc486b428b5398e2f2619ed4897b7294cab90e9a",
            ),
            "decoder.int8.onnx": (
                71664561,
                "f3cca9f77bb9d93c8fcbfb63ae617b6b1ee96818df3aa3b151c40658fe38594f",
            ),
            "tokens.txt": (
                75756,
                "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6",
            ),
        },
    },
    "silero-vad": {
        "repository": "R4kSo1997/sherpa-onnx-silero-vad-v5",
        "revision": "4a6e5a75370a3ca741c950f8feda0dbed11c18ac",
        "format": "sherpa-onnx-silero-vad-v4",
        "destination": "silero-vad",
        "license": {
            "spdx": "MIT",
            "url": "https://raw.githubusercontent.com/snakers4/silero-vad/be95df9152c0d7618fa1edfeb296fc3dae32376f/LICENSE",
        },
        "source_url": "https://huggingface.co/R4kSo1997/sherpa-onnx-silero-vad-v5/tree/4a6e5a75370a3ca741c950f8feda0dbed11c18ac",
        "source_description": (
            "Byte-identical Hugging Face mirror of the pinned sherpa-onnx Silero VAD "
            "artifact."
        ),
        "files": {
            "silero_vad.onnx": (
                643854,
                "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            )
        },
    },
    "whisper-large-v3-turbo": {
        "repository": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "format": "ctranslate2-float16",
        "destination": "faster-whisper-large-v3-turbo",
        "license": {
            "spdx": "MIT",
            "url": "https://huggingface.co/openai/whisper-large-v3-turbo/raw/41f01f3fe87f28c78e2fbf8b568835947dd65ed9/LICENSE",
        },
        "source_url": "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/tree/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "source_description": (
            "Pinned, ready-to-run CTranslate2 float16 conversion of OpenAI Whisper "
            "large-v3-turbo; no client-side conversion is performed."
        ),
        "files": {
            "config.json": (
                2263,
                "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
            ),
            "model.bin": (
                1617884929,
                "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
            ),
            "preprocessor_config.json": (
                340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            "tokenizer.json": (
                2710337,
                "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
            ),
            "vocabulary.json": (
                1068114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        },
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_runtime_model_manifest(path: Path) -> RuntimeModelManifest:
    manifest = load_runtime_model_manifest(path)
    if manifest.release != "runtime-models-v1":
        raise ValueError("manifest release must be runtime-models-v1")
    models = {model.name: model for model in manifest.models}
    if set(models) != set(EXPECTED_MODELS):
        raise ValueError("runtime manifest has missing or unexpected models")

    for name, expected in EXPECTED_MODELS.items():
        model = models[name]
        for field in ("repository", "revision", "format", "destination"):
            if getattr(model, field) != expected[field]:
                raise ValueError(f"runtime model {name!r} has an unexpected {field}")
        expected_license = expected["license"]
        if not isinstance(expected_license, Mapping) or {
            "spdx": model.license.spdx,
            "url": model.license.url,
        } != expected_license:
            raise ValueError(f"runtime model {name!r} has an unexpected license")
        if model.source.url != expected["source_url"]:
            raise ValueError(f"runtime model {name!r} has an unexpected source URL")
        if model.source.description != expected["source_description"]:
            raise ValueError(f"runtime model {name!r} has an unexpected source description")
        expected_files = expected["files"]
        if not isinstance(expected_files, Mapping):
            raise AssertionError("validator file contract is invalid")
        actual_files = {file.path: (file.size, file.sha256) for file in model.files}
        if actual_files != expected_files:
            raise ValueError(f"runtime model {name!r} has an unexpected file inventory")
        for file in model.files:
            url = huggingface_download_url(model, file)
            expected_prefix = (
                f"https://huggingface.co/{model.repository}/resolve/{model.revision}/"
            )
            if not url.startswith(expected_prefix) or "main" in url or "latest" in url:
                raise ValueError(f"runtime model {name!r} does not use a pinned HF URL")
    return manifest


def manifest_summary(manifest: RuntimeModelManifest, path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": manifest.release,
        "provider": manifest.provider,
        "manifest_sha256": sha256_file(path),
        "model_count": len(manifest.models),
        "file_count": sum(len(model.files) for model in manifest.models),
        "download_size": manifest.download_size,
        "repositories": {
            model.name: {
                "repository": model.repository,
                "revision": model.revision,
            }
            for model in manifest.models
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    path = args.manifest.resolve()
    manifest = validate_runtime_model_manifest(path)
    print(json.dumps(manifest_summary(manifest, path), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
