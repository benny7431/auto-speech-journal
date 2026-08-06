from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QSettings, QSize, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_speech_journal.audio import InputDevice
from auto_speech_journal.config import (
    AppConfig,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.controller import ControllerSnapshot
from auto_speech_journal.timeline import (
    DayTimelineView,
    TimelineHourView,
    TimelineSegmentView,
)
from auto_speech_journal.types import InputRoute, SegmentState, Severity, WorkerState
from auto_speech_journal.ui import (
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    POLL_INTERVAL_MS,
    SYSTEM_UI_FONT_FAMILY,
    _apply_rounded_window_corners,
    _audio_age_seconds,
    _configure_application,
    _create_main_window,
    _level_from_dbfs,
    _rounded_window_region,
)


class FakeController:
    def __init__(self, records_root: str) -> None:
        self.config = AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
            records_root=records_root,
        )
        self.snapshot = ControllerSnapshot(
            state=WorkerState.RECORDING,
            message="正在錄音",
            partial_text="現在說到這裡",
            timeline_revision=1,
        )
        self.segments = [
            TimelineSegmentView(
                segment_id="morning-1",
                time_label="[09:02:03]",
                text="早上的第一段聲跡",
                status_label="已定稿",
                editable=True,
                hour_key="2026-07-12_09",
                state=SegmentState.FINAL_READY,
            ),
            TimelineSegmentView(
                segment_id="morning-2",
                time_label="[09:14:20]",
                text="還在整理中的片段",
                status_label="待定稿",
                editable=True,
                hour_key="2026-07-12_09",
                state=SegmentState.FINALIZING,
            ),
        ]
        self.calls: list[object] = []
        self.vocabulary_counts: dict[str, int] = {}
        self.vocabulary_learning_error: Exception | None = None

    def tick(self) -> None:
        self.calls.append("tick")

    def timeline_for_date(self, day) -> DayTimelineView:
        return DayTimelineView(
            day_key=day.isoformat(),
            hours=(
                TimelineHourView(
                    hour_key="2026-07-12_09",
                    label="09:00",
                    segments=tuple(self.segments),
                ),
            ),
        )

    def toggle_pause(self) -> None:
        self.calls.append("toggle_pause")
        paused = not self.snapshot.paused
        self.snapshot = replace(
            self.snapshot,
            paused=paused,
            state=WorkerState.PAUSED if paused else WorkerState.RECORDING,
        )

    def correct_segment(self, segment_id: str, text: str) -> None:
        self.calls.append(("correct", segment_id, text))
        self.segments = [
            replace(
                segment,
                text=text,
                status_label="已修正",
                state=SegmentState.FINAL_READY,
            )
            if segment.segment_id == segment_id
            else segment
            for segment in self.segments
        ]
        self.snapshot = replace(
            self.snapshot,
            timeline_revision=self.snapshot.timeline_revision + 1,
        )

    def available_hours(self) -> list[str]:
        return ["2026-07-12_09", "2026-07-11_18"]

    def delete_hour(self, hour_key: str) -> None:
        self.calls.append(("delete", hour_key))
        self.segments.clear()
        self.snapshot = replace(
            self.snapshot,
            timeline_revision=self.snapshot.timeline_revision + 1,
        )

    def open_records_folder(self) -> None:
        self.calls.append("open_folder")

    def open_settings_history_file(self) -> None:
        self.calls.append("open_settings_history")

    def update_settings(self, config: AppConfig) -> None:
        self.calls.append(("settings", config))
        self.config = config

    def configure_microphone(self, selection: MicrophoneSelection) -> None:
        self.calls.append(("configure_microphone", selection))
        self.config = replace(self.config, microphone=selection)

    def skip_microphone_setup(self) -> None:
        self.calls.append("skip_microphone_setup")
        self.config = replace(
            self.config,
            microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
        )

    def retry_preferred_input(self) -> None:
        self.calls.append("retry_preferred_input")

    def start(self) -> None:
        self.calls.append("start")

    def learned_vocabulary(self) -> dict[str, int]:
        return dict(self.vocabulary_counts)

    def delete_vocabulary_term(self, term: str) -> bool:
        self.calls.append(("delete_vocabulary", term))
        return self.vocabulary_counts.pop(term, None) is not None

    def clear_vocabulary(self) -> int:
        self.calls.append("clear_vocabulary")
        count = len(self.vocabulary_counts)
        self.vocabulary_counts.clear()
        return count

    def set_vocabulary_learning_enabled(self, enabled: bool) -> None:
        self.calls.append(("vocabulary_learning", enabled))
        if self.vocabulary_learning_error is not None:
            raise self.vocabulary_learning_error
        self.config = replace(self.config, vocabulary_learning_enabled=enabled)

    def report_ui_error(self, message: str) -> None:
        self.calls.append(("error", message))

    def stop(self, *, suppress_errors: bool = False) -> None:
        self.calls.append(("stop", suppress_errors))


@pytest.fixture
def journal_window(qtbot, tmp_path):
    application = QApplication.instance()
    assert application is not None
    _configure_application(application)
    controller = FakeController(str(tmp_path))
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    settings.clear()
    window = _create_main_window(
        controller,
        application,
        window_settings=settings,
        microphone_device_provider=lambda: [],
    )
    window.show()
    qtbot.waitUntil(window.isVisible)
    yield window, controller, settings
    view_model = window._journal_view_model
    view_model._poll_timer.stop()
    view_model._allow_close = True
    window.close()
    window.deleteLater()


def _role(model, name: str) -> int:
    return next(role for role, role_name in model.roleNames().items() if role_name == name.encode())


def _default_input_device() -> InputDevice:
    return InputDevice(
        index=2,
        name="Built-in Mic",
        host_api="Windows WASAPI",
        endpoint_id="wasapi:windows wasapi:built-in mic",
        default_sample_rate=48_000,
        max_input_channels=1,
        is_default=True,
    )


def _rectangle_border_width(item: QObject) -> float:
    pen = next(child for child in item.children() if child.inherits("QQuickPen"))
    return float(pen.property("width"))


def _pixel_size(item: QObject) -> int:
    return int(item.property("font").pixelSize())


