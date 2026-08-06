"""Verify that a built wheel contains the complete offline product UI."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from collections.abc import Sequence
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])
STATES = (
    "starting",
    "listening",
    "capturing",
    "finalizing",
    "paused",
    "degraded",
    "error",
    "stopped",
)
LEGACY_EXPECTED_SCENES = {
    f"{month:02d}-{state}.webp"
    for month in range(1, 13)
    for state in STATES
}
VARIANT_EXPECTED_SCENES = {
    f"{month:02d}-{state}-{variant}.webp"
    for month in range(1, 13)
    for state in STATES
    for variant in ("compact", "workspace")
}
REQUIRED_SUFFIXES = {
    "auto_speech_journal/runtime-models-v1.json",
    "auto_speech_journal/qml/AmbientSoundRiver.qml",
    "auto_speech_journal/qml/JournalEntryDelegate.qml",
    "auto_speech_journal/qml/JournalWindow.qml",
    "auto_speech_journal/qml/SceneArt.qml",
    "auto_speech_journal/qml/SoundRiver.qml",
    "auto_speech_journal/qml/TodayLiveBar.qml",
    "auto_speech_journal/qml/TodayParticleLayer.qml",
    "auto_speech_journal/qml/TodayWorkspace.qml",
    "auto_speech_journal/assets/brand/journal-ink-icon.json",
    "auto_speech_journal/assets/brand/journal-ink-icon.png",
    "auto_speech_journal/assets/particles/glow-mote.png",
    "auto_speech_journal/assets/particles/mist-mote.png",
    "auto_speech_journal/assets/particles/soft-ripple.png",
    "auto_speech_journal/assets/scenes/manifest.json",
    "auto_speech_journal/assets/scenes/scene-manifest.schema.json",
}
PROHIBITED_SUFFIXES = {
    "auto_speech_journal/qml/PaperEcho.qml",
    "auto_speech_journal/qml/Waveform.qml",
}
PROHIBITED_PATH_FRAGMENTS = {
    "auto_speech_journal/assets/fonts/",
    "字體/",
    "聲明/",
}
PROHIBITED_MEMBER_SUFFIXES = {
    ".db",
    ".flac",
    ".log",
    ".sqlite",
    ".wav",
}
SCENE_RE = re.compile(r"auto_speech_journal/assets/scenes/([^/]+\.webp)$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_contract(data: bytes) -> tuple[int, int, bool]:
    if len(data) < 33 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise ValueError("brand icon is not a valid PNG")
    ihdr_length = int.from_bytes(data[8:12], "big")
    if ihdr_length != 13:
        raise ValueError("brand icon has an invalid PNG IHDR")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    color_type = data[25]
    return width, height, color_type in {4, 6}


def verify_wheel(path: Path) -> list[str]:
    errors: list[str] = []
    with ZipFile(path) as wheel:
        names = set(wheel.namelist())
        lowercase_names = {name.lower() for name in names}
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        entry_points_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")),
            None,
        )
        license_name = next(
            (name for name in names if name.endswith(".dist-info/licenses/LICENSE")),
            None,
        )
        notices_name = next(
            (
                name
                for name in names
                if name.endswith(".dist-info/licenses/THIRD_PARTY_NOTICES.md")
            ),
            None,
        )
        if metadata_name is None:
            errors.append("wheel is missing distribution METADATA")
        else:
            metadata = BytesParser().parsebytes(wheel.read(metadata_name))
            if metadata.get("Name") != "auto-speech-journal":
                errors.append("wheel project name is not auto-speech-journal")
            expected_version = _project_version()
            if metadata.get("Version") != expected_version:
                errors.append(f"wheel version is not {expected_version}")
            requires_python = str(metadata.get("Requires-Python", ""))
            if ">=3.11" not in requires_python or "<3.12" not in requires_python:
                errors.append("wheel Requires-Python does not enforce Python 3.11")
        if entry_points_name is None:
            errors.append("wheel is missing console entry points")
        else:
            entry_points = wheel.read(entry_points_name).decode("utf-8")
            expected_entry = "auto-speech-journal = auto_speech_journal.cli:main"
            if expected_entry not in entry_points:
                errors.append("wheel is missing the auto-speech-journal console command")
        if license_name is None:
            errors.append("wheel is missing LICENSE")
        if notices_name is None:
            errors.append("wheel is missing THIRD_PARTY_NOTICES.md")
        for suffix in sorted(REQUIRED_SUFFIXES):
            if not any(name.endswith(suffix) for name in names):
                errors.append(f"wheel is missing {suffix}")
        for suffix in sorted(PROHIBITED_SUFFIXES):
            if any(name.endswith(suffix) for name in names):
                errors.append(f"wheel contains prohibited packaged asset {suffix}")
        for fragment in sorted(PROHIBITED_PATH_FRAGMENTS):
            if any(fragment in name for name in names):
                errors.append(f"wheel contains local-only directory {fragment}")
        for name in sorted(lowercase_names):
            suffix = PurePosixPath(name).suffix
            if suffix in PROHIBITED_MEMBER_SUFFIXES:
                errors.append(f"wheel contains runtime or user data: {name}")
            if any(
                fragment in f"/{name}"
                for fragment in ("/models/", "/spool/", "/logs/")
            ):
                errors.append(f"wheel contains runtime directory: {name}")
            if PurePosixPath(name).name in {
                ".model-manifest.json",
                "config.json",
                "settings-history.jsonl",
            }:
                errors.append(f"wheel contains runtime state: {name}")
            if re.search(r"[a-z]:[/\\]", name):
                errors.append(f"wheel contains a Windows absolute path: {name}")

        manifest_name = next(
            (
                name
                for name in names
                if name.endswith("auto_speech_journal/assets/scenes/manifest.json")
            ),
            None,
        )
        manifest = None
        if manifest_name is not None:
            try:
                manifest_bytes = wheel.read(manifest_name)
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
                errors.append(f"wheel scene manifest is invalid: {error}")

        variant_manifest = isinstance(manifest, dict) and manifest.get("schema_version") == 2
        expected_scenes = (
            VARIANT_EXPECTED_SCENES if variant_manifest else LEGACY_EXPECTED_SCENES
        )
        expected_count = len(expected_scenes)
        scene_members = {
            match.group(1): name
            for name in names
            if (match := SCENE_RE.search(name)) is not None
        }
        scene_names = set(scene_members)
        missing = expected_scenes - scene_names
        extra = scene_names - expected_scenes
        if missing:
            errors.append(f"wheel is missing {len(missing)} scene WebP files")
        if extra:
            errors.append(f"wheel contains {len(extra)} unexpected scene WebP files")

        if isinstance(manifest, dict):
            assets = manifest.get("assets")
            if not isinstance(assets, list):
                errors.append("wheel scene manifest has no assets array")
                assets = []
            ready = sum(
                isinstance(asset, dict) and asset.get("status") == "ready"
                for asset in assets
            )
            if (
                manifest.get("asset_count") != expected_count
                or len(assets) != expected_count
            ):
                errors.append(
                    f"wheel manifest matrix is not complete ({len(assets)}/{expected_count})"
                )
            if ready != expected_count:
                errors.append(
                    f"wheel manifest has {ready}/{expected_count} ready assets"
                )
            for asset in assets:
                if not isinstance(asset, dict) or asset.get("status") != "ready":
                    continue
                filename = asset.get("filename")
                if not isinstance(filename, str) or filename not in scene_members:
                    continue
                digest = asset.get("sha256")
                actual = hashlib.sha256(wheel.read(scene_members[filename])).hexdigest()
                if not isinstance(digest, str) or digest != actual:
                    errors.append(f"wheel scene SHA-256 mismatch: {filename}")
            normalized = manifest_bytes.decode("utf-8").replace("\\", "/").lower()
            if "/.codex/" in normalized or "generated_images" in normalized:
                errors.append("wheel manifest contains a machine-local generation path")

        brand_manifest_name = next(
            (
                name
                for name in names
                if name.endswith(
                    "auto_speech_journal/assets/brand/journal-ink-icon.json"
                )
            ),
            None,
        )
        brand_icon_name = next(
            (
                name
                for name in names
                if name.endswith(
                    "auto_speech_journal/assets/brand/journal-ink-icon.png"
                )
            ),
            None,
        )
        if brand_manifest_name is not None and brand_icon_name is not None:
            brand_manifest = json.loads(wheel.read(brand_manifest_name).decode("utf-8"))
            brand_bytes = wheel.read(brand_icon_name)
            if hashlib.sha256(brand_bytes).hexdigest() != brand_manifest.get("sha256"):
                errors.append("wheel brand icon SHA-256 does not match its manifest")
            try:
                width, height, has_alpha = _png_contract(brand_bytes)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                expected_size = (brand_manifest.get("width"), brand_manifest.get("height"))
                if (width, height) != expected_size or expected_size != (256, 256):
                    errors.append("wheel brand icon dimensions do not match its 256x256 manifest")
                if not has_alpha or not brand_manifest.get("background_removal", {}).get("alpha"):
                    errors.append("wheel brand icon does not satisfy its alpha-channel contract")

        for name in names:
            path_parts = PurePosixPath(name).parts
            if "$CODEX_HOME" in name or ".codex" in path_parts:
                errors.append(f"wheel contains a machine-local path: {name}")
    return errors


def _latest_wheel() -> Path:
    wheels = sorted(DIST.glob("*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError(f"no wheel found under {DIST}")
    return wheels[-1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="?", type=Path)
    args = parser.parse_args(argv)
    wheel = args.wheel or _latest_wheel()
    errors = verify_wheel(wheel)
    if errors:
        print(f"Wheel verification failed: {wheel}")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"Wheel verification passed: {wheel} "
        "(runtime model manifest, scene QML, particle sprites, brand assets; "
        "license notices present; runtime data and local fonts excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
