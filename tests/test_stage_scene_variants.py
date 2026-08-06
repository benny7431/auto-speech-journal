from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QImageReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.stage_scene_variants import (  # noqa: E402
    BASE_MANIFEST,
    BASE_SCHEMA,
    build_manifest,
    seed_july,
    stage_variant,
)


def test_stage_variant_and_partial_manifest_are_atomic_and_exact(tmp_path):
    source = tmp_path / "source.png"
    raw = QImage(QSize(1800, 1000), QImage.Format.Format_RGB32)
    raw.fill(QColor("#d7c7dc"))
    assert raw.save(str(source), "PNG")

    matrix = tmp_path / "matrix"
    destination = stage_variant(source, "11", "listening", "compact", matrix_root=matrix)
    decoded = QImageReader(str(destination), b"webp").read()
    assert (decoded.width(), decoded.height()) == (1024, 768)

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    prompt = (
        'Primary request: Create scene for month 11, state "listening". '
        'Output variant "compact". Japanese lifestyle magazine dry pastel interior; '
        "no people, hands, writing, logos, brands, UI, or watermark."
    )
    (prompts / "11-listening-compact.txt").write_text(prompt, encoding="utf-8")
    manifest_path, ready = build_manifest(
        base_manifest=BASE_MANIFEST,
        prompt_root=prompts,
        matrix_root=matrix,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = next(
        item
        for item in manifest["assets"]
        if (item["month"], item["state"], item["variant"])
        == ("11", "listening", "compact")
    )
    assert ready == 1
    assert asset["status"] == "ready"
    assert (asset["width"], asset["height"]) == (1024, 768)
    assert asset["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    month_prompt = next(
        item for item in manifest["prompt_catalog"]["months"] if item["month"] == "11"
    )
    assert month_prompt["theme_zh"] == "暮紫初冬"
    assert "knitted blanket" in month_prompt["scene"]
    assert "watercolor" not in manifest["prompt_catalog"]["global_style"].lower()
    assert (matrix / "scene-manifest.schema.json").read_bytes() == BASE_SCHEMA.read_bytes()


def test_seed_july_copies_the_approved_sixteen_variants(tmp_path):
    assert seed_july(matrix_root=tmp_path) == 16
    assert len(tuple(tmp_path.glob("07-*-*.webp"))) == 16