def _assert_button_content_fits(button: QObject) -> None:
    content = button.property("contentItem")
    assert content is not None
    content_width = float(content.property("implicitWidth"))
    available_width = float(button.property("availableWidth"))
    assert content_width <= available_width + 0.5


def _assert_guarded_button_content_fits(window: QObject) -> None:
    guarded = [
        item
        for item in window.findChildren(QObject)
        if item.property("contentWidthGuard") is True
        and float(item.property("width") or 0) > 0
    ]
    assert guarded
    for button in guarded:
        _assert_button_content_fits(button)


def _assert_confirmation_content_fits(window: QObject) -> None:
    card = window.findChild(QObject, "confirmationCard")
    content = window.findChild(QObject, "confirmationContent")
    message = window.findChild(QObject, "confirmationMessage")
    assert float(message.property("contentHeight")) <= float(message.property("height")) + 0.5
    required_height = float(content.property("implicitHeight")) + (
        float(card.property("contentMargin")) * 2
    )
    assert required_height <= float(card.property("height")) + 0.5
    assert float(card.property("height")) <= float(window.property("height")) - 8


def _click_quick_item(window: QWindow, item: QObject) -> None:
    center = item.mapToScene(
        QPointF(
            float(item.property("width")) / 2,
            float(item.property("height")) / 2,
        )
    )
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(round(center.x()), round(center.y())),
    )


def _visual_items(window, object_name: str) -> list[QObject]:
    matches: list[QObject] = []
    pending = [window.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == object_name:
            matches.append(item)
        pending.extend(item.childItems())
    return matches


def test_pending_microphone_setup_saves_and_starts_on_explicit_choice(qtbot, tmp_path):
    application = QApplication.instance()
    assert application is not None
    controller = FakeController(str(tmp_path))
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
    )
    settings = QSettings(str(tmp_path / "setup-window.ini"), QSettings.Format.IniFormat)
    window = _create_main_window(
        controller,
        application,
        window_settings=settings,
        microphone_device_provider=lambda: [_default_input_device()],
    )
    window.show()
    qtbot.waitUntil(window.isVisible)

    view_model = window._journal_view_model
    overlay = window.findChild(QObject, "microphoneSetupOverlay")
    picker = window.findChild(QObject, "setupMicrophonePicker")
    skip_button = window.findChild(QObject, "setupMicrophoneSkipButton")
    assert overlay.property("visible") is True
    assert picker.property("currentIndex") == -1
    assert window.findChild(QObject, "setupMicrophoneContinueButton") is None
    assert window.findChild(QObject, "setupMicrophoneTestButton") is None
    assert skip_button.property("visible") is False
    assert window.width() == 560
    assert window.height() == 480

    assert view_model.selectMicrophone("system_default") is True
    qtbot.waitUntil(
        lambda: controller.config.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT
    )
    qtbot.waitUntil(lambda: "start" in controller.calls)

    assert overlay.property("visible") is False
    view_model._poll_timer.stop()
    view_model._allow_close = True
    window.close()
    window.deleteLater()


def test_settings_sheet_exposes_microphone_controls(journal_window, qtbot):
    window, _, _ = journal_window
    window._journal_view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    qtbot.waitUntil(
        lambda: window.findChild(QObject, "microphonePicker") is not None
    )

    for object_name in (
        "microphonePicker",
        "microphoneRescanButton",
        "microphoneTestButton",
        "microphoneRouteStatus",
        "microphoneFallbackWarning",
        "retryPreferredInputButton",
        "retryRecordingEngineButton",
        "deferMicrophoneAfterFailureButton",
        "settingsSaveButton",
    ):
        assert window.findChild(QObject, object_name) is not None


def test_microphone_status_shows_active_input_once_when_route_is_healthy(
    journal_window,
    qtbot,
):
    window, controller, _ = journal_window
    controller.snapshot = replace(
        controller.snapshot,
        preferred_input_name="麥克風 (FXR-HUM-15)",
        active_input_name="麥克風 (FXR-HUM-15)",
        input_route=InputRoute.PREFERRED,
        preferred_input_available=True,
    )
    window._journal_view_model.refresh()

    route_status = window.findChild(QObject, "microphoneRouteStatus")
    system_status = window.findChild(QObject, "systemMicrophoneStatus")
    qtbot.waitUntil(lambda: system_status.property("text").count("目前收音：") == 1)

    assert route_status.property("text") == ""
    assert route_status.property("visible") is False
    assert system_status.property("text").count("目前收音：") == 1


def test_settings_save_stays_enabled_for_skipped_microphone_without_devices(
    qtbot,
    tmp_path,
):
    application = QApplication.instance()
    assert application is not None
    controller = FakeController(str(tmp_path))
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
    )
    window = _create_main_window(
        controller,
        application,
        window_settings=QSettings(
            str(tmp_path / "skipped-window.ini"),
            QSettings.Format.IniFormat,
        ),
        microphone_device_provider=lambda: [],
    )
    window.show()
    qtbot.waitUntil(window.isVisible)
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    save_button = window.findChild(QObject, "settingsSaveButton")
    qtbot.waitUntil(lambda: bool(save_button.property("enabled")))

    assert view_model.selectedMicrophoneKey == ""
    assert save_button.property("enabled") is True
    view_model._poll_timer.stop()
    view_model._allow_close = True
    window.close()
    window.deleteLater()


def test_dbfs_mapping_and_audio_freshness_are_bounded():
    assert _level_from_dbfs(None) == 0.0
    assert _level_from_dbfs(float("nan")) == 0.0
    assert _level_from_dbfs(-90.0) == 0.0
    assert 0.0 < _level_from_dbfs(-30.0) < 1.0
    assert _level_from_dbfs(4.0) == 1.0

    now = datetime(2026, 7, 12, 4, 0, 0, tzinfo=UTC)
    assert _audio_age_seconds(now - timedelta(milliseconds=200), now=now) == pytest.approx(
        0.2
    )
    assert _audio_age_seconds("invalid", now=now) == float("inf")


def test_application_uses_product_name_and_brand_icon(qapp):
    _configure_application(qapp)

    assert qapp.applicationName() == "聲跡日記"
    assert not qapp.windowIcon().isNull()
    assert qapp.font().family() == SYSTEM_UI_FONT_FAMILY


