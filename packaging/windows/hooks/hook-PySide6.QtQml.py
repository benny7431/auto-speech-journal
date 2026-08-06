"""Limit the frozen QML tree to modules imported by Auto Speech Journal.

PyInstaller's stock QtQml hook collects every QML module shipped by PySide6,
including WebEngine and Quick3D. The application imports only QtQml/QtQuick;
keeping those complete subtrees preserves their styles and transitive QML
dependencies without shipping unrelated Qt products.
"""

from pathlib import Path, PurePath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


_REQUIRED_MODULES = (
    "QtQml",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtQuick",
    "QtQuick/Controls",
    "QtQuick/Controls/impl",
    "QtQuick/Controls/Basic",
    "QtQuick/Controls/Basic/impl",
    "QtQuick/Layouts",
    "QtQuick/Particles",
    "QtQuick/Templates",
    "QtQuick/Window",
)


qml_source = Path(pyside6_library_info.location["QmlImportsPath"]).resolve()
qml_destination = PurePath(pyside6_library_info.qt_rel_dir) / "qml"

for module_name in _REQUIRED_MODULES:
    qmldir = qml_source.joinpath(*module_name.split("/"), "qmldir")
    if not qmldir.is_file():
        raise RuntimeError(f"Required PySide6 QML module is missing: {module_name}")
    plugin_binaries, plugin_datas = pyside6_library_info._process_qml_plugin(qmldir)
    module_destination = str(qml_destination / PurePath(module_name))
    binaries += [(str(source), module_destination) for source in plugin_binaries]
    datas += [(str(source), module_destination) for source in plugin_datas]
