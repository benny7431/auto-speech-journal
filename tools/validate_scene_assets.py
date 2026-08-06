"""Validate packaged monthly recorder scene assets without optional dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImageReader

EXPECTED_MONTHS = tuple(f"{month:02d}" for month in range(1, 13))
EXPECTED_STATES = (
    "starting",
    "listening",
    "capturing",
    "finalizing",
    "paused",
    "degraded",
    "error",
    "stopped",
)
EXPECTED_STATE_PRIORITIES = {
    "error": 7,
    "degraded": 6,
    "paused": 5,
    "starting": 4,
    "stopped": 4,
    "capturing": 3,
    "finalizing": 2,
    "listening": 1,
}
EXPECTED_MATRIX = {(month, state) for month in EXPECTED_MONTHS for state in EXPECTED_STATES}
EXPECTED_SIZE = (1024, 1536)
EXPECTED_VARIANTS = ("compact", "workspace")
EXPECTED_VARIANT_MATRIX = {
    (month, state, variant)
    for month in EXPECTED_MONTHS
    for state in EXPECTED_STATES
    for variant in EXPECTED_VARIANTS
}
EXPECTED_VARIANT_SIZES = {
    "compact": (1024, 768),
    "workspace": (1536, 1024),
}
EXPECTED_SCHEMA = "scene-manifest.schema.json"
DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "auto_speech_journal"
    / "assets"
    / "scenes"
    / "manifest.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[a-z]:[\\/]", re.IGNORECASE)
_FORBIDDEN_PATH_PARTS = (
    "codex_home",
    "/.codex/",
    "/generated_images/",
    "file://",
)


def _json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _json_strings(key)
            yield from _json_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            yield from _json_strings(item)


def _has_forbidden_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return _WINDOWS_ABSOLUTE_RE.match(value) is not None or any(
        part in normalized for part in _FORBIDDEN_PATH_PARTS
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def webp_dimensions(path: Path) -> tuple[int, int]:
    """Decode a WebP through Qt's runtime image plugin and return its dimensions."""
    reader = QImageReader(str(path), b"webp")
    reader.setDecideFormatFromContent(True)
    if not reader.canRead():
        raise ValueError(reader.errorString() or "WebP decoder rejected the file")
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString() or "WebP decoded to a null image")
    return image.width(), image.height()


def _validate_prompt_catalog(manifest: Mapping[str, Any], errors: list[str]) -> None:
    catalog = manifest.get("prompt_catalog")
    if not isinstance(catalog, Mapping):
        errors.append("prompt_catalog must be an object")
        return

    for field in ("global_style", "global_constraints"):
        value = catalog.get(field)
        if not isinstance(value, str) or len(value.strip()) < 20:
            errors.append(f"prompt_catalog.{field} must be a substantive string")

    month_prompts = catalog.get("months")
    if not isinstance(month_prompts, list):
        errors.append("prompt_catalog.months must be an array")
    else:
        month_keys = [item.get("month") for item in month_prompts if isinstance(item, Mapping)]
        if len(month_prompts) != 12 or set(month_keys) != set(EXPECTED_MONTHS):
            errors.append("prompt_catalog.months must contain each month 01-12 exactly once")
        for index, item in enumerate(month_prompts):
            if not isinstance(item, Mapping):
                errors.append(f"prompt_catalog.months[{index}] must be an object")
                continue
            for field in ("theme_zh", "theme_en", "scene", "palette"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"prompt_catalog.months[{index}].{field} must not be empty")

    state_prompts = catalog.get("states")
    if not isinstance(state_prompts, list):
        errors.append("prompt_catalog.states must be an array")
    else:
        state_keys = [item.get("key") for item in state_prompts if isinstance(item, Mapping)]
        if len(state_prompts) != 8 or set(state_keys) != set(EXPECTED_STATES):
            errors.append("prompt_catalog.states must contain each recorder state exactly once")
        priorities = {
            item.get("key"): item.get("priority")
            for item in state_prompts
            if isinstance(item, Mapping)
        }
        if priorities != EXPECTED_STATE_PRIORITIES:
            errors.append("prompt_catalog.states priorities do not match the product contract")
        for index, item in enumerate(state_prompts):
            if not isinstance(item, Mapping):
                errors.append(f"prompt_catalog.states[{index}] must be an object")
                continue
            if not isinstance(item.get("treatment"), str) or len(item["treatment"].strip()) < 20:
                errors.append(
                    f"prompt_catalog.states[{index}].treatment must be a substantive string"
                )