def test_rounded_window_region_clips_all_four_corners_symmetrically():
    width, height = COMPACT_WIDTH, COMPACT_HEIGHT
    region = _rounded_window_region(width, height, 14)

    assert region.boundingRect() == QRect(0, 0, width, height)
    assert region.contains(QPoint(width // 2, height // 2))
    assert region.contains(QPoint(width // 2, 0))
    assert region.contains(QPoint(0, height // 2))
    for corner in (
        QPoint(0, 0),
        QPoint(width - 1, 0),
        QPoint(0, height - 1),
        QPoint(width - 1, height - 1),
    ):
        assert not region.contains(corner)


def test_rounded_window_corners_remove_integer_mask_when_dwm_is_available(monkeypatch):
    from auto_speech_journal import ui

    class FakeWindow:
        def __init__(self) -> None:
            self.masks = []

        def setMask(self, mask) -> None:
            self.masks.append(mask)

        def width(self) -> int:
            return COMPACT_WIDTH

        def height(self) -> int:
            return COMPACT_HEIGHT

    window = FakeWindow()
    monkeypatch.setattr(ui, "_set_dwm_rounded_corners", lambda _window: True)

    backend = _apply_rounded_window_corners(window, 14)

    assert backend == "dwm"
    assert len(window.masks) == 1
    assert window.masks[0].isEmpty()


def test_rounded_window_corners_use_symmetric_mask_when_dwm_is_unavailable(monkeypatch):
    from auto_speech_journal import ui

    class FakeWindow:
        def __init__(self) -> None:
            self.masks = []

        def setMask(self, mask) -> None:
            self.masks.append(mask)

        def width(self) -> int:
            return COMPACT_WIDTH

        def height(self) -> int:
            return COMPACT_HEIGHT

    window = FakeWindow()
    monkeypatch.setattr(ui, "_set_dwm_rounded_corners", lambda _window: False)

    backend = _apply_rounded_window_corners(window, 14)

    assert backend == "mask"
    assert len(window.masks) == 2
    assert window.masks[0].isEmpty()
    assert not window.masks[1].contains(QPoint(0, 0))
    assert not window.masks[1].contains(QPoint(COMPACT_WIDTH - 1, COMPACT_HEIGHT - 1))


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (ControllerSnapshot(state=WorkerState.ERROR), "error"),
        (ControllerSnapshot(severity=Severity.ERROR), "error"),
        (ControllerSnapshot(state=WorkerState.DEGRADED), "degraded"),
        (ControllerSnapshot(paused=True), "paused"),
        (ControllerSnapshot(state=WorkerState.STARTING), "starting"),
        (ControllerSnapshot(state=WorkerState.STOPPED), "stopped"),
        (ControllerSnapshot(state=WorkerState.RECORDING, speech_active=True), "capturing"),
        (ControllerSnapshot(state=WorkerState.RECORDING, backlog=1), "finalizing"),
        (ControllerSnapshot(state=WorkerState.RECORDING), "listening"),
    ],
)
def test_scene_priority_maps_runtime_snapshot_to_offline_asset(snapshot, expected):
    from auto_speech_journal.ui_models import JournalViewModel

    assert JournalViewModel._desired_scene(snapshot) == expected


def test_status_uses_text_without_status_icons_or_transient_messages(journal_window, qtbot):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    controller.snapshot = replace(
        controller.snapshot,
        state=WorkerState.ERROR,
        severity=Severity.ERROR,
        message="麥克風連線失敗",
    )
    view_model._scene_changed_at -= 3.0
    view_model.refresh()
    qtbot.wait(20)

    assert view_model.stateText == "錄音需要處理"
    assert window.findChild(QObject, "compactStatusMark") is None
    assert window.findChild(QObject, "compactStateText") is None
    assert window.findChild(QObject, "compactStatusMessage") is None

    view_model.toggleExpanded()
    qtbot.wait(20)
    assert window.findChild(QObject, "workspaceStatusMark") is None
    assert window.findChild(QObject, "workspaceStatusMessage") is None
    assert window.findChild(QObject, "workspaceStateText").property("text") == (
        "錄音需要處理"
    )


def test_scene_change_respects_two_second_hold(journal_window):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    assert view_model.sceneKey == "listening"

    controller.snapshot = replace(controller.snapshot, speech_active=True)
    view_model.refresh()
    assert view_model.sceneKey == "listening"

    view_model._scene_changed_at -= 2.1
    view_model.refresh()
    assert view_model.sceneKey == "capturing"


def test_midnight_refresh_rolls_date_timeline_and_month_scene(journal_window):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    now = [datetime(2026, 7, 31, 23, 59, 59, tzinfo=ZoneInfo("Asia/Taipei"))]
    view_model._clock = lambda: now[0]
    view_model.refresh(force_timeline=True)

    assert view_model.dayKey == "2026-07-31"
    assert view_model.monthLabel == "7 月聲景"
    assert view_model.sceneSource.toLocalFile().endswith("07-listening-compact.webp")

    now[0] = datetime(2026, 8, 1, 0, 0, 1, tzinfo=ZoneInfo("Asia/Taipei"))
    view_model.refresh()

    assert view_model.dayKey == "2026-08-01"
    assert view_model.monthLabel == "8 月聲景"
    assert view_model.sceneSource.toLocalFile().endswith("08-listening-compact.webp")


def test_qml_window_switches_between_compact_and_centered_workspace(journal_window, qtbot):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    window.setPosition(QPoint(120, 130))
    qtbot.wait(20)

    assert (window.width(), window.height()) == (COMPACT_WIDTH, COMPACT_HEIGHT)
    assert window.flags() & Qt.WindowType.FramelessWindowHint
    assert window.flags() & Qt.WindowType.WindowStaysOnTopHint
    assert view_model._poll_timer.interval() == POLL_INTERVAL_MS
    assert window.findChild(QObject, "compactContent").property("visible") is True
    assert window.findChild(QObject, "compactScene").property("cropMode") is True
    compact_mask = window.mask()
    assert window._rounded_corner_backend in {"dwm", "mask"}
    if window._rounded_corner_backend == "dwm":
        assert compact_mask.isEmpty()
    else:
        assert compact_mask.boundingRect() == QRect(0, 0, COMPACT_WIDTH, COMPACT_HEIGHT)
        assert not compact_mask.contains(QPoint(0, 0))
        assert not compact_mask.contains(QPoint(COMPACT_WIDTH - 1, COMPACT_HEIGHT - 1))

    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    qtbot.wait(40)

    assert (window.width(), window.height()) == (
        view_model.expandedWidth,
        view_model.expandedHeight,
    )
    assert window.width() >= 960
    assert window.height() >= 680
    assert not window.flags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.findChild(QObject, "expandedContent").property("visible") is True
    assert window.findChild(QObject, "leftResizeHandle").property("visible") is True
    assert window.minimumWidth() == 960
    assert window.minimumHeight() == 680
    assert window.maximumWidth() >= window.minimumWidth()
    assert window.maximumHeight() >= window.minimumHeight()
    assert window.findChild(QObject, "timelineList") is not None
    assert window.findChild(QObject, "workspaceScene").property("cropMode") is True
    assert window.findChild(QObject, "settingsButton") is not None
    assert window.findChild(QObject, "hoursButton") is not None
    expanded_mask = window.mask()
    if window._rounded_corner_backend == "dwm":
        assert expanded_mask.isEmpty()
    else:
        assert expanded_mask.boundingRect() == QRect(
            0, 0, window.width(), window.height()
        )
        assert not expanded_mask.contains(QPoint(0, 0))
        assert not expanded_mask.contains(QPoint(window.width() - 1, window.height() - 1))

    view_model.collapseToCompact()
    qtbot.wait(40)
    assert (window.width(), window.height()) == (COMPACT_WIDTH, COMPACT_HEIGHT)
    assert window.position() == QPoint(120, 130)
    assert window.flags() & Qt.WindowType.WindowStaysOnTopHint
    if window._rounded_corner_backend == "dwm":
        assert window.mask().isEmpty()
    else:
        assert window.mask().boundingRect() == QRect(0, 0, COMPACT_WIDTH, COMPACT_HEIGHT)


def test_redesigned_workspace_uses_full_bleed_scene_and_single_river(
    journal_window, qtbot
):
    window, _, _ = journal_window
    compact_content = window.findChild(QObject, "compactContent")
    compact_scene = window.findChild(QObject, "compactScene")
    compact_info = window.findChild(QObject, "compactInfo")
    compact_action_row = window.findChild(QObject, "compactActionRow")
    compact_backlog = window.findChild(QObject, "compactBacklogText")
    compact_partial = window.findChild(QObject, "compactPartialText")
    title_bar = window.findChild(QObject, "titleBar")
    brand_icon = window.findChild(QObject, "brandIcon")
    close_button = window.findChild(QObject, "closeButton")
    qtbot.wait(20)

    assert float(compact_scene.property("x")) == pytest.approx(0.0, abs=0.5)
    assert float(title_bar.property("height")) == pytest.approx(38.0, abs=0.5)
    assert title_bar.property("color").alpha() == 0
    assert float(compact_scene.property("y")) == pytest.approx(
        -float(title_bar.property("height")), abs=0.5
    )
    assert float(compact_scene.property("width")) == pytest.approx(242.0, abs=0.5)
    assert float(compact_scene.property("height")) == pytest.approx(
        float(compact_content.property("height")) + float(title_bar.property("height")),
        abs=0.5,
    )
    assert brand_icon.property("source").toLocalFile().endswith("journal-ink-icon.png")
    assert window.findChild(QObject, "minimizeButton") is None
    assert close_button.property("text") == "×"
    assert float(compact_info.property("x")) == pytest.approx(146.0, abs=0.5)
    assert float(compact_info.property("width")) == pytest.approx(286.0, abs=0.5)
    assert window.findChild(QObject, "compactStatusMark") is None
    assert window.findChild(QObject, "compactStateText") is None
    assert window.findChild(QObject, "compactStatusMessage") is None
    assert float(compact_backlog.property("x")) + float(
        compact_backlog.property("width")
    ) == pytest.approx(float(compact_info.property("width")), abs=0.5)
    assert float(compact_partial.property("y")) >= (
        float(compact_backlog.property("y"))
        + float(compact_backlog.property("height"))
        + 5.5
    )
    compact_action_parent = compact_action_row.parent()
    bottom_gap = float(compact_action_parent.property("height")) - (
        float(compact_action_row.property("y"))
        + float(compact_action_row.property("height"))
    )
    assert bottom_gap == pytest.approx(9.0, abs=0.5)
    assert float(compact_action_row.property("spacing")) == pytest.approx(8.0)

    window._journal_view_model.toggleExpanded()
    qtbot.wait(40)

    paper_spread = window.findChild(QObject, "paperSpread")
    paper_spine = window.findChild(QObject, "paperSpine")
    live_trace = window.findChild(QObject, "workspaceLiveTrace")
    primary_actions = window.findChild(QObject, "primaryActionRow")
    secondary_actions = window.findChild(QObject, "secondaryActionRow")
    expanded_content = window.findChild(QObject, "expandedContent")
    workspace_scene = window.findChild(QObject, "workspaceScene")
    live_bar = window.findChild(QObject, "todayLiveBar")
    workspace_status = window.findChild(QObject, "workspaceStatusRow")
    workspace_state = window.findChild(QObject, "workspaceStateText")
    workspace_backlog = window.findChild(QObject, "workspaceBacklogText")

    assert paper_spread is not None
    assert paper_spread.inherits("QQuickRectangle")
    assert _rectangle_border_width(paper_spread) == pytest.approx(0.0)
    assert paper_spine is None
    assert float(title_bar.property("height")) == pytest.approx(50.0, abs=0.5)
    assert float(expanded_content.property("y")) == pytest.approx(0.0, abs=0.5)
    assert float(paper_spread.property("y")) == pytest.approx(0.0, abs=0.5)
    assert float(workspace_scene.property("height")) == pytest.approx(
        float(paper_spread.property("height")), abs=0.5
    )
    assert close_button.property("text") == "×"

    assert live_trace is not None
    assert live_trace.inherits("QQuickItem")
    assert not live_trace.inherits("QQuickRectangle")
    assert window.findChild(QObject, "workspaceStatusMark") is None
    assert window.findChild(QObject, "workspaceStatusMessage") is None
    assert workspace_state.parent() == workspace_status
    assert workspace_backlog.parent() == workspace_status
    assert float(workspace_status.property("x")) > float(live_bar.property("width")) / 2
    assert float(workspace_status.property("x")) + float(
        workspace_status.property("width")
    ) == pytest.approx(float(live_bar.property("width")) - 22, abs=0.5)
    assert primary_actions is not None
    assert secondary_actions is not None
    assert float(workspace_status.property("y")) >= (
        float(primary_actions.property("y"))
        + float(primary_actions.property("height"))
    )

    expanded_pause = window.findChild(QObject, "expandedPauseButton")
    assert expanded_pause.parent() == primary_actions
    assert window.findChild(QObject, "folderButton") is None
    assert float(expanded_pause.property("width")) == pytest.approx(
        float(primary_actions.property("width")), abs=0.5
    )

    for object_name in ("settingsButton", "systemButton", "hoursButton"):
        button = window.findChild(QObject, object_name)
        assert button.parent() == secondary_actions
        assert button.property("flat") is True

    timeline = window.findChild(QObject, "timelineList")
    timeline_title = window.findChild(QObject, "timelineTitle")
    assert timeline.property("idleSegmentColor").alpha() == 0
    assert float(timeline.property("segmentBorderWidth")) == pytest.approx(0.0)
    assert float(timeline.property("spacing")) == pytest.approx(6.0)
    assert window.findChild(QObject, "timelineSubtitle") is None
    assert float(timeline.property("y")) == pytest.approx(
        float(timeline_title.property("y"))
        + float(timeline_title.property("height"))
        + 10,
        abs=0.5,
    )
    assert window.property("uiFontFamily") == QApplication.instance().font().family()
    assert window.findChild(QObject, "compactWaveform") is None
    assert window.findChild(QObject, "workspaceWaveform") is None


def test_internal_typography_is_enlarged_without_crowding_fixed_layouts(
    journal_window, qtbot
):
    window, _, _ = journal_window

    assert _pixel_size(window.findChild(QObject, "compactBacklogText")) == 15
    assert _pixel_size(window.findChild(QObject, "compactPartialText")) == 17
    assert _pixel_size(window.findChild(QObject, "pauseButton")) == 18
    assert _pixel_size(window.findChild(QObject, "expandButton")) == 18
    assert _pixel_size(window.findChild(QObject, "closeButton")) == 16
    assert window.findChild(QObject, "compactStatusRow") is None
    assert float(window.findChild(QObject, "compactPartialText").property("height")) >= 70
    assert float(window.findChild(QObject, "compactActionRow").property("height")) == 32

    window._journal_view_model.toggleExpanded()
    qtbot.wait(40)
    qtbot.waitUntil(lambda: bool(_visual_items(window, "timelineRow")))

    expected_sizes = {
        "workspaceDate": 27,
        "workspaceStateText": 20,
        "workspaceBacklogText": 17,
        "workspaceLiveLabel": 15,
        "workspacePartialText": 19,
        "timelineTitle": 32,
        "sheetTitle": 32,
        "previewSpin": 18,
    }
    for object_name, pixel_size in expected_sizes.items():
        assert _pixel_size(window.findChild(QObject, object_name)) == pixel_size

    delegate_sizes = {
        "timelineHourLabel": 24,
        "timelineTimeLabel": 15,
        "timelineStatusLabel": 14,
        "timelineSegmentText": 19,
    }
    for object_name, pixel_size in delegate_sizes.items():
        assert _pixel_size(_visual_items(window, object_name)[0]) == pixel_size

    segment_surfaces = _visual_items(window, "timelineSegmentSurface")
    assert segment_surfaces
    assert all(float(surface.property("height")) >= 88 for surface in segment_surfaces)

    window._journal_view_model.beginEdit("morning-1")
    qtbot.wait(40)
    segment_surfaces = _visual_items(window, "timelineSegmentSurface")
    assert any(float(surface.property("height")) >= 194 for surface in segment_surfaces)


def test_correction_button_uses_a_stable_reserved_slot(journal_window, qtbot):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    view_model._poll_timer.stop()
    view_model.toggleExpanded()
    qtbot.waitUntil(lambda: bool(_visual_items(window, "timelineCorrectionSlot")))
    qtbot.wait(100)

    slot = _visual_items(window, "timelineCorrectionSlot")[0]
    header = slot.parent()
    button = next(
        child
        for child in slot.childItems()
        if child.objectName() == "timelineCorrectionButton"
    )
    time_label = next(
        child
        for child in header.childItems()
        if child.objectName() == "timelineTimeLabel"
    )
    status_label = next(
        child
        for child in header.childItems()
        if child.objectName() == "timelineStatusLabel"
    )

    button.setProperty("visible", False)
    qtbot.wait(20)
    before = (
        float(time_label.property("x")),
        float(time_label.property("width")),
        float(status_label.property("x")),
        float(status_label.property("width")),
        float(header.property("width")),
    )
    assert float(slot.property("width")) == pytest.approx(66.0, abs=0.5)

    button.setProperty("visible", True)
    qtbot.wait(20)
    after = (
        float(time_label.property("x")),
        float(time_label.property("width")),
        float(status_label.property("x")),
        float(status_label.property("width")),
        float(header.property("width")),
    )

    assert after == pytest.approx(before, abs=0.5)


def test_brand_icon_and_title_stay_vertically_centered_at_all_font_sizes(
    journal_window, qtbot
):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    brand_icon = window.findChild(QObject, "brandIcon")
    brand_title = window.findChild(QObject, "brandTitle")

    def assert_centered() -> None:
        icon_center = float(brand_icon.property("y")) + float(
            brand_icon.property("height")
        ) / 2
        title_center = float(brand_title.property("y")) + float(
            brand_title.property("height")
        ) / 2
        assert icon_center == pytest.approx(title_center, abs=0.5)

    assert_centered()
    view_model.toggleExpanded()
    qtbot.wait(20)
    assert_centered()
    view_model.collapseToCompact()
    qtbot.wait(20)

    assert view_model.applyAppearance(view_model.uiFontFamily, view_model.maxUiFontSize)
    qtbot.wait(20)
    assert_centered()
    view_model.toggleExpanded()
    qtbot.wait(20)
    assert_centered()


def test_live_bar_collapses_when_only_waiting_placeholder_remains(journal_window, qtbot):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    qtbot.wait(40)
    live_bar = window.findChild(QObject, "todayLiveBar")
    expanded_height = float(live_bar.property("implicitHeight"))
    assert live_bar.property("hasPartial") is True

    controller.snapshot = replace(controller.snapshot, partial_text="")
    view_model.refresh()
    qtbot.wait(300)

    assert live_bar.property("hasPartial") is False
    assert float(live_bar.property("implicitHeight")) < expanded_height
    assert window.findChild(QObject, "workspaceLiveLabel").property("text") == "即時預覽"


def test_pigment_absorption_uses_blooms_instead_of_wave_animation() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "auto_speech_journal"
        / "qml"
        / "PigmentAbsorption.qml"
    ).read_text(encoding="utf-8")

    assert "Math.sin" not in source
    assert "NumberAnimation on phase" not in source
    assert "loops: Animation.Infinite" not in source


def test_scene_art_has_no_pointer_driven_parallax() -> None:
    qml_dir = (
        Path(__file__).resolve().parents[1] / "src" / "auto_speech_journal" / "qml"
    )
    scene_source = (qml_dir / "SceneArt.qml").read_text(encoding="utf-8")
    caller_source = "\n".join(
        (qml_dir / filename).read_text(encoding="utf-8")
        for filename in ("JournalWindow.qml", "TodayWorkspace.qml")
    )

    assert "HoverHandler" not in scene_source
    assert "parallaxX" not in scene_source
    assert "parallaxY" not in scene_source
    assert "maximumParallax" not in scene_source
    assert "maximumParallax" not in caller_source


def test_timeline_model_groups_hours_and_preserves_active_draft(journal_window):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    model = view_model.timelineModel
    assert model.rowCount() == 2
    assert model.data(model.index(0), _role(model, "hourLabel")) == "09:00"
    assert model.data(model.index(0), _role(model, "isHourStart")) is True
    assert model.data(model.index(1), _role(model, "isHourStart")) is False

    view_model.beginEdit("morning-1")
    view_model.updateDraft("morning-1", "尚未儲存的草稿")
    controller.segments.append(
        TimelineSegmentView(
            segment_id="morning-3",
            time_label="[09:25:00]",
            text="新片段",
            status_label="已定稿",
            editable=True,
            hour_key="2026-07-12_09",
            state=SegmentState.FINAL_READY,
        )
    )
    controller.snapshot = replace(
        controller.snapshot,
        timeline_revision=controller.snapshot.timeline_revision + 1,
    )
    view_model.refresh(force_timeline=True)

    first = model.index(0)
    assert model.rowCount() == 3
    assert model.data(first, _role(model, "editing")) is True
    assert model.data(first, _role(model, "draftText")) == "尚未儲存的草稿"

    view_model.saveEdit("morning-1")
    assert ("correct", "morning-1", "尚未儲存的草稿") in controller.calls
    assert model.data(model.index(0), _role(model, "segmentText")) == "尚未儲存的草稿"
    assert model.data(model.index(0), _role(model, "statusLabel")) == "已修正"


def test_edit_validation_error_remains_visible_inside_editor(journal_window, qtbot):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    model = view_model.timelineModel
    view_model.toggleExpanded()
    qtbot.waitUntil(lambda: bool(_visual_items(window, "timelineRow")))

    view_model.beginEdit("morning-1")
    view_model.updateDraft("morning-1", "   ")
    view_model.saveEdit("morning-1")
    qtbot.wait(20)

    first = model.index(0)
    assert model.data(first, _role(model, "editing")) is True
    assert model.data(first, _role(model, "lastError")) == "修正文字不可為空"
    errors = _visual_items(window, "timelineEditErrorText")
    assert any(item.property("visible") and item.property("text") for item in errors)


def test_timeline_refresh_preserves_scroll_position_while_editing(
    journal_window, qtbot
):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    controller.segments = [
        TimelineSegmentView(
            segment_id=f"segment-{index:02d}",
            time_label=f"[09:{index:02d}:00]",
            text=f"第 {index + 1} 段聲跡",
            status_label="已定稿",
            editable=True,
            hour_key="2026-07-12_09",
            state=SegmentState.FINAL_READY,
        )
        for index in range(40)
    ]
    controller.snapshot = replace(
        controller.snapshot,
        timeline_revision=controller.snapshot.timeline_revision + 1,
    )
    view_model.toggleExpanded()
    view_model.refresh(force_timeline=True)
    timeline = window.findChild(QObject, "timelineList")
    assert timeline is not None
    qtbot.waitUntil(lambda: timeline.property("contentHeight") > timeline.property("height"))
    qtbot.wait(250)

    bottom = timeline.property("contentHeight") - timeline.property("height")
    timeline.setProperty("contentY", bottom)
    view_model.beginEdit("segment-00")
    qtbot.wait(20)
    saved_y = float(timeline.property("contentY"))

    controller.segments.append(
        TimelineSegmentView(
            segment_id="segment-40",
            time_label="[09:40:00]",
            text="新增的聲跡",
            status_label="已定稿",
            editable=True,
            hour_key="2026-07-12_09",
            state=SegmentState.FINAL_READY,
        )
    )
    controller.snapshot = replace(
        controller.snapshot,
        timeline_revision=controller.snapshot.timeline_revision + 1,
    )
    view_model.refresh(force_timeline=True)
    qtbot.wait(250)

    assert timeline.property("contentY") == pytest.approx(saved_y, abs=1.0)
    assert window.findChild(QObject, "newSegmentsPill").property("visible") is True


def test_large_timeline_keeps_delegate_count_virtualized(journal_window, qtbot):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    controller.segments = [
        TimelineSegmentView(
            segment_id=f"bulk-{index:04d}",
            time_label=f"[{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}]",
            text=f"大量時間軸測試片段 {index}",
            status_label="已定稿",
            editable=True,
            hour_key="2026-07-12_09",
            state=SegmentState.FINAL_READY,
        )
        for index in range(2_000)
    ]
    controller.snapshot = replace(
        controller.snapshot,
        timeline_revision=controller.snapshot.timeline_revision + 1,
    )
    view_model.toggleExpanded()
    view_model.refresh(force_timeline=True)
    qtbot.waitUntil(lambda: view_model.timelineModel.rowCount() == 2_000)
    qtbot.wait(120)

    instantiated = window.findChildren(QObject, "timelineRow")
    assert len(instantiated) < 100


def test_custom_side_sheets_and_controller_actions_are_available(journal_window):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()

    window.setProperty("activeSheet", "settings")
    assert window.findChild(QObject, "settingsSheet").property("visible") is True
    assert window.findChild(QObject, "utilityDrawerTabs") is not None
    assert window.findChild(QObject, "settingsDrawerTab") is not None
    assert window.findChild(QObject, "systemDrawerTab") is not None
    assert window.findChild(QObject, "vocabularyDrawerTab") is not None
    assert window.findChild(QObject, "hoursDrawerTab") is not None
    window.setProperty("activeSheet", "system")
    assert window.findChild(QObject, "systemSheet").property("visible") is True
    window.setProperty("activeSheet", "hours")
    assert window.findChild(QObject, "hoursSheet").property("visible") is True

    view_model.togglePause()
    view_model.openRecordsFolder()
    view_model.deleteHour("2026-07-12_09")
    assert "toggle_pause" in controller.calls
    assert "open_folder" in controller.calls
    assert ("delete", "2026-07-12_09") in controller.calls


def test_vocabulary_sheet_renders_and_dispatches_management_actions(
    journal_window, qtbot
):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    controller.vocabulary_counts = {"奇怪詞": 4, "保留詞": 2}
    view_model.refreshVocabulary()
    view_model.toggleExpanded()
    qtbot.wait(40)

    _click_quick_item(window, window.findChild(QObject, "vocabularyButton"))
    qtbot.waitUntil(lambda: window.property("activeSheet") == "vocabulary")
    qtbot.waitUntil(lambda: len(_visual_items(window, "vocabularyEntry")) == 2)

    _click_quick_item(window, window.findChild(QObject, "settingsDrawerTab"))
    qtbot.waitUntil(lambda: window.property("activeSheet") == "settings")
    _click_quick_item(window, window.findChild(QObject, "vocabularyDrawerTab"))
    qtbot.waitUntil(lambda: window.property("activeSheet") == "vocabulary")
    qtbot.waitUntil(lambda: len(_visual_items(window, "vocabularyEntry")) == 2)

    assert window.findChild(QObject, "vocabularyDrawerTab") is not None
    assert window.findChild(QObject, "vocabularySheet").property("visible") is True
    assert window.findChild(QObject, "vocabularyList").property("visible") is True
    assert window.findChild(QObject, "vocabularyEmpty").property("visible") is False
    assert sorted(item.property("text") for item in _visual_items(window, "vocabularyTerm")) == [
        "保留詞",
        "奇怪詞",
    ]
    assert sorted(item.property("text") for item in _visual_items(window, "vocabularyCount")) == [
        "2 次",
        "4 次",
    ]

    strange_entry = next(
        entry
        for entry in _visual_items(window, "vocabularyEntry")
        if entry.findChild(QObject, "vocabularyTerm").property("text") == "奇怪詞"
    )
    _click_quick_item(
        window,
        strange_entry.findChild(QObject, "deleteVocabularyTermButton"),
    )
    qtbot.waitUntil(lambda: ("delete_vocabulary", "奇怪詞") in controller.calls)
    qtbot.waitUntil(lambda: view_model.vocabularyEntries == [{"term": "保留詞", "count": 2}])

    learning_switch = window.findChild(QObject, "vocabularyLearningSwitch")
    assert learning_switch.property("checked") is True
    _click_quick_item(window, learning_switch)
    qtbot.waitUntil(lambda: ("vocabulary_learning", False) in controller.calls)
    assert view_model.vocabularyLearningEnabled is False

    controller.vocabulary_learning_error = OSError("設定檔已鎖定")
    _click_quick_item(window, learning_switch)
    qtbot.waitUntil(lambda: ("vocabulary_learning", True) in controller.calls)
    qtbot.waitUntil(lambda: learning_switch.property("checked") is False)
    assert controller.config.vocabulary_learning_enabled is False

    _click_quick_item(window, window.findChild(QObject, "clearVocabularyButton"))
    qtbot.waitUntil(lambda: window.property("confirmationKind") == "clearVocabulary")
    assert "既有的使用者修正仍會保留" in window.property("confirmationMessage")
    confirmation_actions = window.findChild(QObject, "confirmationActions")
    clear_action = next(
        item
        for item in confirmation_actions.childItems()
        if item.property("text") == "清空全部"
    )
    _click_quick_item(window, clear_action)
    qtbot.waitUntil(lambda: "clear_vocabulary" in controller.calls)

    assert window.property("confirmationKind") == ""
    assert view_model.vocabularyEntries == []
    assert controller.vocabulary_counts == {}


def test_settings_can_switch_local_font_and_size_without_restarting(
    journal_window, qtbot
):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")

    font_picker = window.findChild(QObject, "fontPicker")
    size_spin = window.findChild(QObject, "fontSizeSpin")
    settings_sheet = window.findChild(QObject, "settingsSheet")
    system_font_family = window.property("systemFontFamily")
    assert font_picker is not None
    assert size_spin is not None
    assert settings_sheet is not None
    assert int(size_spin.property("from")) == 14
    assert int(size_spin.property("to")) == 26
    assert settings_sheet.property("clip") is True

    family = view_model.availableFontFamilies[0]
    assert view_model.applyAppearance(family, 22) is True
    qtbot.waitUntil(lambda: _pixel_size(window.findChild(QObject, "timelineTitle")) == 39)

    assert view_model.uiFontFamily == family
    assert view_model.uiFontSize == 22
    assert controller.config.ui_font_family == family
    assert controller.config.ui_font_size == 22
    assert window.property("uiFontFamily") == family
    assert window.property("systemFontFamily") == system_font_family
    assert window.findChild(QObject, "workspaceStateText").property("font").family() == (
        system_font_family
    )
    assert _visual_items(window, "timelineSegmentText")[0].property(
        "font"
    ).family() == family


def test_settings_sheet_renders_recent_history_and_opens_full_log(journal_window, qtbot):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    view_model._settings_history_entries = [
        {
            "timestamp": "2026/07/12 22:30:00",
            "summary": "2026/07/12 22:30:00 · 已變更介面字級",
            "details": "介面字級：17 px → 18 px",
            "changedFields": ["ui_font_size"],
        }
    ]
    view_model.settingsHistoryChanged.emit()
    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    qtbot.waitUntil(lambda: bool(_visual_items(window, "settingsHistoryEntry")))

    assert window.findChild(QObject, "settingsHistoryEmpty").property("visible") is False
    assert window.findChild(QObject, "openSettingsHistoryButton") is not None
    view_model.openSettingsHistoryFile()
    assert "open_settings_history" in controller.calls


def test_max_font_size_keeps_widest_local_font_inside_fixed_boundaries(
    journal_window, qtbot
):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    controller.snapshot = replace(controller.snapshot, paused=True)
    view_model.refresh()
    families = tuple(view_model.availableFontFamilies)
    assert families
    widest_family = "SentyWen" if "SentyWen" in families else families[-1]

    assert view_model.applyAppearance(widest_family, 26) is True
    window.setProperty("confirmationKind", "exit")
    window.setProperty(
        "confirmationMessage",
        "確定停止錄音、完成可及的轉錄並結束聲跡日記？",
    )
    qtbot.wait(10)
    _assert_confirmation_content_fits(window)
    _assert_guarded_button_content_fits(window)
    window.setProperty("confirmationKind", "")

    for family in families:
        assert view_model.applyAppearance(family, 26) is True
        qtbot.wait(5)
        _assert_button_content_fits(window.findChild(QObject, "pauseButton"))
        _assert_button_content_fits(window.findChild(QObject, "expandButton"))

    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    qtbot.wait(40)
    for family in families:
        assert view_model.applyAppearance(family, 26) is True
        qtbot.wait(5)
        for object_name in (
            "expandedPauseButton",
            "settingsButton",
            "systemButton",
            "vocabularyButton",
            "hoursButton",
            "newSegmentsPill",
        ):
            _assert_button_content_fits(window.findChild(QObject, object_name))
        _assert_guarded_button_content_fits(window)

        date_item = window.findChild(QObject, "workspaceDate")
        status_row = window.findChild(QObject, "workspaceStatusRow")
        assert float(date_item.property("contentWidth")) <= (
            float(date_item.property("width")) + 0.5
        )
        assert float(status_row.property("y")) >= (
            float(date_item.property("y"))
            + float(date_item.property("height"))
            + 9.5
        )
        live_label = window.findChild(QObject, "workspaceLiveLabel")
        partial_text = window.findChild(QObject, "workspacePartialText")
        assert float(partial_text.property("y")) >= (
            float(live_label.property("y")) + float(live_label.property("height"))
        )

    assert view_model.applyAppearance(widest_family, 26) is True
    window.setProperty("confirmationKind", "exit")
    qtbot.wait(10)
    _assert_confirmation_content_fits(window)
    _assert_guarded_button_content_fits(window)


def test_native_close_collapses_workspace_then_minimizes_compact(journal_window, qtbot):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    assert view_model.expanded is True

    window.close()
    qtbot.wait(40)
    assert view_model.expanded is False
    assert window.isVisible()

    window.close()
    qtbot.wait(40)
    assert window.visibility() == QWindow.Visibility.Minimized
    assert window.findChild(QObject, "compactScene").property("motionEnabled") is False


def test_one_hundred_expand_collapse_cycles_do_not_duplicate_ambient_layer(
    journal_window,
):
    window, _, _ = journal_window
    view_model = window._journal_view_model

    for _ in range(100):
        view_model.toggleExpanded()
        QApplication.processEvents()
        view_model.collapseToCompact()
        QApplication.processEvents()

    ambient_layers = window.findChildren(QObject, "ambientSoundRiver")
    fallback_motes = window.findChildren(QObject, "softwareRiverMote")
    assert view_model.expanded is False
    assert len(ambient_layers) == 1
    assert len(fallback_motes) <= int(ambient_layers[0].property("fallbackMoteCount"))
    assert ambient_layers[0].property("particleLayerLoaded") is False
    assert ambient_layers[0].property("fallbackAnimationRunning") is False


def test_compact_position_is_persisted_independently(journal_window):
    window, _, settings = journal_window
    view_model = window._journal_view_model
    window.setPosition(177, 188)
    view_model.rememberCompactPosition(177, 188)
    view_model.persistWindowState()

    assert int(settings.value("compactX")) == 177
    assert int(settings.value("compactY")) == 188


def test_confirmation_overlay_consumes_clicks_before_the_open_side_sheet(
    journal_window, qtbot
):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    window.setProperty("activeSheet", "hours")
    window.setProperty("confirmationKind", "delete")
    qtbot.wait(20)

    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(80, 80),
    )
    qtbot.wait(20)

    assert window.property("activeSheet") == "hours"
    assert window.property("confirmationKind") == "delete"


def test_settings_sheet_resets_unsaved_values_and_reports_save_failure(journal_window):
    window, controller, _ = journal_window
    view_model = window._journal_view_model
    view_model.toggleExpanded()
    window.setProperty("activeSheet", "settings")
    records_field = window.findChild(QObject, "recordsField")
    assert records_field is not None
    original = controller.config.records_root
    records_field.setProperty("text", "C:/not-saved")

    window.setProperty("activeSheet", "")
    window.setProperty("activeSheet", "settings")
    assert records_field.property("text") == original
    assert view_model.applySettings("", 350, 900, 28_000) is False
    assert controller.config.records_root == original


def test_delete_hour_returns_success_for_qml_list_refresh(journal_window):
    window, controller, _ = journal_window
    view_model = window._journal_view_model

    assert view_model.deleteHour("2026-07-12_09") is True
    assert ("delete", "2026-07-12_09") in controller.calls


def test_saved_compact_position_is_clamped_to_an_available_screen(journal_window):
    window, _, _ = journal_window
    view_model = window._journal_view_model
    clamped = view_model._clamp_compact_position(
        QPoint(-100_000, -100_000),
        QSize(COMPACT_WIDTH, COMPACT_HEIGHT),
    )
    available = QApplication.primaryScreen().availableGeometry()

    assert available.contains(clamped)
    assert available.contains(
        QPoint(clamped.x() + COMPACT_WIDTH - 1, clamped.y() + COMPACT_HEIGHT - 1)
    )
