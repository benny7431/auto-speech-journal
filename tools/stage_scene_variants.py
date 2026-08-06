from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QImageWriter

try:
    from tools.build_scene_variant_prompts import (
        GLOBAL_CONSTRAINTS,
        GLOBAL_STYLE,
        MONTHS,
    )
    from tools.build_scene_variant_prompts import (
        STATES as STATE_TREATMENTS,
    )
except ModuleNotFoundError:  # Direct execution adds tools/, not the repository root.
    from build_scene_variant_prompts import (  # type: ignore[no-redef]
        GLOBAL_CONSTRAINTS,
        GLOBAL_STYLE,
        MONTHS,
    )
    from build_scene_variant_prompts import (
        STATES as STATE_TREATMENTS,
    )

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = ROOT / "artifacts" / "today-river-production-v2"
RAW_ROOT = PRODUCTION_ROOT / "raw"
PROMPT_ROOT = PRODUCTION_ROOT / "prompts"
MATRIX_ROOT = PRODUCTION_ROOT / "matrix"
BASE_MANIFEST = (
    ROOT / "artifacts" / "today-river-prototype" / "month-07" / "manifest.json"
)
BASE_SCHEMA = (
    ROOT
    / "src"
    / "auto_speech_journal"
    / "assets"
    / "scenes"
    / "scene-manifest.schema.json"
)
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
VARIANT_SIZES = {
    "compact": QSize(1024, 768),
    "workspace": QSize(1536, 1024),
}
MONTH_THEMES_ZH = {
    "01": "霜窗冬晨",
    "02": "梅紫晚冬",
    "03": "嫩綠春雨",
    "04": "雨鏡春桌",
    "05": "翡翠初夏樹冠",
    "06": "靛藍梅雨室內",
    "07": "盛夏生活痕跡",
    "08": "青綠盛夏風暴",
    "09": "月光初秋蘆葦",
    "10": "琥珀秋日下午",
    "11": "暮紫初冬",
    "12": "象牙冬星霧",
}


def _asset_name(month: str, state: str, variant: str) -> str:
    if month not in {f"{value:02d}" for value in range(1, 13)}:
        raise ValueError(f"invalid month: {month}")
    if state not in STATES:
        raise ValueError(f"invalid state: {state}")
    if variant not in VARIANT_SIZES:
        raise ValueError(f"invalid variant: {variant}")
    return f"{month}-{state}-{variant}"


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


def stage_variant(
    source: Path,
    month: str,
    state: str,
    variant: str,
    *,
    matrix_root: Path = MATRIX_ROOT,
) -> Path:
    name = _asset_name(month, state, variant)
    image = QImageReader(str(source)).read()
    if image.isNull():
        raise ValueError(f"unable to decode generated image: {source}")
    image = _cover_crop(image, VARIANT_SIZES[variant])
    matrix_root.mkdir(parents=True, exist_ok=True)
    destination = matrix_root / f"{name}.webp"
    temporary = destination.with_suffix(".webp.tmp")
    writer = QImageWriter(str(temporary), b"webp")
    writer.setQuality(88)
    if not writer.write(image):
        raise ValueError(f"unable to encode {destination.name}: {writer.errorString()}")
    del writer
    temporary.replace(destination)
    return destination


def stage_all(
    *,
    raw_root: Path = RAW_ROOT,
    matrix_root: Path = MATRIX_ROOT,
    months: Sequence[str] | None = None,
) -> list[Path]:
    outputs: list[Path] = []
    selected_months = tuple(months or (f"{value:02d}" for value in range(1, 13)))
    for month in selected_months:
        for state in STATES:
            for variant in VARIANT_SIZES:
                source = raw_root / f"{month}-{state}-{variant}.png"
                if source.is_file():
                    outputs.append(
                        stage_variant(source, month, state, variant, matrix_root=matrix_root)
                    )
    return outputs


def seed_july(
    *,
    pilot_root: Path = BASE_MANIFEST.parent,
    matrix_root: Path = MATRIX_ROOT,
) -> int:
    matrix_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for state in STATES:
        for variant in VARIANT_SIZES:
            name = f"07-{state}-{variant}.webp"
            source = pilot_root / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, matrix_root / name)
            count += 1
    return count


