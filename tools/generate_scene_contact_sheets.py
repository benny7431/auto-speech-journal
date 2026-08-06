"""Render scene-pack contact sheets for visual QA."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QImageReader,
    QImageWriter,
    QPainter,
    QPen,
)

ROOT = Path(__file__).resolve().parents[1]
SCENES = ROOT / "src" / "auto_speech_journal" / "assets" / "scenes"
DEFAULT_OUTPUT = ROOT / "artifacts" / "imagegen" / "contact-sheets"
MONTHS = tuple(f"{month:02d}" for month in range(1, 13))
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

PAPER = QColor("#f4eddf")
INK = QColor("#463c35")
HAIRLINE = QColor("#cbbfac")
THUMBNAIL = QSize(144, 216)
GAP = 12
HEADER_HEIGHT = 54
LABEL_HEIGHT = 26


def _read_scene(path: Path) -> QImage:
    reader = QImageReader(str(path), b"webp")
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ValueError(f"unable to decode {path.name}: {reader.errorString()}")
    return image


def _fit(image: QImage, size: QSize) -> QImage:
    return image.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _write_png(image: QImage, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = QImageWriter(str(destination), b"png")
    writer.setCompression(6)
    if not writer.write(image):
        raise ValueError(f"unable to write {destination}: {writer.errorString()}")


def render_matrix(*, scene_dir: Path = SCENES, output_dir: Path = DEFAULT_OUTPUT) -> Path:
    width = GAP + len(STATES) * (THUMBNAIL.width() + GAP)
    row_height = LABEL_HEIGHT + THUMBNAIL.height() + GAP
    height = HEADER_HEIGHT + len(MONTHS) * row_height + GAP
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(PAPER)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(INK)
    painter.setFont(QFont("Microsoft JhengHei UI", 13, QFont.Weight.DemiBold))
    painter.drawText(
        QRect(GAP, 0, width - GAP * 2, HEADER_HEIGHT),
        Qt.AlignmentFlag.AlignVCenter,
        "Scene matrix - 12 months x 8 states",
    )

    label_font = QFont("Microsoft JhengHei UI", 8)
    painter.setFont(label_font)
    for column, state in enumerate(STATES):
        x = GAP + column * (THUMBNAIL.width() + GAP)
        painter.drawText(
            QRect(x, HEADER_HEIGHT - LABEL_HEIGHT, THUMBNAIL.width(), LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignCenter,
            state,
        )

    painter.setPen(QPen(HAIRLINE, 1))
    for row, month in enumerate(MONTHS):
        y = HEADER_HEIGHT + row * row_height
        painter.setPen(INK)
        painter.setFont(QFont("Microsoft JhengHei UI", 9, QFont.Weight.DemiBold))
        painter.drawText(
            QRect(GAP, y, width - GAP * 2, LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter,
            f"Month {month}",
        )
        for column, state in enumerate(STATES):
            path = scene_dir / f"{month}-{state}.webp"
            if not path.is_file():
                raise FileNotFoundError(path)
            thumbnail = _fit(_read_scene(path), THUMBNAIL)
            x = GAP + column * (THUMBNAIL.width() + GAP)
            image_y = y + LABEL_HEIGHT
            painter.drawImage(x, image_y, thumbnail)
            painter.setPen(QPen(HAIRLINE, 1))
            painter.drawRect(x, image_y, THUMBNAIL.width() - 1, THUMBNAIL.height() - 1)
    painter.end()

    destination = output_dir / "scene-matrix.png"
    _write_png(canvas, destination)
    return destination


def render_month(
    month: str,
    *,
    scene_dir: Path = SCENES,
    output_dir: Path = DEFAULT_OUTPUT,
) -> Path:
    columns = 4
    rows = 2
    card_width = THUMBNAIL.width() * 2
    card_height = THUMBNAIL.height() * 2
    width = GAP + columns * (card_width + GAP)
    height = HEADER_HEIGHT + rows * (LABEL_HEIGHT + card_height + GAP) + GAP
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(PAPER)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(INK)
    painter.setFont(QFont("Microsoft JhengHei UI", 13, QFont.Weight.DemiBold))
    painter.drawText(
        QRect(GAP, 0, width - GAP * 2, HEADER_HEIGHT),
        Qt.AlignmentFlag.AlignVCenter,
        f"Month {month} - state consistency",
    )
    for index, state in enumerate(STATES):
        row, column = divmod(index, columns)
        x = GAP + column * (card_width + GAP)
        y = HEADER_HEIGHT + row * (LABEL_HEIGHT + card_height + GAP)
        painter.setFont(QFont("Microsoft JhengHei UI", 9))
        painter.drawText(
            QRect(x, y, card_width, LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignCenter,
            state,
        )
        path = scene_dir / f"{month}-{state}.webp"
        if not path.is_file():
            raise FileNotFoundError(path)
        image = _fit(_read_scene(path), QSize(card_width, card_height))
        image_y = y + LABEL_HEIGHT
        painter.drawImage(x, image_y, image)
        painter.setPen(QPen(HAIRLINE, 1))
        painter.drawRect(x, image_y, card_width - 1, card_height - 1)
        painter.setPen(INK)
    painter.end()

    destination = output_dir / f"month-{month}.png"
    _write_png(canvas, destination)
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-dir", type=Path, default=SCENES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    application = QGuiApplication.instance() or QGuiApplication([])

    print(render_matrix(scene_dir=args.scene_dir, output_dir=args.output_dir))
    for month in MONTHS:
        print(
            render_month(
                month,
                scene_dir=args.scene_dir,
                output_dir=args.output_dir,
            )
        )
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