def _validate_ready_asset(
    asset: Mapping[str, Any],
    asset_path: Path,
    expected_size: tuple[int, int],
    index: int,
    errors: list[str],
) -> None:
    final_prompt = asset.get("final_prompt")
    if not isinstance(final_prompt, str) or len(final_prompt.strip()) < 100:
        errors.append(f"assets[{index}].final_prompt is required when status is ready")

    width = asset.get("width")
    height = asset.get("height")
    if (width, height) != expected_size:
        dimensions = f"{expected_size[0]}x{expected_size[1]}"
        errors.append(f"assets[{index}] manifest dimensions must be {dimensions}")

    expected_digest = asset.get("sha256")
    if not isinstance(expected_digest, str) or _SHA256_RE.fullmatch(expected_digest) is None:
        errors.append(f"assets[{index}].sha256 must be a lowercase SHA-256 digest")

    if not asset_path.is_file():
        errors.append(f"assets[{index}] ready file is missing: {asset_path.name}")
        return

    try:
        actual_size = webp_dimensions(asset_path)
    except (OSError, ValueError) as exc:
        errors.append(f"assets[{index}] invalid WebP {asset_path.name}: {exc}")
    else:
        if actual_size != expected_size:
            errors.append(
                f"assets[{index}] decoded dimensions are {actual_size[0]}x{actual_size[1]}, "
                f"expected {expected_size[0]}x{expected_size[1]}"
            )

    if isinstance(expected_digest, str) and _SHA256_RE.fullmatch(expected_digest):
        actual_digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            errors.append(f"assets[{index}] SHA-256 does not match {asset_path.name}")


def _validate_unready_asset(asset: Mapping[str, Any], index: int, errors: list[str]) -> None:
    for field in ("final_prompt", "width", "height", "sha256"):
        if asset.get(field) is not None:
            errors.append(f"assets[{index}].{field} must be null until status is ready")


def _validate_variant_dimensions(dimensions: Any, errors: list[str]) -> None:
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(EXPECTED_VARIANTS):
        errors.append("dimensions must declare compact and workspace variants")
        return
    for variant, expected_size in EXPECTED_VARIANT_SIZES.items():
        value = dimensions.get(variant)
        if not isinstance(value, Mapping):
            errors.append(f"dimensions.{variant} must be an object")
            continue
        if (value.get("width"), value.get("height")) != expected_size:
            errors.append(
                f"dimensions.{variant} must declare "
                f"{expected_size[0]}x{expected_size[1]}"
            )
        expected_ratio = "4:3" if variant == "compact" else "3:2"
        if value.get("aspect_ratio") != expected_ratio or value.get("format") != "webp":
            errors.append(f"dimensions.{variant} must declare {expected_ratio} WebP assets")


