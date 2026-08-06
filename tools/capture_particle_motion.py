"""Capture two hardware-rendered sound-river frames and prove motion occurred."""

from __future__ import annotations

import argparse
import hashlib
import time
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImageWriter
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "src" / "auto_speech_journal" / "qml" / "AmbientSoundRiver.qml"
DEFAULT_OUTPUT = ROOT / "artifacts" / "today-river-production-v2" / "particle-motion"


def _write_png(image, path: Path) -> None:
    writer = QImageWriter(str(path), b"png")
    writer.setCompression(6)
    if not writer.write(image):
        raise ValueError(f"unable to write {path}: {writer.errorString()}")


def capture(output_dir: Path, *, soak_seconds: int = 0) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QGuiApplication.instance() or QGuiApplication([])
    view = QQuickView()
    view.setColor(QColor("#334A48"))
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(QSize(360, 720))
    view.setSource(QUrl.fromLocalFile(str(QML)))
    if view.status() == QQuickView.Status.Error:
        raise ValueError("\n".join(error.toString() for error in view.errors()))
    root = view.rootObject()
    if root is None:
        raise ValueError("AmbientSoundRiver root object was not created")
    root.setProperty("sceneKey", "capturing")
    root.setProperty("speechActive", True)
    root.setProperty("audioLevel", 1.0)
    root.setProperty("motionEnabled", True)
    root.setProperty("reducedMotion", False)
    view.show()
    QTest.qWait(1200)
    if not root.property("particleBackendAllowed"):
        raise ValueError("the active scene-graph backend is not on the particle allowlist")
    if not root.property("particleLayerLoaded"):
        raise ValueError("the hardware particle layer did not load")
    initial_layers = len(root.findChildren(QObject, "todayParticleLayer"))
    initial_systems = len(root.findChildren(QObject, "todayParticleSystem"))
    if (initial_layers, initial_systems) != (1, 1):
        raise ValueError("the hardware particle graph contains duplicate layers")

    first = view.grabWindow()
    QTest.qWait(1600)
    second = view.grabWindow()
    if first.isNull() or second.isNull():
        raise ValueError("hardware frame capture returned a null image")
    first_path = output_dir / "particle-frame-1.png"
    second_path = output_dir / "particle-frame-2.png"
    _write_png(first, first_path)
    _write_png(second, second_path)
    if hashlib.sha256(first_path.read_bytes()).digest() == hashlib.sha256(
        second_path.read_bytes()
    ).digest():
        raise ValueError("time-separated hardware particle frames are identical")
    deadline = time.monotonic() + max(0, soak_seconds)
    while time.monotonic() < deadline:
        QTest.qWait(min(1000, max(1, round((deadline - time.monotonic()) * 1000))))
        if not root.property("particleLayerLoaded"):
            raise ValueError("the hardware particle layer unloaded during the soak")
    if len(root.findChildren(QObject, "todayParticleLayer")) != initial_layers or len(
        root.findChildren(QObject, "todayParticleSystem")
    ) != initial_systems:
        raise ValueError("the particle graph accumulated objects during the soak")
    view.close()
    application.processEvents()
    return first_path, second_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--soak-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        first, second = capture(args.output_dir, soak_seconds=args.soak_seconds)
    except ValueError as error:
        print(f"Particle motion capture failed: {error}")
        return 1
    print(f"Particle motion capture passed: {first}, {second}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
