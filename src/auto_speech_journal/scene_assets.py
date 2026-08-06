from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImageReader

SCENE_STATES = (
    "starting",
    "listening",
    "capturing",
    "finalizing",
    "paused",
    "degraded",
    "error",
    "stopped",
)
SCENE_MONTHS = tuple(f"{month:02d}" for month in range(1, 13))
EXPECTED_SCENES = {(month, state) for month in SCENE_MONTHS for state in SCENE_STATES}
EXPECTED_SIZE = (1024, 1536)
SCENE_VARIANTS = ("compact", "workspace")
EXPECTED_VARIANT_SCENES = {
    (month, state, variant)
    for month in SCENE_MONTHS
    for state in SCENE_STATES
    for variant in SCENE_VARIANTS
}
EXPECTED_VARIANT_SIZES = {
    "compact": (1024, 768),
    "workspace": (1536, 1024),
}


def scene_root() -> Path:
    return Path(__file__).resolve().parent / "assets" / "scenes"


def validate_runtime_scenes(
    *,
    strict: bool,
    root: Path | None = None,
    decode_images: bool = True,
) -> list[str]:
    """Validate the packaged scene matrix used by the production installer."""
    directory = Path(root) if root is not None else scene_root()
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"無法讀取場景 manifest：{error}"]
    if not isinstance(manifest, dict):
        return ["場景 manifest 必須是 JSON object"]

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return ["場景 manifest 缺少 assets array"]

    schema_version = manifest.get("schema_version", 1)
    variant_manifest = schema_version == 2
    expected_matrix = EXPECTED_VARIANT_SCENES if variant_manifest else EXPECTED_SCENES
    expected_count = len(expected_matrix)

    errors: list[str] = []
    if schema_version not in {1, 2}:
        errors.append(f"不支援的場景 manifest schema_version：{schema_version}")
    if manifest.get("asset_count") != expected_count:
        errors.append(f"場景 manifest asset_count 應為 {expected_count}")
    seen: set[tuple[object, ...]] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            errors.append(f"assets[{index}] 格式錯誤")
            continue
        month = asset.get("month")
        state = asset.get("state")
        variant = asset.get("variant") if variant_manifest else None
        if (
            not isinstance(month, str)
            or not isinstance(state, str)
            or (variant_manifest and not isinstance(variant, str))
        ):
            errors.append(f"assets[{index}] 月份、狀態或 variant 格式錯誤")
            continue
        key = (month, state, variant) if variant_manifest else (month, state)
        if key in seen:
            errors.append(f"assets[{index}] 重複的場景矩陣項目")
        seen.add(key)
        suffix = f"-{variant}" if variant_manifest else ""
        expected_name = f"{month}-{state}{suffix}.webp"
        filename = asset.get("filename")
        if key not in expected_matrix or filename != expected_name:
            errors.append(f"assets[{index}] 月份、狀態或檔名不符合矩陣")
            continue
        if variant_manifest:
            expected_parent = (
                f"{month}-{state}-workspace.webp"
                if variant == "compact"
                else None if state == "listening" else f"{month}-listening-workspace.webp"
            )
            if asset.get("derived_from") != expected_parent:
                errors.append(f"assets[{index}] 母圖／衍生關係不符合矩陣")

        status = asset.get("status")
        if status != "ready":
            planned_status = "planned" if variant_manifest else "pending"
            if status != planned_status:
                errors.append(f"assets[{index}] 狀態不符合 schema_version {schema_version}")
                continue
            if strict:
                errors.append(f"{expected_name} 尚未完成")
            continue
        expected_size = (
            EXPECTED_VARIANT_SIZES[str(variant)]
            if variant_manifest
            else EXPECTED_SIZE
        )
        _validate_ready_scene(
            directory / expected_name,
            asset,
            expected_size,
            errors,
            decode_image=decode_images,
        )

    missing = expected_matrix - seen
    extra = seen - expected_matrix
    if len(assets) != expected_count:
        errors.append(f"場景 manifest 應有 {expected_count} 筆，目前為 {len(assets)} 筆")
    if missing:
        errors.append(f"場景矩陣缺少 {len(missing)} 筆")
    if extra:
        errors.append(f"場景矩陣含有 {len(extra)} 筆未預期項目")
    return errors


def _validate_ready_scene(
    path: Path,
    asset: dict[str, Any],
    expected_size: tuple[int, int],
    errors: list[str],
    *,
    decode_image: bool,
) -> None:
    if (asset.get("width"), asset.get("height")) != expected_size:
        errors.append(
            f"場景 manifest 尺寸錯誤：{path.name} 預期 "
            f"{expected_size[0]}x{expected_size[1]}"
        )
    if not path.is_file():
        errors.append(f"缺少場景檔：{path.name}")
        return
    reader = QImageReader(str(path), b"webp")
    reader.setDecideFormatFromContent(True)
    if decode_image:
        image = reader.read()
        if image.isNull():
            errors.append(f"場景檔無法解碼：{path.name}（{reader.errorString()}）")
            return
        actual_size = (image.width(), image.height())
    else:
        size = reader.size()
        if not reader.canRead() or not size.isValid():
            errors.append(f"場景檔標頭無法讀取：{path.name}（{reader.errorString()}）")
            return
        actual_size = (size.width(), size.height())
    if actual_size != expected_size:
        errors.append(
            f"場景尺寸錯誤：{path.name} 為 {actual_size[0]}x{actual_size[1]}，"
            f"預期 {expected_size[0]}x{expected_size[1]}"
        )
    digest = asset.get("sha256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not isinstance(digest, str) or digest != actual:
        errors.append(f"場景 SHA-256 不符：{path.name}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="驗證聲跡日記的離線月份場景")
    parser.add_argument("--strict", action="store_true", help="要求 manifest 場景全部完成")
    args = parser.parse_args(argv)
    errors = validate_runtime_scenes(strict=args.strict)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    print("[PASS] 離線場景資產")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
