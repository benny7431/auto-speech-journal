from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine

QML_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "auto_speech_journal" / "qml"
)
PARTICLE_DIR = QML_DIR.parent / "assets" / "particles"


def _create_qml_component(qml_name: str) -> tuple[QQmlEngine, QQmlComponent, QObject]:
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / qml_name)))
    assert not component.isError(), "\n".join(error.toString() for error in component.errors())
    instance = component.create()
    assert instance is not None, "\n".join(error.toString() for error in component.errors())
    return engine, component, instance


def test_particle_import_is_isolated_behind_hardware_loader() -> None:
    wrapper_source = (QML_DIR / "AmbientSoundRiver.qml").read_text(encoding="utf-8")
    particle_source = (QML_DIR / "TodayParticleLayer.qml").read_text(encoding="utf-8")

    assert "import QtQuick.Particles" not in wrapper_source
    assert 'source: "TodayParticleLayer.qml"' in wrapper_source
    assert "active: root.effectRunning && root.particleBackendAllowed" in wrapper_source
    assert "GraphicsInfo.Software" not in wrapper_source
    assert "GraphicsInfo.Direct3D11" in wrapper_source
    assert "GraphicsInfo.OpenGL" in wrapper_source
    assert "import QtQuick.Particles" in particle_source
    assert "ImageParticle" in particle_source
    assert "Wander" in particle_source
    assert "TrailEmitter" in particle_source


def test_particle_emitters_have_a_bounded_active_budget() -> None:
    source = (QML_DIR / "TodayParticleLayer.qml").read_text(encoding="utf-8")
    emitter_caps = [
        int(value) for value in re.findall(r"maximumEmitted:\s*(\d+)", source)
    ]

    assert emitter_caps == [44, 18, 4, 38]
    assert sum(emitter_caps) == 104
    assert sum(emitter_caps) <= 120
    assert "ShaderEffect" not in source


def test_particle_sprite_assets_are_decodable_transparent_pngs() -> None:
    for name in ("mist-mote.png", "glow-mote.png", "soft-ripple.png"):
        image = QImage(str(PARTICLE_DIR / name))
        assert not image.isNull()
        assert (image.width(), image.height()) == (64, 64)
        assert image.hasAlphaChannel()

    wrapper_source = (QML_DIR / "AmbientSoundRiver.qml").read_text(encoding="utf-8")
    particle_source = (QML_DIR / "TodayParticleLayer.qml").read_text(encoding="utf-8")
    assert "mist-mote.png" in wrapper_source
    assert "data:image/png;base64" not in wrapper_source
    for name in ("mist-mote.png", "glow-mote.png", "soft-ripple.png"):
        assert name in particle_source
    assert "data:image/png;base64" not in particle_source


def test_qml_components_import_with_the_software_backend(qapp) -> None:
    wrapper_engine, wrapper_component, wrapper = _create_qml_component(
        "AmbientSoundRiver.qml"
    )
    particle_engine, particle_component, particle = _create_qml_component(
        "TodayParticleLayer.qml"
    )

    assert wrapper.property("particleBackendAllowed") is False
    loader = wrapper.findChild(QObject, "todayParticleLoader")
    assert loader is not None
    assert loader.property("active") is False
    assert wrapper.property("particleLayerLoaded") is False
    assert particle.property("particleBudget") == 104

    particle.deleteLater()
    particle_component.deleteLater()
    particle_engine.deleteLater()
    wrapper.deleteLater()
    wrapper_component.deleteLater()
    wrapper_engine.deleteLater()


def test_reduced_motion_disables_every_animation_and_keeps_static_fallback(qapp) -> None:
    engine, component, wrapper = _create_qml_component("AmbientSoundRiver.qml")
    wrapper.setProperty("motionEnabled", True)
    wrapper.setProperty("reducedMotion", True)
    qapp.processEvents()

    loader = wrapper.findChild(QObject, "todayParticleLoader")
    fallback = wrapper.findChild(QObject, "softwareRiverFallback")
    assert wrapper.property("effectRunning") is False
    assert wrapper.property("fallbackAnimationRunning") is False
    assert loader.property("active") is False
    assert fallback.property("visible") is True

    wrapper.deleteLater()
    component.deleteLater()
    engine.deleteLater()
