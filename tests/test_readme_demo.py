from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from auto_speech_journal.paths import AppPaths

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.render_readme_demo import render_demo  # noqa: E402


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
