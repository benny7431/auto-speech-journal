from __future__ import annotations

import math
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .controller import JournalController
from .ui_models import (
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    EXPANDED_HEIGHT,
    EXPANDED_MIN_HEIGHT,
    EXPANDED_MIN_WIDTH,
    EXPANDED_WIDTH,
    POLL_INTERVAL_MS,
    JournalViewModel,
)

AUDIO_STALE_SECONDS = 0.5
COMPACT_CORNER_RADIUS = 14.0
EXPANDED_CORNER_RADIUS = 18.0
WINDOWS_11_BUILD = 22000
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
BACKGROUND = "#F4EEE3"
TEXT = "#493F35"
SIGNAL = "#718C78"
WARNING = "#B88647"
ERROR = "#B85C4A"
PAUSED = "#8B8377"
SYSTEM_UI_FONT_FAMILY = "Microsoft JhengHei UI"


def _level_from_dbfs(value: float | int | None) -> float:
    """Map an audio level into a stable 0..1 visual range."""
    try:
        dbfs = float(value) if value is not None else -90.0
    except (TypeError, ValueError):
        dbfs = -90.0
    if not math.isfinite(dbfs):
        dbfs = -90.0
    return max(0.0, min(1.0, (dbfs + 60.0) / 54.0))


def _audio_age_seconds(value: object, *, now: datetime | None = None) -> float:
    if value is None:
        return math.inf
    timestamp: datetime
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return math.inf
    else:
        return math.inf
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (current - timestamp.astimezone(UTC)).total_seconds())


def _rounded_window_region(width: int, height: int, radius: float) -> Any:
    """Build the integer fallback mask used when DWM rounded corners are unavailable."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath, QRegion

    if width <= 0 or height <= 0:
        return QRegion()
    bounded_radius = max(0.0, min(float(radius), width / 2, height / 2))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), bounded_radius, bounded_radius)
    return QRegion(path.toFillPolygon().toPolygon())


def _windows_build_number() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except (AttributeError, OSError, ValueError):
        return 0


def _set_dwm_rounded_corners(window: Any) -> bool:
    """Request compositor-antialiased corners for the window's current HWND."""
    if _windows_build_number() < WINDOWS_11_BUILD:
        return False

    import ctypes

    try:
        hwnd = int(window.winId())
        if not hwnd:
            return False
        preference = ctypes.c_int(DWMWCP_ROUND)
        dwmapi = ctypes.WinDLL("dwmapi")
        setter = dwmapi.DwmSetWindowAttribute
        setter.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        setter.restype = ctypes.c_long
        result = setter(
            ctypes.c_void_p(hwnd),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return result == 0


def _apply_rounded_window_corners(window: Any, radius: float) -> str:
    """Prefer smooth DWM clipping and retain a Windows 10/offscreen fallback."""
    from PySide6.QtGui import QRegion

    # SetWindowRgn disables DWM corner rendering, so remove any previous fallback first.
    window.setMask(QRegion())
    if _set_dwm_rounded_corners(window):
        return "dwm"
    window.setMask(_rounded_window_region(window.width(), window.height(), radius))
    return "mask"


def _install_rounded_window_corners(window: Any, view_model: Any) -> None:
    """Reapply corners whenever Qt recreates the HWND while changing window flags."""
    from PySide6.QtCore import QEvent, QObject, QTimer

    def apply_corners() -> None:
        radius = EXPANDED_CORNER_RADIUS if view_model.expanded else COMPACT_CORNER_RADIUS
        window._rounded_corner_backend = _apply_rounded_window_corners(window, radius)

    update_timer = QTimer(window)
    update_timer.setSingleShot(True)
    update_timer.timeout.connect(apply_corners)

    def schedule_corner_update(*_args: object) -> None:
        update_timer.start(0)

    class WindowSurfaceObserver(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if watched is window and event.type() in {
                QEvent.Type.PlatformSurface,
                QEvent.Type.WinIdChange,
                QEvent.Type.Show,
                QEvent.Type.Resize,
            }:
                schedule_corner_update()
            return False

    observer = WindowSurfaceObserver(window)
    window.installEventFilter(observer)
    view_model.expandedChanged.connect(schedule_corner_update)
    window.widthChanged.connect(schedule_corner_update)
    window.heightChanged.connect(schedule_corner_update)
    window.visibleChanged.connect(schedule_corner_update)
    window._rounded_corner_timer = update_timer
    window._rounded_corner_observer = observer
    window._apply_rounded_corners = apply_corners
    apply_corners()


def _configure_application(application: Any) -> None:
    from PySide6.QtGui import QFont, QIcon

    application.setApplicationName("聲跡日記")
    application.setOrganizationName("AutoSpeechJournal")
    application.setQuitOnLastWindowClosed(False)
    QFont.insertSubstitution("Noto Sans TC", "Microsoft JhengHei UI")
    QFont.insertSubstitution("Noto Serif TC", "Microsoft JhengHei UI")
    application.setFont(QFont(SYSTEM_UI_FONT_FAMILY))
    brand_mark = (
        Path(__file__).resolve().parent
        / "assets"
        / "brand"
        / "journal-ink-icon.png"
    )
    application.setWindowIcon(QIcon(str(brand_mark)))
    try:
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("Basic")
    except RuntimeError:
        # A host process may already have loaded Qt Quick Controls.
        pass


def _create_main_window(
    controller: JournalController,
    application: Any,
    *,
    window_settings: Any | None = None,
    microphone_device_provider: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    font_directories: Sequence[Path] | None = None,
) -> Any:
    """Create the QML window without entering Qt's event loop."""
    try:
        from PySide6.QtCore import QSettings, QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickWindow
    except ImportError as error:  # pragma: no cover - packaging failure
        raise RuntimeError("PySide6 未安裝；請重新執行 install.ps1") from error

    engine = QQmlApplicationEngine()
    settings = window_settings or QSettings("AutoSpeechJournal", "Desktop")
    view_model = JournalViewModel(
        controller,
        application,
        settings=settings,
        clock=clock,
        font_directories=font_directories,
        microphone_device_provider=microphone_device_provider,
    )
    engine.rootContext().setContextProperty("journal", view_model)
    qml_path = Path(__file__).resolve().parent / "qml" / "JournalWindow.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QQuickWindow):
        raise RuntimeError(f"無法載入聲跡日記介面：{qml_path}")

    window = roots[0]
    window._qml_engine = engine
    window._journal_view_model = view_model
    view_model.attach_window(window)
    _install_rounded_window_corners(window, view_model)
    view_model.activate()
    application.aboutToQuit.connect(view_model.shutdown)
    return window


def run_ui(controller: JournalController, argv: Sequence[str] | None = None) -> int:
    """Run the local-first compact recorder and its expanded journal workspace."""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError as error:  # pragma: no cover - packaging failure
        raise RuntimeError("PySide6 未安裝；請重新執行 install.ps1") from error

    application = QApplication.instance() or QApplication(list(argv or sys.argv))
    _configure_application(application)
    window = _create_main_window(controller, application)
    window.show()

    QTimer.singleShot(0, window._journal_view_model.startControllerIfReady)
    return int(application.exec())


__all__ = [
    "COMPACT_HEIGHT",
    "COMPACT_WIDTH",
    "EXPANDED_HEIGHT",
    "EXPANDED_MIN_HEIGHT",
    "EXPANDED_MIN_WIDTH",
    "EXPANDED_WIDTH",
    "POLL_INTERVAL_MS",
    "SYSTEM_UI_FONT_FAMILY",
    "run_ui",
]
