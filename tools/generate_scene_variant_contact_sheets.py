"""Render the 192-entry v2 scene pack as compact visual-QA sheets."""

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
DEFAULT_SCENES = ROOT / "artifacts" / "today-river-production-v2" / "matrix"
DEFAULT_OUTPUT = ROOT / "artifacts" / "today-river-production-v2" / "contact-sheets"
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
VARIANTS = ("compact", "workspace")
PAPER = QColor("#f4eddf")
INK = QColor("#463c35")
HAIRLINE = QColor("#cbbfac")
GAP = 10
HEADER_HEIGHT = 48
LABEL_HEIGHT = 24
MATRIX_THUMBNAILS = {
    "compact": QSize(160, 120),
    "workspace": QSize(180, 120),
}
MONTH_CARD = QSize(300, 225)


def _read(path: Path) -> QImage:
    reader = QImageReader(str(path), b"webp")
    reader.setDecideFormatFromContent(True)
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


def _write(image: QImage, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = QImageWriter(str(destination), b"png")
    writer.setCompression(6)
    if not writer.write(image):
        raise ValueError(f"unable to write {destination}: {writer.errorString()}")
    return destination


def render_matrix(variant: str, *, scene_dir: Path, output_dir: Path) -> Path:
    thumbnail = MATRIX_THUMBNAILS[variant]
    width = GAP + len(STATES) * (thumbnail.width() + GAP)
    row_height = LABEL_HEIGHT + thumbnail.height() + GAP
    height = HEADER_HEIGHT + len(MONTHS) * row_height + GAP
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(PAPER)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(INK)
    painter.setFont(QFont("Microsoft JhengHei UI", 12, QFont.Weight.DemiBold))
    painter.drawText(
        QRect(GAP, 0, width - 2 * GAP, HEADER_HEIGHT),
        Qt.AlignmentFlag.AlignVCenter,
        f"Today river v2 - {variant} - 12 months x 8 states",
    )
    painter.setFont(QFont("Microsoft JhengHei UI", 8))
    for column, state in enumerate(STATES):
        x = GAP + column * (thumbnail.width() + GAP)
        painter.drawText(
            QRect(x, HEADER_HEIGHT - LABEL_HEIGHT, thumbnail.width(), LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignCenter,
            state,
        )
    for row, month in enumerate(MONTHS):
        y = HEADER_HEIGHT + row * row_height
        painter.setPen(INK)
        painter.setFont(QFont("Microsoft JhengHei UI", 8, QFont.Weight.DemiBold))
        painter.drawText(
            QRect(GAP, y, width - 2 * GAP, LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter,
            f"Month {month}",
        )
        for column, state in enumerate(STATES):
            path = scene_dir / f"{month}-{state}-{variant}.webp"
            if not path.is_file():
                raise FileNotFoundError(path)
            image = _fit(_read(path), thumbnail)
            x = GAP + column * (thumbnail.width() + GAP)
            image_y = y + LABEL_HEIGHT
            painter.drawImage(x, image_y, image)
            painter.setPen(QPen(HAIRLINE, 1))
            painter.drawRect(x, image_y, image.width() - 1, image.height() - 1)
    painter.end()
    return _write(canvas, output_dir / f"scene-matrix-{variant}.png")


def render_month(
    month: str,
    variant: str,
    *,
    scene_dir: Path,
    output_dir: Path,
) -> Path:
    columns = 4
    rows = 2
    width = GAP + columns * (MONTH_CARD.width() + GAP)
    row_height = LABEL_HEIGHT + MONTH_CARD.height() + GAP
    height = HEADER_HEIGHT + rows * row_height + GAP
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(PAPER)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(INK)
    painter.setFont(QFont("Microsoft JhengHei UI", 12, QFont.Weight.DemiBold))
    painter.drawText(
        QRect(GAP, 0, width - 2 * GAP, HEADER_HEIGHT),
        Qt.AlignmentFlag.AlignVCenter,
        f"Month {month} - {variant} state consistency",
    )
    for index, state in enumerate(STATES):
        row, column = divmod(index, columns)
        x = GAP + column * (MONTH_CARD.width() + GAP)
        y = HEADER_HEIGHT + row * row_height
        painter.setFont(QFont("Microsoft JhengHei UI", 9))
        painter.drawText(
            QRect(x, y, MONTH_CARD.width(), LABEL_HEIGHT),
            Qt.AlignmentFlag.AlignCenter,
            state,
        )
        path = scene_dir / f"{month}-{state}-{variant}.webp"
        if not path.is_file():
            raise FileNotFoundError(path)
        image = _fit(_read(path), MONTH_CARD)
        image_y = y + LABEL_HEIGHT + (MONTH_CARD.height() - image.height()) // 2
        image_x = x + (MONTH_CARD.width() - image.width()) // 2
        painter.drawImage(image_x, image_y, image)
        painter.setPen(QPen(HAIRLINE, 1))
        painter.drawRect(image_x, image_y, image.width() - 1, image.height() - 1)
        painter.setPen(INK)
    painter.end()
    return _write(canvas, output_dir / f"month-{month}-{variant}.png")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-dir", type=Path, default=DEFAULT_SCENES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    application = QGuiApplication.instance() or QGuiApplication([])
    for variant in VARIANTS:
        print(render_matrix(variant, scene_dir=args.scene_dir, output_dir=args.output_dir))
        for month in MONTHS:
            print(
                render_month(
                    month,
                    variant,
                    scene_dir=args.scene_dir,
                    output_dir=args.output_dir,
                )
            )
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
