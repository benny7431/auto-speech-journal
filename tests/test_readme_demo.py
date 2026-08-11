from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from auto_speech_journal.paths import AppPaths

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.render_readme_demo import render_demo  # noqa: E402

LIVE_VIDEO = ROOT / "docs" / "media" / "auto-speech-journal-live-demo.mp4"
LIVE_GIF = ROOT / "docs" / "images" / "auto-speech-journal-live-demo.gif"
LIVE_STILL = ROOT / "docs" / "images" / "auto-speech-journal-live-demo-still.png"
SOCIAL_PREVIEW = ROOT / "docs" / "images" / "social-preview.png"


def test_readme_demo_is_headless_multiframe_gif_without_runtime_paths(
    tmp_path,
    monkeypatch,
) -> None:
    def reject_runtime_paths(cls):
        raise AssertionError("README demo must not initialize AppPaths")

    monkeypatch.setattr(AppPaths, "defaults", classmethod(reject_runtime_paths))
    output = render_demo(
        tmp_path / "demo.gif",
        frame_count=8,
        duration_ms=125,
        max_width=640,
    )

    assert output.stat().st_size < 8 * 1024**2
    with Image.open(output) as image:
        assert image.format == "GIF"
        assert image.is_animated
        assert image.n_frames >= 4
        assert image.width <= 960


def test_live_demo_and_social_preview_assets_match_readme_contract() -> None:
    assert LIVE_VIDEO.stat().st_size < 10 * 1024**2
    assert b"ftyp" in LIVE_VIDEO.read_bytes()[:32]

    with Image.open(LIVE_GIF) as image:
        assert image.format == "GIF"
        assert image.is_animated
        assert image.n_frames >= 160
        assert image.width == 900
        assert image.height > 500

    with Image.open(LIVE_STILL) as image:
        assert image.format == "PNG"
        assert image.size == (1180, 820)

    assert SOCIAL_PREVIEW.stat().st_size < 1024**2
    with Image.open(SOCIAL_PREVIEW) as image:
        assert image.format == "PNG"
        assert image.size == (1280, 640)
