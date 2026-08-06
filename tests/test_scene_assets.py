from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auto_speech_journal.scene_assets import validate_runtime_scenes  # noqa: E402
from tools.validate_scene_assets import (  # noqa: E402
    DEFAULT_MANIFEST,
    validate_manifest,
    webp_dimensions,
)


def _copy_manifest_bundle(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.json"
    shutil.copyfile(DEFAULT_MANIFEST, manifest_path)
    shutil.copyfile(
        DEFAULT_MANIFEST.with_name("scene-manifest.schema.json"),
        tmp_path / "scene-manifest.schema.json",
    )
    return manifest_path


def _read_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_v2_planned_manifest(tmp_path: Path) -> Path:
    manifest_path = _copy_manifest_bundle(tmp_path)
    legacy = _read_manifest(manifest_path)
    assets: list[dict[str, object]] = []
    if legacy.get("schema_version") == 2:
        for source in legacy["assets"]:
            assert isinstance(source, dict)
            asset = dict(source)
            asset.update(
                status="planned",
                final_prompt=None,
                width=None,
                height=None,
                sha256=None,
            )
            assets.append(asset)
    else:
        for source in legacy["assets"]:
            assert isinstance(source, dict)
            for variant in ("compact", "workspace"):
                month = source["month"]
                state = source["state"]
                assets.append(
                    {
                        "month": month,
                        "state": state,
                        "variant": variant,
                        "derived_from": (
                            f"{month}-{state}-workspace.webp"
                            if variant == "compact"
                            else None
                            if state == "listening"
                            else f"{month}-listening-workspace.webp"
                        ),
                        "filename": f"{month}-{state}-{variant}.webp",
                        "status": "planned",
                        "planned_prompt": (
                            f'{source["planned_prompt"]}\nOutput variant "{variant}" uses the '
                            "approved today-river composition and crop contract."
                        ),
                        "final_prompt": None,
                        "width": None,
                        "height": None,
                        "sha256": None,
                    }
                )
    legacy.update(
        schema_version=2,
        asset_count=192,
        dimensions={
            "compact": {
                "width": 1024,
                "height": 768,
                "aspect_ratio": "4:3",
                "format": "webp",
            },
            "workspace": {
                "width": 1536,
                "height": 1024,
                "aspect_ratio": "3:2",
                "format": "webp",
            },
        },
        assets=assets,
    )
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    return manifest_path


def test_production_manifest_has_complete_ready_matrix() -> None:
    manifest = _read_manifest()
    assets = manifest["assets"]

    assert validate_manifest() == []
    assert validate_manifest(strict=True) == []
    assert validate_runtime_scenes(strict=True) == []
    assert isinstance(assets, list)
    assert manifest["schema_version"] == 2
    assert manifest["asset_count"] == 192
    assert len(assets) == 192
    assert {asset["status"] for asset in assets} == {"ready"}
    assert len(
        {(asset["month"], asset["state"], asset["variant"]) for asset in assets}
    ) == 192
    assert all(len(asset["final_prompt"]) >= 100 for asset in assets)
    assert all(len(asset["planned_prompt"]) >= 100 for asset in assets)
    assert all(
        (asset["width"], asset["height"])
        == ((1024, 768) if asset["variant"] == "compact" else (1536, 1024))
        for asset in assets
    )
    assert all(len(asset["sha256"]) == 64 for asset in assets)


def test_strict_mode_rejects_planned_assets_without_pretending_they_exist(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_manifest_bundle(tmp_path)
    manifest = _read_manifest(manifest_path)
    for asset in manifest["assets"]:
        asset.update(
            status="planned",
            final_prompt=None,
            width=None,
            height=None,
            sha256=None,
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    errors = validate_manifest(manifest_path, strict=True)

    planned_errors = [error for error in errors if "strict mode requires all assets ready" in error]
    assert len(planned_errors) == 192


def test_runtime_installer_gate_rejects_a_planned_development_pack(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_bundle(tmp_path)
    manifest = _read_manifest(manifest_path)
    for asset in manifest["assets"]:
        asset.update(
            status="planned",
            final_prompt=None,
            width=None,
            height=None,
            sha256=None,
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    errors = validate_runtime_scenes(strict=True, root=tmp_path)

    assert len([error for error in errors if "尚未完成" in error]) == 192
    assert validate_runtime_scenes(strict=False, root=tmp_path) == []


def test_v2_planned_variant_matrix_passes_prototype_gate_but_not_strict_gate(
    tmp_path: Path,
) -> None:
    manifest_path = _write_v2_planned_manifest(tmp_path)

    assert validate_manifest(manifest_path) == []
    strict_errors = validate_manifest(manifest_path, strict=True)
    assert len(
        [error for error in strict_errors if "strict mode requires all assets ready" in error]
    ) == 192
    assert validate_runtime_scenes(strict=False, root=tmp_path) == []
    assert len(
        [
            error
            for error in validate_runtime_scenes(strict=True, root=tmp_path)
            if "尚未完成" in error
        ]
    ) == 192


def test_v2_ready_variant_uses_variant_specific_dimensions_and_digest(
    tmp_path: Path,
) -> None:
    manifest_path = _write_v2_planned_manifest(tmp_path)
    manifest = _read_manifest(manifest_path)
    asset = manifest["assets"][0]
    assert isinstance(asset, dict)
    image_path = tmp_path / str(asset["filename"])
    image = QImage(1024, 768, QImage.Format.Format_RGB32)
    image.fill(QColor("#E9E2D2"))
    assert image.save(str(image_path), "WEBP", 88)
    asset.update(
        status="ready",
        final_prompt=asset["planned_prompt"],
        width=1024,
        height=768,
        sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert validate_manifest(manifest_path) == []
    assert validate_runtime_scenes(strict=False, root=tmp_path) == []


def test_v2_manifest_rejects_broken_master_lineage(tmp_path: Path) -> None:
    manifest_path = _write_v2_planned_manifest(tmp_path)
    manifest = _read_manifest(manifest_path)
    asset = manifest["assets"][0]
    assert isinstance(asset, dict)
    asset["derived_from"] = "01-listening-workspace.webp"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert any("derived_from" in error for error in validate_manifest(manifest_path))
    assert any(
        "母圖／衍生關係" in error
        for error in validate_runtime_scenes(strict=False, root=tmp_path)
    )


def test_validator_rejects_incomplete_matrix(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_bundle(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["assets"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_manifest(manifest_path)

    assert "assets must contain exactly 192 entries, found 191" in errors
    assert "asset matrix is missing 1 month/state entries" in errors


def test_validator_rejects_machine_local_asset_paths(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_bundle(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["generation_workflow"]["candidate_gate"] = (
        "Copy from C:\\Users\\user\\.codex\\generated_images\\candidate.webp"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_manifest(manifest_path)

    assert any("forbidden machine-local path" in error for error in errors)


def test_manifest_schema_declares_legacy_and_variant_contracts() -> None:
    schema = _read_manifest(DEFAULT_MANIFEST.with_name("scene-manifest.schema.json"))

    assert schema["properties"]["assets"]["minItems"] == 96
    assert schema["properties"]["assets"]["maxItems"] == 192
    assert schema["$defs"]["legacyAsset"]["properties"]["status"]["enum"] == [
        "pending",
        "ready",
    ]
    assert schema["$defs"]["variantAsset"]["properties"]["status"]["enum"] == [
        "planned",
        "ready",
    ]
    assert schema["$defs"]["variantDimensions"]["properties"]["compact"][
        "properties"
    ]["height"]["const"] == 768
    assert schema["$defs"]["variantDimensions"]["properties"]["workspace"][
        "properties"
    ]["width"]["const"] == 1536


def test_webp_dimension_reader_decodes_real_image(tmp_path: Path) -> None:
    path = tmp_path / "scene.webp"
    image = QImage(1024, 1536, QImage.Format.Format_RGB32)
    image.fill(QColor("#E9E2D2"))
    assert image.save(str(path), "WEBP", 88)

    assert webp_dimensions(path) == (1024, 1536)


def test_webp_dimension_reader_rejects_header_only_corruption(tmp_path: Path) -> None:
    vp8x = b"\x00" * 4 + (1024 - 1).to_bytes(3, "little") + (1536 - 1).to_bytes(3, "little")
    chunk = b"VP8X" + len(vp8x).to_bytes(4, "little") + vp8x
    payload = b"WEBP" + chunk
    path = tmp_path / "broken.webp"
    path.write_bytes(b"RIFF" + len(payload).to_bytes(4, "little") + payload)

    try:
        webp_dimensions(path)
    except ValueError:
        pass
    else:
        raise AssertionError("a header-only WebP must not pass the production decoder gate")


def test_brand_mark_is_a_transparent_imagegen_asset() -> None:
    brand_root = DEFAULT_MANIFEST.parents[1] / "brand"
    mark_path = brand_root / "journal-ink-icon.png"
    metadata = json.loads((brand_root / "journal-ink-icon.json").read_text(encoding="utf-8"))
    image = QImage(str(mark_path))

    assert not image.isNull()
    assert (image.width(), image.height()) == (256, 256)
    assert image.hasAlphaChannel()
    assert QColor(image.pixelColor(0, 0)).alpha() == 0
    assert QColor(image.pixelColor(128, 128)).alpha() > 0
    assert metadata["generator"] == "built-in ImageGen"
    assert (metadata["width"], metadata["height"]) == (image.width(), image.height())
    assert metadata["background_removal"]["alpha"] is True
    assert metadata["sha256"] == hashlib.sha256(mark_path.read_bytes()).hexdigest()