def validate_manifest(manifest_path: Path = DEFAULT_MANIFEST, *, strict: bool = False) -> list[str]:
    """Return validation errors; development mode intentionally permits pending bitmaps."""
    errors: list[str] = []
    manifest_path = manifest_path.resolve()
    try:
        manifest = _load_json_object(manifest_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"cannot load manifest: {exc}"]

    if manifest.get("$schema") != EXPECTED_SCHEMA:
        errors.append(f"$schema must be {EXPECTED_SCHEMA!r}")
    schema_path = manifest_path.parent / EXPECTED_SCHEMA
    try:
        schema = _load_json_object(schema_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"cannot load schema: {exc}")
    else:
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append("scene schema must use JSON Schema draft 2020-12")

    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        errors.append("schema_version must be 1 or 2")
    variant_manifest = schema_version == 2
    expected_matrix = EXPECTED_VARIANT_MATRIX if variant_manifest else EXPECTED_MATRIX
    expected_count = len(expected_matrix)
    unready_status = "planned" if variant_manifest else "pending"

    if manifest.get("asset_set") != "monthly-recorder-scenes":
        errors.append("asset_set must be 'monthly-recorder-scenes'")
    if manifest.get("asset_count") != expected_count:
        errors.append(f"asset_count must be {expected_count}")

    dimensions = manifest.get("dimensions")
    if variant_manifest:
        _validate_variant_dimensions(dimensions, errors)
    elif not isinstance(dimensions, Mapping) or (
        dimensions.get("width"), dimensions.get("height")
    ) != EXPECTED_SIZE:
        errors.append("dimensions must declare 1024x1536")
    elif dimensions.get("aspect_ratio") != "2:3" or dimensions.get("format") != "webp":
        errors.append("dimensions must declare 2:3 WebP assets")

    workflow = manifest.get("generation_workflow")
    if not isinstance(workflow, Mapping):
        errors.append("generation_workflow must be an object")
    else:
        if workflow.get("base_state") != "listening":
            errors.append("generation_workflow.base_state must be listening")
        if workflow.get("runtime_generation") is not False:
            errors.append("generation_workflow.runtime_generation must be false")
        if not isinstance(workflow.get("candidate_gate"), str):
            errors.append("generation_workflow.candidate_gate must document the selection gate")

    _validate_prompt_catalog(manifest, errors)

    for value in _json_strings(manifest):
        if _has_forbidden_path(value):
            errors.append(f"manifest contains a forbidden machine-local path: {value!r}")

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        errors.append("assets must be an array")
        return errors
    if len(assets) != expected_count:
        errors.append(
            f"assets must contain exactly {expected_count} entries, found {len(assets)}"
        )

    seen: set[tuple[Any, ...]] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, Mapping):
            errors.append(f"assets[{index}] must be an object")
            continue

        month = asset.get("month")
        state = asset.get("state")
        variant = asset.get("variant") if variant_manifest else None
        if (
            not isinstance(month, str)
            or not isinstance(state, str)
            or (variant_manifest and not isinstance(variant, str))
        ):
            errors.append(f"assets[{index}] has an invalid month/state/variant key")
            continue
        key = (month, state, variant) if variant_manifest else (month, state)
        if key in seen:
            errors.append(f"assets[{index}] duplicates scene matrix key {key}")
        seen.add(key)

        suffix = f"-{variant}" if variant_manifest else ""
        expected_filename = f"{month}-{state}{suffix}.webp"
        filename = asset.get("filename")
        if filename != expected_filename:
            errors.append(f"assets[{index}].filename must be {expected_filename!r}")
        if variant_manifest:
            expected_parent = (
                f"{month}-{state}-workspace.webp"
                if variant == "compact"
                else None if state == "listening" else f"{month}-listening-workspace.webp"
            )
            if asset.get("derived_from") != expected_parent:
                errors.append(
                    f"assets[{index}].derived_from must be {expected_parent!r}"
                )

        planned_prompt = asset.get("planned_prompt")
        if not isinstance(planned_prompt, str) or len(planned_prompt.strip()) < 100:
            errors.append(f"assets[{index}].planned_prompt must be a complete prompt")
        elif f'month {month}, state "{state}"' not in planned_prompt:
            errors.append(f"assets[{index}].planned_prompt does not identify its month/state")
        elif variant_manifest and f'variant "{variant}"' not in planned_prompt:
            errors.append(f"assets[{index}].planned_prompt does not identify its variant")

        status = asset.get("status")
        if status == unready_status:
            _validate_unready_asset(asset, index, errors)
            if strict:
                errors.append(
                    f"assets[{index}] is {unready_status}; "
                    "strict mode requires all assets ready"
                )
        elif status == "ready":
            expected_size = (
                EXPECTED_VARIANT_SIZES[str(variant)]
                if variant_manifest and variant in EXPECTED_VARIANT_SIZES
                else EXPECTED_SIZE
            )
            _validate_ready_asset(
                asset,
                manifest_path.parent / str(filename),
                expected_size,
                index,
                errors,
            )
        else:
            errors.append(
                f"assets[{index}].status must be {unready_status} or ready"
            )

    missing = expected_matrix - seen
    extra = seen - expected_matrix
    if missing:
        errors.append(f"asset matrix is missing {len(missing)} month/state entries")
    if extra:
        errors.append(f"asset matrix contains {len(extra)} unexpected month/state entries")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="scene manifest path",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="require every manifest WebP file to be ready and verified",
    )
    args = parser.parse_args(argv)
    errors = validate_manifest(args.manifest, strict=args.strict)
    if errors:
        mode = "strict" if args.strict else "development"
        print(f"Scene asset validation failed ({mode} mode):")
        for error in errors:
            print(f"- {error}")
        return 1

    ready_count = sum(
        asset["status"] == "ready" for asset in _load_json_object(args.manifest)["assets"]
    )
    mode = "strict" if args.strict else "development"
    asset_count = len(_load_json_object(args.manifest)["assets"])
    print(
        f"Scene asset validation passed ({mode} mode): "
        f"{ready_count}/{asset_count} assets ready"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
