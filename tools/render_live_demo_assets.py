"""Rebuild the README GIF, still, and GitHub social preview from the live MP4."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = REPO_ROOT / "docs" / "media" / "auto-speech-journal-live-demo.mp4"
DEFAULT_GIF = REPO_ROOT / "docs" / "images" / "auto-speech-journal-live-demo.gif"
DEFAULT_STILL = REPO_ROOT / "docs" / "images" / "auto-speech-journal-live-demo-still.png"
DEFAULT_SOCIAL = REPO_ROOT / "docs" / "images" / "social-preview.png"

PAPER = "#F8F2E8"
PAPER_RAISED = "#FFFDF8"
INK = "#2F2B27"
INK_MUTED = "#746C63"
ACCENT = "#718C78"
LINE = "#D9CEC0"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _font(candidates: tuple[Path, ...], size: int) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError(f"no usable font found: {candidates}")


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    scale = min(width / image.width, height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", size, PAPER_RAISED)
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def render_social_preview(still_path: Path, output_path: Path) -> None:
    chinese_regular = _font(
        (
            Path(r"C:\Windows\Fonts\msjh.ttc"),
            Path(r"C:\Windows\Fonts\msjhbd.ttc"),
        ),
        28,
    )
    chinese_bold = _font(
        (
            Path(r"C:\Windows\Fonts\msjhbd.ttc"),
            Path(r"C:\Windows\Fonts\msjh.ttc"),
        ),
        54,
    )
    english = _font(
        (
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
        ),
        25,
    )
    chip_font = _font(
        (
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
        ),
        18,
    )

    canvas = Image.new("RGB", (1280, 640), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((1040, -150, 1390, 200), fill="#E6EFE9")
    draw.ellipse((-170, 500, 210, 880), fill="#F1E4D5")

    screenshot = _fit(Image.open(still_path).convert("RGB"), (650, 452))
    screenshot_mask = Image.new("L", screenshot.size, 0)
    ImageDraw.Draw(screenshot_mask).rounded_rectangle(
        (0, 0, screenshot.width - 1, screenshot.height - 1),
        radius=22,
        fill=255,
    )
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((48, 82, 714, 550), radius=28, fill=(47, 43, 39, 55))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")
    canvas.paste(screenshot, (56, 88), screenshot_mask)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((55, 87, 706, 540), radius=23, outline=LINE, width=2)

    text_x = 760
    draw.text((text_x, 112), "聲跡日記", font=chinese_bold, fill=INK)
    draw.text((text_x + 2, 182), "Auto Speech Journal", font=english, fill=INK_MUTED)
    draw.rounded_rectangle((text_x, 241, 1167, 329), radius=18, fill="#E8F0EA")
    draw.text((text_x + 22, 267), "本機辨識 · 錄音不上雲端", font=chinese_regular, fill=INK)
    draw.line((text_x, 376, 1170, 376), fill=LINE, width=2)

    chips = ("Windows 10/11", "Whisper", "Markdown")
    chip_x = text_x
    for label in chips:
        box = draw.textbbox((0, 0), label, font=chip_font)
        chip_width = box[2] - box[0] + 34
        draw.rounded_rectangle(
            (chip_x, 414, chip_x + chip_width, 459),
            radius=22,
            fill=PAPER_RAISED,
            outline=ACCENT,
            width=2,
        )
        draw.text((chip_x + 17, 425), label, font=chip_font, fill=ACCENT)
        chip_x += chip_width + 12

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True, compress_level=9)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--gif", type=Path, default=DEFAULT_GIF)
    parser.add_argument("--still", type=Path, default=DEFAULT_STILL)
    parser.add_argument("--social", type=Path, default=DEFAULT_SOCIAL)
    parser.add_argument("--still-at", type=float, default=15.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for output in (args.gif, args.still, args.social):
        output.parent.mkdir(parents=True, exist_ok=True)

    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{args.still_at:.3f}",
            "-i",
            str(args.video),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(args.still),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(args.video),
            "-filter_complex",
            (
                "[0:v]fps=8,scale=900:-2:flags=lanczos,split[s0][s1];"
                "[s0]palettegen=max_colors=192:stats_mode=diff[p];"
                "[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle[v]"
            ),
            "-map",
            "[v]",
            "-loop",
            "0",
            str(args.gif),
        ]
    )
    render_social_preview(args.still, args.social)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