def build_manifest(
    *,
    base_manifest: Path = BASE_MANIFEST,
    prompt_root: Path = PROMPT_ROOT,
    matrix_root: Path = MATRIX_ROOT,
) -> tuple[Path, int]:
    manifest: dict[str, Any] = json.loads(base_manifest.read_text(encoding="utf-8"))
    prompt_catalog = manifest.get("prompt_catalog")
    if not isinstance(prompt_catalog, dict):
        raise ValueError("base manifest has no prompt_catalog object")
    prompt_catalog["global_style"] = GLOBAL_STYLE
    prompt_catalog["global_constraints"] = GLOBAL_CONSTRAINTS
    month_records = prompt_catalog.get("months")
    if not isinstance(month_records, list):
        raise ValueError("base manifest prompt_catalog has no months array")
    for record in month_records:
        if not isinstance(record, dict) or str(record.get("month")) not in MONTHS:
            raise ValueError("base manifest contains an invalid month prompt record")
        month = str(record["month"])
        theme_en, scene = MONTHS[month]
        record.update(
            theme_zh=MONTH_THEMES_ZH[month],
            theme_en=theme_en,
            scene=scene,
        )
    state_records = prompt_catalog.get("states")
    if not isinstance(state_records, list):
        raise ValueError("base manifest prompt_catalog has no states array")
    for record in state_records:
        if not isinstance(record, dict) or str(record.get("key")) not in STATE_TREATMENTS:
            raise ValueError("base manifest contains an invalid state prompt record")
        record["treatment"] = STATE_TREATMENTS[str(record["key"])]
    ready = 0
    for asset in manifest["assets"]:
        filename = str(asset["filename"])
        path = matrix_root / filename
        if not path.is_file():
            asset.update(
                status="planned",
                final_prompt=None,
                width=None,
                height=None,
                sha256=None,
            )
            continue
        reader = QImageReader(str(path), b"webp")
        reader.setDecideFormatFromContent(True)
        image = reader.read()
        if image.isNull():
            raise ValueError(f"unable to decode staged image: {path}")
        expected = VARIANT_SIZES[str(asset["variant"])]
        if image.size() != expected:
            raise ValueError(
                f"unexpected staged size for {filename}: "
                f"{image.width()}x{image.height()}"
            )
        prompt_path = prompt_root / f"{path.stem}.txt"
        if asset["month"] == "07" and asset.get("status") == "ready":
            final_prompt = str(asset.get("final_prompt") or "").strip()
        elif prompt_path.is_file():
            final_prompt = prompt_path.read_text(encoding="utf-8").strip()
        else:
            final_prompt = str(asset.get("final_prompt") or "").strip()
        if len(final_prompt) < 100:
            raise ValueError(f"missing final prompt for {filename}")
        planned_prompt = str(asset.get("planned_prompt") or "").strip()
        expected_header = f'month {asset["month"]}, state "{asset["state"]}"'
        expected_variant = f'variant "{asset["variant"]}"'
        if expected_header in final_prompt and expected_variant in final_prompt:
            planned_prompt = final_prompt
        asset.update(
            status="ready",
            planned_prompt=planned_prompt,
            final_prompt=final_prompt,
            width=image.width(),
            height=image.height(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        ready += 1

    matrix_root.mkdir(parents=True, exist_ok=True)
    destination = matrix_root / "manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    shutil.copy2(BASE_SCHEMA, matrix_root / "scene-manifest.schema.json")
    return destination, ready


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage and assemble the 192-entry today-river scene matrix"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    stage = subcommands.add_parser("stage")
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--month", required=True)
    stage.add_argument("--state", choices=STATES, required=True)
    stage.add_argument("--variant", choices=tuple(VARIANT_SIZES), required=True)
    stage_all_parser = subcommands.add_parser("stage-all")
    stage_all_parser.add_argument(
        "--month",
        action="append",
        choices=tuple(f"{value:02d}" for value in range(1, 13)),
        dest="months",
    )
    subcommands.add_parser("seed-july")
    subcommands.add_parser("manifest")
    args = parser.parse_args(argv)

    if args.command == "stage":
        print(stage_variant(args.source, args.month, args.state, args.variant))
    elif args.command == "stage-all":
        print(f"Staged {len(stage_all(months=args.months))} scene variants")
    elif args.command == "seed-july":
        print(f"Seeded {seed_july()} approved July variants")
    else:
        path, ready = build_manifest()
        print(f"Wrote {path} with {ready}/192 ready variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
