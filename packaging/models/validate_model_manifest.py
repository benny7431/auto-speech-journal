"""Validate the immutable models-v1 installer manifest and its pinned digest."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from auto_speech_journal.provisioning import ProvisionManifest, load_manifest

REPOSITORY = "benny7431/auto-speech-journal"
RELEASE = "models-v1"
EXPECTED_ASSETS = {
    "models-v1-paraformer-int8.zip": (
        "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "zip",
        {
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "tokens.txt",
            ".model-manifest.json",
        },
    ),
    "models-v1-whisper-large-v3-turbo-float16.zip": (
        "faster-whisper-large-v3-turbo",
        "zip",
        {
            "config.json",
            "model.bin",
            "preprocessor_config.json",
            "tokenizer.json",
            "vocabulary.json",
            ".model-manifest.json",
        },
    ),
    "models-v1-silero-vad.onnx": (
        "silero-vad/silero_vad.onnx",
        "file",
        {"silero_vad.onnx"},
    ),
    "models-v1-licenses.zip": (
        "licenses/models-v1",
        "zip",
        {
            "paraformer-LICENSE.txt",
            "silero-vad-LICENSE.txt",
            "whisper-LICENSE.txt",
            "MODEL-PROVENANCE.json",
        },
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pinned_digest(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", value) or value == "0" * 64:
        raise ValueError(
            "models-v1 SHA-256 is not pinned; publish and attest models-v1, then commit its "
            "manifest digest before tagging the application"
        )
    return value


def validate_model_manifest(
    manifest_path: Path,
    *,
    expected_sha256_file: Path | None = None,
) -> ProvisionManifest:
    actual_digest = sha256_file(manifest_path)
    if expected_sha256_file is not None:
        expected_digest = _pinned_digest(expected_sha256_file)
        if actual_digest != expected_digest:
            raise ValueError(
                f"models-v1 manifest digest mismatch: expected {expected_digest}, got "
                f"{actual_digest}"
            )

    manifest = load_manifest(manifest_path)
    if manifest.release != RELEASE:
        raise ValueError(f"expected release {RELEASE!r}, got {manifest.release!r}")
    assets = {asset.name: asset for asset in manifest.assets}
    if set(assets) != set(EXPECTED_ASSETS):
        raise ValueError("models-v1 manifest has missing or unexpected assets")

    base_url = f"https://github.com/{REPOSITORY}/releases/download/{RELEASE}"
    for name, (destination, archive, required_names) in EXPECTED_ASSETS.items():
        asset = assets[name]
        if asset.url != f"{base_url}/{name}":
            raise ValueError(f"asset {name!r} does not use the immutable GitHub release URL")
        if asset.destination != destination or asset.archive != archive:
            raise ValueError(f"asset {name!r} has an unexpected destination or archive type")
        if {item.path for item in asset.required_files} != required_names:
            raise ValueError(f"asset {name!r} has an unexpected installed file inventory")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-sha256-file", type=Path)
    args = parser.parse_args(argv)
    validate_model_manifest(
        args.manifest.resolve(),
        expected_sha256_file=(
            args.expected_sha256_file.resolve() if args.expected_sha256_file else None
        ),
    )
    print(sha256_file(args.manifest.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
