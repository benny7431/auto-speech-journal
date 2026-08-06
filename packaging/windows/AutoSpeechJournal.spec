# -*- mode: python ; coding: utf-8 -*-
"""Build the GUI and CLI into one CPU-first onedir payload."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).parents[1]
SOURCE_ROOT = ROOT / "src"
HOOK_ROOT = Path(SPECPATH) / "hooks"
GUI_VERSION_FILE = os.environ.get("ASJ_GUI_VERSION_FILE")
CLI_VERSION_FILE = os.environ.get("ASJ_CLI_VERSION_FILE")
if not GUI_VERSION_FILE or not CLI_VERSION_FILE:
    raise RuntimeError("PyInstaller version-resource paths were not provided")
ICON_FILE = os.environ.get("ASJ_ICON_FILE") or None


def package_payload(name):
    return collect_all(name)


datas = collect_data_files(
    "auto_speech_journal",
    includes=["qml/**", "assets/**", "runtime-models-v1.json"],
    excludes=["assets/fonts/**", "models/**"],
)
binaries = []
hiddenimports = []

for distribution in (
    "auto-speech-journal",
    "ctranslate2",
    "faster-whisper",
    "huggingface-hub",
    "onnxruntime",
    "opencc",
    "sherpa-onnx",
):
    datas += copy_metadata(distribution)

for package in (
    "sherpa_onnx",
    "ctranslate2",
    "opencc",
    "huggingface_hub",
    "hf_xet",
):
    package_datas, package_binaries, package_hiddenimports = package_payload(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


def is_nvidia_runtime(entry):
    names = {Path(str(value)).name.casefold() for value in entry[:2]}
    prefixes = ("cublas", "cudnn", "nvcuda", "nvjitlink", "nvrtc")
    return any(name.endswith(".dll") and name.startswith(prefixes) for name in names)


binaries = [entry for entry in binaries if not is_nvidia_runtime(entry)]
datas = [entry for entry in datas if not is_nvidia_runtime(entry)]

hiddenimports += collect_submodules("PySide6.QtQml")
hiddenimports += collect_submodules("PySide6.QtQuick")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtWidgets",
    "huggingface_hub",
    "hf_xet",
    "numpy",
    "onnxruntime",
    "soundfile",
    "sounddevice",
    "soxr",
    "tzdata",
]

excludes = [
    "PIL",
    "PyInstaller",
    "coverage",
    "nvidia",
    "pre_commit",
    "pygments",
    "safetensors",
    "torch",
    "transformers",
    "pytest",
    "pytest_cov",
    "ruff",
]

gui_analysis = Analysis(
    [str(Path(SPECPATH) / "gui_entry.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(HOOK_ROOT)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
cli_analysis = Analysis(
    [str(Path(SPECPATH) / "cli_entry.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(HOOK_ROOT)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

MERGE((gui_analysis, "gui", "gui"), (cli_analysis, "cli", "cli"))

gui_pyz = PYZ(gui_analysis.pure)
cli_pyz = PYZ(cli_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="AutoSpeechJournal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=ICON_FILE,
    version=GUI_VERSION_FILE,
)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="AutoSpeechJournal.CLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=ICON_FILE,
    version=CLI_VERSION_FILE,
)

collect = COLLECT(
    gui_exe,
    cli_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    name="AutoSpeechJournal",
)
