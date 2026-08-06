"""Generate the small transparent PNG sprites used by the sound-river effect."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "src" / "auto_speech_journal" / "assets" / "particles"
SPRITE_SIZE = 64


def _canvas() -> QImage:
    image = QImage(SPRITE_SIZE, SPRITE_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    return image


def _write_radial(name: str, stops: tuple[tuple[float, QColor], ...]) -> None:
    image = _canvas()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QRadialGradient(SPRITE_SIZE / 2, SPRITE_SIZE / 2, SPRITE_SIZE * 0.46)
    for position, color in stops:
        gradient.setColorAt(position, color)
    painter.fillRect(image.rect(), gradient)
    painter.end()
    _save(image, name)


def _write_ripple() -> None:
    image = _canvas()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 255, 255, 150), 2.0))
    painter.drawEllipse(QRectF(5, 20, 54, 24))
    painter.setPen(QPen(QColor(255, 255, 255, 72), 1.0))
    painter.drawEllipse(QRectF(12, 24, 40, 16))
    painter.end()
    _save(image, "soft-ripple.png")


def _save(image: QImage, name: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_DIR / name
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"unable to save {destination}")


def main() -> int:
    _write_radial(
        "mist-mote.png",
        (
            (0.0, QColor(255, 255, 255, 96)),
            (0.45, QColor(255, 255, 255, 46)),
            (1.0, QColor(255, 255, 255, 0)),
        ),
    )
    _write_radial(
        "glow-mote.png",
        (
            (0.0, QColor(255, 248, 220, 230)),
            (0.22, QColor(255, 244, 207, 150)),
            (1.0, QColor(255, 244, 207, 0)),
        ),
    )
    _write_ripple()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
