from __future__ import annotations

from pathlib import Path

from auto_speech_journal.config import DEFAULT_UI_FONT_FAMILY, DEFAULT_UI_FONT_SIZE
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.ui_models import FONT_DIRECTORY_ENV, _font_directories

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FONT_DIR = ROOT / "src" / "auto_speech_journal" / "assets" / "fonts"


def test_proprietary_fonts_are_not_package_assets() -> None:
    assert not PACKAGE_FONT_DIR.exists()


def test_external_font_search_uses_runtime_and_source_folders(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "chosen-fonts"
    monkeypatch.setenv(FONT_DIRECTORY_ENV, str(override))

    directories = _font_directories()

    assert directories[0] == override.resolve()
    assert AppPaths.defaults().fonts_dir.resolve() in directories
    assert (ROOT / "字體").resolve() in directories


def test_default_appearance_prefers_legible_local_ink_font() -> None:
    assert DEFAULT_UI_FONT_FAMILY == "SentyCreek"
    assert DEFAULT_UI_FONT_SIZE == 18


def test_decorative_ripple_qml_is_not_packaged() -> None:
    qml_dir = ROOT / "src" / "auto_speech_journal" / "qml"
    assert not (qml_dir / "Waveform.qml").exists()
    assert not (qml_dir / "PaperEcho.qml").exists()
