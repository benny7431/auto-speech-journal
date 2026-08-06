from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QImageWriter

ROOT = Path(__file__).resolve().parents[1]
SCENES = ROOT / "src" / "auto_speech_journal" / "assets" / "scenes"
MANIFEST = SCENES / "manifest.json"
RECORDS = ROOT / "artifacts" / "imagegen" / "records"
EXPECTED_SIZE = QSize(1024, 1536)


def final_prompt_for(month: str, state: str, *, style_reference: bool = False) -> str:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    asset = next(
        item
        for item in manifest["assets"]
        if item["month"] == month and item["state"] == state
    )
    prompt = str(asset["planned_prompt"])
    if style_reference:
        prompt = prompt.replace(
            "Input images: none; generate the monthly listening master as the composition "
            "source for later edits.",
            "Input images: Image 1 is the selected January listening master. Use it only as "
            "the style, paper-texture, viewpoint, and composition-system reference; create a "
            "new monthly landscape rather than copying January's terrain.",
        )
    return prompt


def stage_asset(
    source: Path,
    month: str,
    state: str,
    *,
    prompt: str,
) -> Path:
    image = QImageReader(str(source)).read()
    if image.isNull():
        raise ValueError(f"unable to decode generated image: {source}")
    image = _cover_crop(image, EXPECTED_SIZE)
    destination = SCENES / f"{month}-{state}.webp"
    temporary = destination.with_suffix(".webp.tmp")
    writer = QImageWriter(str(temporary), b"webp")
    writer.setQuality(88)
    if not writer.write(image):
        raise ValueError(f"unable to encode {destination.name}: {writer.errorString()}")
    del writer
    temporary.replace(destination)

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    RECORDS.mkdir(parents=True, exist_ok=True)
    record = {
        "month": month,
        "state": state,
        "filename": destination.name,
        "final_prompt": prompt,
        "width": image.width(),
        "height": image.height(),
        "sha256": digest,
    }
    record_path = RECORDS / f"{month}-{state}.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def commit_records() -> int:
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_key = {(item["month"], item["state"]): item for item in manifest["assets"]}
    count = 0
    for record_path in sorted(RECORDS.glob("??-*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        key = (record["month"], record["state"])
        asset = by_key.get(key)
        if asset is None:
            raise ValueError(f"record is outside the manifest matrix: {record_path.name}")
        asset.update(
            status="ready",
            final_prompt=record["final_prompt"],
            width=record["width"],
            height=record["height"],
            sha256=record["sha256"],
        )
        count += 1
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return count


def _cover_crop(image: QImage, target: QSize) -> QImage:
    scaled = image.scaled(
        target,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - target.width()) // 2)
    y = max(0, (scaled.height() - target.height()) // 2)
    return scaled.copy(QRect(x, y, target.width(), target.height())).convertToFormat(
        QImage.Format.Format_RGB32
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage generated journal scenes atomically")
    subcommands = parser.add_subparsers(dest="command", required=True)
    stage = subcommands.add_parser("stage")
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--month", required=True)
    stage.add_argument("--state", required=True)
    stage.add_argument("--style-reference", action="store_true")
    stage.add_argument("--prompt-file", type=Path)
    subcommands.add_parser("commit")
    args = parser.parse_args(argv)

    if args.command == "commit":
        print(f"Committed {commit_records()} scene records")
        return 0

    prompt = (
        args.prompt_file.read_text(encoding="utf-8")
        if args.prompt_file is not None
        else final_prompt_for(
            args.month,
            args.state,
            style_reference=args.style_reference,
        )
    )
    destination = stage_asset(
        args.source,
        args.month,
        args.state,
        prompt=prompt,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
