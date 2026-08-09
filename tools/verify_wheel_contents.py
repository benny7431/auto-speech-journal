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
REQUIRED_SUFFIXES = {
    "auto_speech_journal/runtime-models-v1.json",
    "auto_speech_journal/qml/CompactRecorder.qml",
    "auto_speech_journal/qml/FirstRunWizard.qml",
    "auto_speech_journal/qml/HoursSheet.qml",
    "auto_speech_journal/qml/IconButton.qml",
    "auto_speech_journal/qml/JournalEntryDelegate.qml",
    "auto_speech_journal/qml/JournalWindow.qml",
    "auto_speech_journal/qml/PaperButton.qml",
    "auto_speech_journal/qml/SettingsSheet.qml",
    "auto_speech_journal/qml/SoundRiver.qml",
    "auto_speech_journal/qml/SystemSheet.qml",
    "auto_speech_journal/qml/TextButton.qml",
    "auto_speech_journal/qml/TodayLiveBar.qml",
    "auto_speech_journal/qml/TodayWorkspace.qml",
    "auto_speech_journal/qml/UtilityDrawer.qml",
    "auto_speech_journal/qml/VocabularySheet.qml",
    "auto_speech_journal/assets/brand/journal-ink-icon.json",
    "auto_speech_journal/assets/brand/journal-ink-icon.png",
}
PROHIBITED_SUFFIXES = {
    "auto_speech_journal/qml/AmbientSoundRiver.qml",
    "auto_speech_journal/qml/PaperEcho.qml",
    "auto_speech_journal/qml/PigmentAbsorption.qml",
    "auto_speech_journal/qml/SceneArt.qml",
    "auto_speech_journal/qml/TodayParticleLayer.qml",
    "auto_speech_journal/qml/Waveform.qml",
}
PROHIBITED_PATH_FRAGMENTS = {
    "auto_speech_journal/assets/fonts/",
    "auto_speech_journal/assets/particles/",
    "auto_speech_journal/assets/scenes/",
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
        "(runtime model manifest, journal QML, brand assets; "
        "license notices present; runtime data and local fonts excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
