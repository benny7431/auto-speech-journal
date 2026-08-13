from __future__ import annotations

import math
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QGuiApplication

from . import __version__
from .config import (
    DEFAULT_UI_FONT_SIZE,
    MAX_UI_FONT_SIZE,
    MIN_UI_FONT_SIZE,
    AppConfig,
    MicrophoneMode,
    MicrophoneSelection,
)
from .paths import AppPaths

COMPACT_WIDTH = 440
COMPACT_HEIGHT = 190
SETUP_WIDTH = 560
SETUP_HEIGHT = 480
EXPANDED_WIDTH = 1180
EXPANDED_HEIGHT = 820
EXPANDED_MIN_WIDTH = 960
EXPANDED_MIN_HEIGHT = 680
POLL_INTERVAL_MS = 100
MICROPHONE_TEST_TIMEOUT_MS = 3_000
SCENE_HOLD_SECONDS = 2.0
SPI_GETCLIENTAREAANIMATION = 0x1042
FONT_DIRECTORY_ENV = "AUTO_SPEECH_JOURNAL_FONT_DIR"
SUPPORTED_FONT_SUFFIXES = frozenset({".ttf", ".otf"})

TAIPEI = ZoneInfo("Asia/Taipei")
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
SETTINGS_FIELD_LABELS = {
    "records_root": "紀錄資料夾",
    "preview_interval_ms": "即時預覽間隔",
    "endpoint_silence_ms": "靜音收束時間",
    "max_segment_ms": "最長片段時間",
    "ui_font_family": "日記字體",
    "ui_font_size": "介面字級",
    "microphone.mode": "麥克風模式",
    "microphone.preferred_device.name": "偏好麥克風",
    "onboarding_completed": "首次設定",
    "startup_enabled": "開機自動啟動",
    "update_check_enabled": "版本更新提示",
    "vocabulary_learning_enabled": "校正字典自動學習",
}
FONT_FAMILY_DISPLAY_NAMES = {
    "HanyiSentyJournal": "漢儀新蒂手札體",
    "Hanyi Senty Diary": "漢儀新蒂日記體",
    "SentyWen": "漢儀新蒂文徵明體",
    "SentyFountainPen": "新蒂美工鋼筆",
    "新蒂美工鋼筆": "新蒂美工鋼筆",
    "SentyOrchid": "新蒂君子蘭",
    "新蒂君子蘭": "新蒂君子蘭",
    "SentyCreek": "新蒂山泉體",
    "新蒂山泉体": "新蒂山泉體",
    "HanyiSentyZhangjizhi": "漢儀新蒂張即之體",
    "漢儀新蒂張即之體": "漢儀新蒂張即之體",
    "Hanyi Senty Lingfei Scroll": "漢儀新蒂靈飛經體",
    "SentyEtherealWander": "新蒂逍遙遊",
    "新蒂逍遙遊": "新蒂逍遙遊",
    "LXGW WenKai TC": "霞鶩文楷 TC v1.522",
}


def _font_directories() -> tuple[Path, ...]:
    """Return external font folders without relying on wheel package data."""
    candidates: list[Path] = []
    override = os.environ.get(FONT_DIRECTORY_ENV, "")
    if override:
        candidates.extend(
            Path(value).expanduser().resolve(strict=False)
            for value in override.split(os.pathsep)
            if value.strip()
        )

    candidates.append(AppPaths.defaults().fonts_dir.resolve(strict=False))
    checkout_root = Path(__file__).resolve().parents[2]
    if (checkout_root / "pyproject.toml").is_file():
        candidates.append((checkout_root / "字體").resolve(strict=False))

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class _LoadedFont:
    signature: tuple[int, int]
    font_id: int
    families: tuple[str, ...]


class LocalFontCatalog:
    """Load local TTF/OTF files and support rescanning while the app is running."""

    def __init__(self, directories: Sequence[Path] | None = None) -> None:
        self.directories = tuple(directories or _font_directories())
        self._loaded: dict[Path, _LoadedFont] = {}

    @property
    def primary_directory(self) -> Path:
        return self.directories[0]

    def rescan(self) -> tuple[str, ...]:
        files = self._discover_files()
        current = set(files)
        for path in tuple(self._loaded):
            if path not in current:
                self._remove(path)

        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_mtime_ns, stat.st_size)
            loaded = self._loaded.get(path)
            if loaded is not None and loaded.signature == signature:
                continue
            if loaded is not None:
                self._remove(path)
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = (
                tuple(QFontDatabase.applicationFontFamilies(font_id))
                if font_id >= 0
                else ()
            )
            self._loaded[path] = _LoadedFont(signature, font_id, families)

        # Windows may expose localized aliases for the same TTF. Keep one
        # selectable family per file so the settings list mirrors the files
        # the user placed in the folder instead of showing duplicates.
        families = {
            loaded.families[0]
            for loaded in self._loaded.values()
            if loaded.families and loaded.families[0].strip()
        }
        return tuple(sorted(families, key=str.casefold))

    def canonical_family(self, family: str) -> str:
        """Resolve a localized Qt family alias to its file's primary family."""
        requested = family.strip()
        for loaded in self._loaded.values():
            if requested in loaded.families:
                return loaded.families[0]
        return requested

    def _discover_files(self) -> tuple[Path, ...]:
        files: set[Path] = set()
        for directory in self.directories:
            if not directory.is_dir():
                continue
            try:
                entries = directory.rglob("*")
                files.update(
                    path.resolve(strict=False)
                    for path in entries
                    if path.is_file() and path.suffix.casefold() in SUPPORTED_FONT_SUFFIXES
                )
            except OSError:
                continue
        return tuple(sorted(files, key=lambda path: str(path).casefold()))

    def _remove(self, path: Path) -> None:
        loaded = self._loaded.pop(path, None)
        if loaded is not None and loaded.font_id >= 0:
            QFontDatabase.removeApplicationFont(loaded.font_id)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value or ""))


def _safe_float(value: object, default: float = -120.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _model_setup_value(result: object, name: str, default: object) -> object:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _model_setup_count(result: object, name: str) -> int:
    try:
        return max(0, int(_model_setup_value(result, name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _normalized_model_setup_result(
    result: object,
    *,
    default_state: str,
) -> dict[str, object]:
    if isinstance(result, bool):
        ready = result
        return {
            "state": "ready" if ready else default_state,
            "ready": ready,
            "message": "語音模型已就緒" if ready else "語音模型尚未就緒",
            "completed": 0,
            "total": 0,
            "asset": "",
        }
    ready = bool(_model_setup_value(result, "ready", False))
    state = str(_model_setup_value(result, "state", default_state) or default_state)
    if ready:
        state = "ready"
    completed = _model_setup_count(result, "completed")
    total = _model_setup_count(result, "total")
    return {
        "state": state,
        "ready": ready,
        "message": str(_model_setup_value(result, "message", "") or ""),
        "completed": min(completed, total) if total > 0 else completed,
        "total": total,
        "asset": str(_model_setup_value(result, "asset", "") or ""),
    }


def _format_transfer_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.2f} GiB"


def _microphone_key_for_fingerprint(fingerprint: object | None) -> str:
    if fingerprint is None:
        return ""
    endpoint_id = str(getattr(fingerprint, "endpoint_id", "") or "").strip()
    if endpoint_id:
        return f"fixed:{endpoint_id}"
    name = str(getattr(fingerprint, "name", "") or "").strip().casefold()
    host_api = str(getattr(fingerprint, "host_api", "") or "").strip().casefold()
    return f"fixed:{host_api}:{name}" if name else ""


def _microphone_key_for_selection(selection: object | None) -> str:
    mode = _enum_value(getattr(selection, "mode", ""))
    if mode == MicrophoneMode.SYSTEM_DEFAULT.value:
        return MicrophoneMode.SYSTEM_DEFAULT.value
    if mode == MicrophoneMode.FIXED.value:
        return _microphone_key_for_fingerprint(
            getattr(selection, "preferred_device", None)
        )
    return ""


def _windows_reduced_motion(
    *,
    platform_name: str | None = None,
    system_parameters_info: Callable[..., object] | None = None,
) -> bool:
    """Return whether Windows disables animations inside application windows."""
    if (platform_name or sys.platform) != "win32":
        return False

    import ctypes

    try:
        query = system_parameters_info
        if query is None:
            query = ctypes.windll.user32.SystemParametersInfoW  # type: ignore[attr-defined]
        enabled = ctypes.c_int(1)
        succeeded = query(
            SPI_GETCLIENTAREAANIMATION,
            0,
            ctypes.byref(enabled),
            0,
        )
    except (AttributeError, OSError):
        return False
    return bool(succeeded) and not bool(enabled.value)


@dataclass(frozen=True, slots=True)
class _TimelineRow:
    segment_id: str
    hour_key: str
    hour_label: str
    is_hour_start: bool
    hour_segment_count: int
    time_label: str
    text: str
    status_label: str
    editable: bool
    state: str
    last_error: str


class TimelineListModel(QAbstractListModel):
    SegmentIdRole = Qt.ItemDataRole.UserRole + 1
    HourKeyRole = SegmentIdRole + 1
    HourLabelRole = HourKeyRole + 1
    IsHourStartRole = HourLabelRole + 1
    HourSegmentCountRole = IsHourStartRole + 1
    TimeLabelRole = HourSegmentCountRole + 1
    TextRole = TimeLabelRole + 1
    StatusLabelRole = TextRole + 1
    EditableRole = StatusLabelRole + 1
    StateRole = EditableRole + 1
    LastErrorRole = StateRole + 1
    EditingRole = LastErrorRole + 1
    DraftTextRole = EditingRole + 1

    _ROLE_NAMES = {
        SegmentIdRole: b"segmentId",
        HourKeyRole: b"hourKey",
        HourLabelRole: b"hourLabel",
        IsHourStartRole: b"isHourStart",
        HourSegmentCountRole: b"hourSegmentCount",
        TimeLabelRole: b"timeLabel",
        TextRole: b"segmentText",
        StatusLabelRole: b"statusLabel",
        EditableRole: b"editable",
        StateRole: b"segmentState",
        LastErrorRole: b"lastError",
        EditingRole: b"editing",
        DraftTextRole: b"draftText",
    }

    countChanged = Signal()
    editingChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_TimelineRow] = []
        self._editing_id = ""
        self._drafts: dict[str, str] = {}
        self._edit_errors: dict[str, str] = {}

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return self._ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        values = {
            self.SegmentIdRole: row.segment_id,
            self.HourKeyRole: row.hour_key,
            self.HourLabelRole: row.hour_label,
            self.IsHourStartRole: row.is_hour_start,
            self.HourSegmentCountRole: row.hour_segment_count,
            self.TimeLabelRole: row.time_label,
            self.TextRole: row.text,
            self.StatusLabelRole: row.status_label,
            self.EditableRole: row.editable,
            self.StateRole: row.state,
            self.LastErrorRole: (
                self._edit_errors.get(row.segment_id, row.last_error)
                if row.segment_id == self._editing_id
                else row.last_error
            ),
            self.EditingRole: row.segment_id == self._editing_id,
            self.DraftTextRole: self._drafts.get(row.segment_id, row.text),
            Qt.ItemDataRole.DisplayRole: row.text,
        }
        return values.get(role)

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._rows)

    @Property(bool, notify=editingChanged)
    def hasActiveEdit(self) -> bool:
        return bool(self._editing_id)

    @Property(str, notify=countChanged)
    def lastHourLabel(self) -> str:
        """The hour of the most recent row, so the view can draw the rest of the day."""

        return self._rows[-1].hour_label if self._rows else ""

    def replace_rows(self, rows: Sequence[_TimelineRow]) -> int:
        previous_ids = {row.segment_id for row in self._rows}
        valid_ids = {row.segment_id for row in rows}
        added = len(valid_ids - previous_ids)
        self.beginResetModel()
        self._rows = list(rows)
        self._drafts = {
            segment_id: text
            for segment_id, text in self._drafts.items()
            if segment_id in valid_ids
        }
        self._edit_errors = {
            segment_id: text
            for segment_id, text in self._edit_errors.items()
            if segment_id in valid_ids
        }
        if self._editing_id not in valid_ids:
            self._editing_id = ""
        self.endResetModel()
        self.countChanged.emit()
        self.editingChanged.emit()
        return added

    def row_for_id(self, segment_id: str) -> _TimelineRow | None:
        return next((row for row in self._rows if row.segment_id == segment_id), None)

    @Slot(str, result=int)
    def indexForSegmentId(self, segment_id: str) -> int:
        return next(
            (
                index
                for index, row in enumerate(self._rows)
                if row.segment_id == segment_id
            ),
            -1,
        )

    def begin_edit(self, segment_id: str) -> bool:
        row = self.row_for_id(segment_id)
        if row is None or not row.editable or not row.text.strip():
            return False
        previous = self._editing_id
        if previous and previous != segment_id:
            self._edit_errors.pop(previous, None)
        self._editing_id = segment_id
        self._drafts.setdefault(segment_id, row.text)
        self._edit_errors.pop(segment_id, None)
        self._emit_edit_roles(previous, segment_id)
        self.editingChanged.emit()
        return True

    def update_draft(self, segment_id: str, text: str) -> None:
        if segment_id != self._editing_id:
            return
        self._drafts[segment_id] = text
        self._edit_errors.pop(segment_id, None)
        self._emit_row(segment_id, (self.DraftTextRole, self.LastErrorRole))

    def draft_for(self, segment_id: str) -> str:
        row = self.row_for_id(segment_id)
        return self._drafts.get(segment_id, row.text if row else "")

    def set_edit_error(self, segment_id: str, message: str) -> None:
        if segment_id != self._editing_id:
            return
        self._edit_errors[segment_id] = message.strip() or "操作失敗"
        self._emit_row(segment_id, (self.LastErrorRole,))

    def cancel_edit(self, segment_id: str) -> None:
        if segment_id != self._editing_id:
            return
        self._drafts.pop(segment_id, None)
        self._edit_errors.pop(segment_id, None)
        self._editing_id = ""
        self._emit_row(
            segment_id,
            (self.EditingRole, self.DraftTextRole, self.LastErrorRole),
        )
        self.editingChanged.emit()

    def finish_edit(self, segment_id: str, text: str) -> None:
        for index, row in enumerate(self._rows):
            if row.segment_id != segment_id:
                continue
            self._rows[index] = replace(row, text=text, status_label="已修正", state="corrected")
            break
        self._drafts.pop(segment_id, None)
        self._edit_errors.pop(segment_id, None)
        self._editing_id = ""
        self._emit_row(
            segment_id,
            (
                self.TextRole,
                self.StatusLabelRole,
                self.StateRole,
                self.EditingRole,
                self.LastErrorRole,
            ),
        )
        self.editingChanged.emit()

    def _emit_edit_roles(self, *segment_ids: str) -> None:
        for segment_id in segment_ids:
            if segment_id:
                self._emit_row(
                    segment_id,
                    (self.EditingRole, self.DraftTextRole, self.LastErrorRole),
                )

    def _emit_row(self, segment_id: str, roles: Sequence[int]) -> None:
        for row_index, row in enumerate(self._rows):
            if row.segment_id == segment_id:
                index = self.index(row_index, 0)
                self.dataChanged.emit(index, index, list(roles))
                return


class JournalViewModel(QObject):
    snapshotChanged = Signal()
    expandedChanged = Signal()
    expandedSizeChanged = Signal()
    sceneChanged = Signal()
    dateChanged = Signal()
    settingsChanged = Signal()
    settingsHistoryChanged = Signal()
    vocabularyChanged = Signal()
    appearanceChanged = Signal()
    availableFontsChanged = Signal()
    microphoneDevicesChanged = Signal()
    microphoneSelectionChanged = Signal()
    microphoneSetupChanged = Signal()
    microphoneTestChanged = Signal()
    onboardingChanged = Signal()
    modelSetupChanged = Signal()
    updateCheckChanged = Signal()
    controllerStartChanged = Signal()
    _microphoneTestCompleted = Signal(str, bool, str, float, float)
    _updateCheckCompleted = Signal(int, object)
    _modelSetupProgressReceived = Signal(int, object)
    _modelSetupCompleted = Signal(int, object)
    timelineRevisionChanged = Signal()
    timelineUpdating = Signal()
    timelineUpdated = Signal(int)
    actionFailed = Signal(str)
    actionSucceeded = Signal(str)
    allowCloseChanged = Signal()

    def __init__(
        self,
        controller: Any,
        application: Any,
        *,
        settings: QSettings | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        font_directories: Sequence[Path] | None = None,
        microphone_device_provider: Callable[[], Sequence[Any]] | None = None,
        startup_setting_callback: Callable[[bool], Any] | None = None,
        update_check_callback: Callable[[bool, Callable[[Any], None]], Any] | None = None,
        model_status_callback: Callable[[], Any] | None = None,
        model_provision_callback: Callable[[Callable[[Any], None]], Any] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._application = application
        self._settings = settings or QSettings("AutoSpeechJournal", "Desktop")
        self._clock = clock or (lambda: datetime.now(TAIPEI))
        self._monotonic = monotonic
        self._microphone_device_provider = microphone_device_provider
        self._startup_setting_callback = startup_setting_callback
        self._update_check_callback = update_check_callback
        self._model_status_callback = model_status_callback
        self._model_provision_callback = model_provision_callback
        self._window: Any | None = None
        self._expanded = False
        self._allow_close = False
        self._stopping = False
        self._snapshot = getattr(controller, "snapshot", None)
        self._day = self._local_now().date()
        self._timeline_key: object = object()
        self._timeline_revision = -1
        self._reduced_motion = _windows_reduced_motion()
        self._scene_key = self._desired_scene(self._snapshot)
        self._scene_changed_at = self._monotonic()
        self._compact_position: QPoint | None = self._read_position()
        self._expanded_size = self._read_expanded_size()
        self._font_catalog = LocalFontCatalog(font_directories)
        self._available_font_families: tuple[str, ...] = ()
        self._fallback_ui_font_family = str(self._application.font().family())
        self._ui_font_family = self._fallback_ui_font_family
        self._ui_font_size = DEFAULT_UI_FONT_SIZE
        self._rescan_fonts()
        self._load_configured_appearance()
        self._settings_history_entries = self._read_settings_history()
        self._vocabulary_entries = self._read_vocabulary_entries()
        self._microphone_options: list[dict[str, Any]] = []
        self._microphone_fingerprints: dict[str, Any] = {}
        self._microphone_scan_error = ""
        self._microphone_has_selectable_route = False
        configured_microphone = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        self._selected_microphone_key = (
            ""
            if _enum_value(getattr(configured_microphone, "mode", ""))
            == MicrophoneMode.PENDING.value
            else _microphone_key_for_selection(configured_microphone)
        )
        self._microphone_test_state = "idle"
        self._microphone_test_message = ""
        self._microphone_test_level = 0.0
        self._microphone_test_request_id = ""
        config = getattr(self._controller, "config", None)
        self._onboarding_deferred = (
            not bool(getattr(config, "onboarding_completed", False))
            and _enum_value(getattr(getattr(config, "microphone", None), "mode", ""))
            == MicrophoneMode.SKIPPED.value
        )
        self._onboarding_step = 0
        self._onboarding_records_root = str(getattr(config, "records_root", "") or "")
        self._onboarding_records_tested = False
        self._onboarding_startup_enabled = False
        self._onboarding_update_check_enabled = False
        self._model_setup_generation = 0
        self._model_setup_running = False
        self._model_setup_operation = "check"
        self._model_setup_state = "ready" if model_status_callback is None else "checking"
        self._model_setup_message = (
            "語音模型已就緒"
            if model_status_callback is None
            else "正在確認本機語音模型…"
        )
        self._model_setup_completed = 0
        self._model_setup_total = 0
        self._model_setup_asset = ""
        self._update_available = False
        self._update_available_text = ""
        self._update_release_url = ""
        self._update_check_generation = 0
        self._update_check_requested = False
        self._consent_microphone_test_required = False
        self._controller_started = False
        self._microphoneTestCompleted.connect(self._finish_microphone_test)
        self._updateCheckCompleted.connect(self._finish_update_check)
        self._modelSetupProgressReceived.connect(self._finish_model_setup_progress)
        self._modelSetupCompleted.connect(self._finish_model_setup)
        self._timeline_model = TimelineListModel(self)
        self._poll_timer = QTimer(self)
        self._poll_timer.setObjectName("journalPollTimer")
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)

    def attach_window(self, window: Any) -> None:
        self._window = window
        self._apply_window_mode(initial=True)

    def activate(self) -> None:
        if self.onboardingPending:
            self.rescanMicrophones()
            self.checkOnboardingModels()
        self.refresh(force_timeline=True)
        self._poll_timer.start()
        config = getattr(self._controller, "config", None)
        if bool(getattr(config, "update_check_enabled", False)):
            self._trigger_update_check(True)

    @Property(QObject, constant=True)
    def timelineModel(self) -> QObject:
        return self._timeline_model

    @Property(bool, notify=expandedChanged)
    def expanded(self) -> bool:
        return self._expanded

    @Property(int, notify=expandedSizeChanged)
    def expandedWidth(self) -> int:
        return self._expanded_size.width()

    @Property(int, notify=expandedSizeChanged)
    def expandedHeight(self) -> int:
        return self._expanded_size.height()

    @Property(bool, notify=allowCloseChanged)
    def allowClose(self) -> bool:
        return self._allow_close

    @Property(bool, constant=True)
    def reducedMotion(self) -> bool:
        return self._reduced_motion

    @Property("QStringList", notify=availableFontsChanged)
    def availableFontFamilies(self) -> list[str]:
        return list(self._available_font_families)

    @Property(str, notify=appearanceChanged)
    def uiFontFamily(self) -> str:
        return self._ui_font_family

    @Property(int, notify=appearanceChanged)
    def uiFontSize(self) -> int:
        return self._ui_font_size

    @Property(str, constant=True)
    def systemFontFamily(self) -> str:
        return self._fallback_ui_font_family

    @Property(str, constant=True)
    def appVersion(self) -> str:
        return __version__

    @Property(float, notify=appearanceChanged)
    def uiFontScale(self) -> float:
        return self._ui_font_size / 16.0

    @Property(int, constant=True)
    def minUiFontSize(self) -> int:
        return MIN_UI_FONT_SIZE

    @Property(int, constant=True)
    def maxUiFontSize(self) -> int:
        return MAX_UI_FONT_SIZE

    @Property(str, constant=True)
    def fontFolder(self) -> str:
        return str(self._font_catalog.primary_directory)

    @Property(str, notify=snapshotChanged)
    def stateText(self) -> str:
        snapshot = self._snapshot
        state = _enum_value(getattr(snapshot, "state", "stopped"))
        if not self.recordingControlsEnabled and state not in {"degraded", "error"}:
            return "尚未開始錄音"
        if bool(getattr(snapshot, "paused", False)):
            return "錄音已暫停"
        return {
            "starting": "正在喚醒聲跡",
            "ready": "準備聆聽",
            "recording": "正在聆聽",
            "paused": "錄音已暫停",
            "degraded": "錄音持續中 · 需要留意",
            "error": "錄音需要處理",
            "stopped": "聲跡已收妥",
        }.get(state, "正在聆聽")

    @Property(str, notify=snapshotChanged)
    def statusMessage(self) -> str:
        message = str(getattr(self._snapshot, "message", "") or "")
        return message or "等待錄音引擎"

    @Property(str, notify=snapshotChanged)
    def partialText(self) -> str:
        text = str(getattr(self._snapshot, "partial_text", "") or "").strip()
        if text:
            return text
        if not self.recordingControlsEnabled:
            return "完成首次設定並啟動錄音後，這裡會顯示即時文字。"
        if bool(getattr(self._snapshot, "speech_active", False)):
            return "聽見你了，正在辨識…"
        return "等待你的聲音…"

    @Property(bool, notify=snapshotChanged)
    def hasPartialText(self) -> bool:
        return bool(str(getattr(self._snapshot, "partial_text", "") or "").strip())

    @Property(int, notify=snapshotChanged)
    def backlog(self) -> int:
        return max(0, int(getattr(self._snapshot, "backlog", 0) or 0))

    @Property(str, notify=snapshotChanged)
    def backlogText(self) -> str:
        return f"待處理 {self.backlog}"

    @Property(bool, notify=snapshotChanged)
    def paused(self) -> bool:
        return bool(getattr(self._snapshot, "paused", False))

    @Property(bool, notify=snapshotChanged)
    def speechActive(self) -> bool:
        return bool(getattr(self._snapshot, "speech_active", False))

    @Property(float, notify=snapshotChanged)
    def audioLevel(self) -> float:
        dbfs = _safe_float(getattr(self._snapshot, "rms_dbfs", -120.0))
        return max(0.0, min(1.0, (dbfs + 60.0) / 54.0))

    @Property(str, notify=sceneChanged)
    def sceneKey(self) -> str:
        return self._scene_key


    @Property(str, notify=dateChanged)
    def dateLabel(self) -> str:
        weekday = WEEKDAYS[self._day.weekday()]
        return f"{self._day.year} 年 {self._day.month} 月 {self._day.day} 日 · {weekday}"

    @Property(str, notify=dateChanged)
    def dayKey(self) -> str:
        return self._day.isoformat()

    @Property(int, notify=timelineRevisionChanged)
    def timelineRevision(self) -> int:
        return self._timeline_revision

    @Property(str, notify=settingsChanged)
    def recordsRoot(self) -> str:
        return str(getattr(getattr(self._controller, "config", None), "records_root", ""))

    @Property(int, notify=settingsChanged)
    def previewInterval(self) -> int:
        return int(getattr(getattr(self._controller, "config", None), "preview_interval_ms", 350))

    @Property(int, notify=settingsChanged)
    def endpointSilence(self) -> int:
        return int(getattr(getattr(self._controller, "config", None), "endpoint_silence_ms", 2000))

    @Property(int, notify=settingsChanged)
    def maxSegment(self) -> int:
        return int(getattr(getattr(self._controller, "config", None), "max_segment_ms", 28000))

    @Property(bool, notify=onboardingChanged)
    def onboardingCompleted(self) -> bool:
        config = getattr(self._controller, "config", None)
        return bool(getattr(config, "onboarding_completed", False))

    @Property(bool, notify=onboardingChanged)
    def onboardingPending(self) -> bool:
        return not self.onboardingCompleted and not self._onboarding_deferred

    @Property(int, notify=onboardingChanged)
    def onboardingStep(self) -> int:
        return self._onboarding_step

    @Property(str, notify=onboardingChanged)
    def onboardingRecordsRoot(self) -> str:
        return self._onboarding_records_root

    @Property(bool, notify=onboardingChanged)
    def onboardingRecordsTested(self) -> bool:
        return self._onboarding_records_tested

    @Property(bool, notify=onboardingChanged)
    def onboardingStartupEnabled(self) -> bool:
        return self._onboarding_startup_enabled

    @Property(bool, notify=onboardingChanged)
    def onboardingUpdateCheckEnabled(self) -> bool:
        return self._onboarding_update_check_enabled

    @Property(bool, notify=modelSetupChanged)
    def onboardingModelsReady(self) -> bool:
        return self._model_setup_state == "ready"

    @Property(bool, notify=modelSetupChanged)
    def onboardingModelBusy(self) -> bool:
        return self._model_setup_running

    @Property(str, notify=modelSetupChanged)
    def onboardingModelState(self) -> str:
        return self._model_setup_state

    @Property(str, notify=modelSetupChanged)
    def onboardingModelStatusText(self) -> str:
        return self._model_setup_message

    @Property(float, notify=modelSetupChanged)
    def onboardingModelProgress(self) -> float:
        if self._model_setup_total <= 0:
            return 0.0
        return min(1.0, self._model_setup_completed / self._model_setup_total)

    @Property(str, notify=modelSetupChanged)
    def onboardingModelProgressText(self) -> str:
        if self._model_setup_total <= 0:
            return self._model_setup_asset
        completed = _format_transfer_size(self._model_setup_completed)
        total = _format_transfer_size(self._model_setup_total)
        return f"{completed} / {total}"

    @Property(bool, notify=microphoneSelectionChanged)
    def onboardingMicrophoneReady(self) -> bool:
        return bool(self._selection_for_key(self._selected_microphone_key))

    @Property(bool, notify=settingsChanged)
    def startupEnabled(self) -> bool:
        config = getattr(self._controller, "config", None)
        return bool(getattr(config, "startup_enabled", False))

    @Property(bool, notify=settingsChanged)
    def updateCheckEnabled(self) -> bool:
        config = getattr(self._controller, "config", None)
        return bool(getattr(config, "update_check_enabled", False))

    @Property(bool, notify=updateCheckChanged)
    def updateAvailable(self) -> bool:
        return self._update_available

    @Property(str, notify=updateCheckChanged)
    def updateAvailableText(self) -> str:
        return self._update_available_text

    @Property(bool, notify=onboardingChanged)
    def microphoneSetupPending(self) -> bool:
        """Compatibility alias for the v3 QML window sizing contract."""
        return self.onboardingPending

    @Property(bool, notify=controllerStartChanged)
    def recordingEngineNeedsStart(self) -> bool:
        if not self.onboardingCompleted:
            return False
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        mode = _enum_value(getattr(selection, "mode", ""))
        if mode in {MicrophoneMode.PENDING.value, MicrophoneMode.SKIPPED.value}:
            return False
        workers_started = getattr(self._controller, "workers_started", None)
        if workers_started is not None:
            return not bool(workers_started)
        return not self._controller_started

    @Property(bool, notify=controllerStartChanged)
    def recordingControlsEnabled(self) -> bool:
        if not self.onboardingCompleted:
            return False
        workers_started = getattr(self._controller, "workers_started", None)
        if workers_started is not None:
            return bool(workers_started)
        state = _enum_value(getattr(self._snapshot, "state", "stopped"))
        return self._controller_started or state in {"recording", "paused", "degraded"}

    @Property("QVariantList", notify=microphoneDevicesChanged)
    def microphoneOptions(self) -> list[dict[str, Any]]:
        return [dict(option) for option in self._microphone_options]

    @Property(str, notify=microphoneDevicesChanged)
    def microphoneScanError(self) -> str:
        return self._microphone_scan_error

    @Property(str, notify=microphoneSelectionChanged)
    def selectedMicrophoneKey(self) -> str:
        return self._selected_microphone_key

    @Property(str, notify=microphoneSelectionChanged)
    def selectedMicrophoneLabel(self) -> str:
        option = next(
            (
                item
                for item in self._microphone_options
                if str(item.get("key", "")) == self._selected_microphone_key
            ),
            None,
        )
        return str((option or {}).get("label", "") or "")

    @Property(bool, notify=microphoneSelectionChanged)
    def settingsMicrophoneSelectionValid(self) -> bool:
        if self._selected_microphone_key:
            return True
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        return (
            _enum_value(getattr(selection, "mode", ""))
            == MicrophoneMode.SKIPPED.value
        )

    @Property(str, notify=snapshotChanged)
    def preferredInputName(self) -> str:
        snapshot_name = str(
            getattr(self._snapshot, "preferred_input_name", "") or ""
        ).strip()
        if snapshot_name:
            return snapshot_name
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        mode = _enum_value(getattr(selection, "mode", ""))
        if mode == MicrophoneMode.SYSTEM_DEFAULT.value:
            return "跟隨 Windows 預設"
        if mode == MicrophoneMode.FIXED.value:
            name = str(
                getattr(getattr(selection, "preferred_device", None), "name", "")
                or ""
            ).strip()
            return name or "固定麥克風"
        if mode == MicrophoneMode.SKIPPED.value:
            return "稍後設定"
        return "尚未選擇"

    @Property(str, notify=snapshotChanged)
    def activeInputName(self) -> str:
        return str(getattr(self._snapshot, "active_input_name", "") or "").strip()

    @Property(str, notify=snapshotChanged)
    def inputRoute(self) -> str:
        return _enum_value(getattr(self._snapshot, "input_route", ""))

    @Property(bool, notify=snapshotChanged)
    def inputSwitching(self) -> bool:
        return bool(getattr(self._snapshot, "input_switching", False))

    @Property(bool, notify=snapshotChanged)
    def preferredInputAvailable(self) -> bool:
        return bool(getattr(self._snapshot, "preferred_input_available", False))

    @Property(bool, notify=snapshotChanged)
    def inputFallbackActive(self) -> bool:
        route = self.inputRoute.casefold()
        return "fallback" in route or route in {"last_good", "unavailable"}

    @Property(str, notify=snapshotChanged)
    def inputStatusText(self) -> str:
        if self.inputSwitching:
            return "正在安全收束目前片段並切換麥克風…"
        active = self.activeInputName
        route_reason = str(
            getattr(self._snapshot, "input_route_reason", "") or ""
        ).strip()
        if self.inputRoute.casefold() == "unavailable":
            return route_reason or "目前沒有可用的麥克風；偏好設定仍已保留。"
        if self.inputFallbackActive:
            suffix = f"：{active}" if active else ""
            fallback = (
                f"偏好裝置「{self.preferredInputName}」目前無法使用；"
                f"暫用 Windows 預設{suffix}。偏好已保留。"
            )
            return f"{fallback} {route_reason}".strip() if route_reason else fallback
        if self.inputRoute.casefold() in {"system_default", "default"}:
            suffix = f"：{active}" if active else ""
            return f"正在跟隨 Windows 預設麥克風{suffix}"
        if active:
            return f"目前收音：{active}"
        return "目前尚未開始收音"

    @Property(str, notify=snapshotChanged)
    def inputRouteNoticeText(self) -> str:
        """Return only route state that adds information beyond preference and active input."""
        if self.inputSwitching or self.inputFallbackActive:
            return self.inputStatusText
        return ""

    @Property(bool, notify=microphoneTestChanged)
    def microphoneTestRunning(self) -> bool:
        return self._microphone_test_state == "running"

    @Property(str, notify=microphoneTestChanged)
    def microphoneTestState(self) -> str:
        return self._microphone_test_state

    @Property(str, notify=microphoneTestChanged)
    def microphoneTestMessage(self) -> str:
        return self._microphone_test_message

    @Property(float, notify=microphoneTestChanged)
    def microphoneTestLevel(self) -> float:
        return self._microphone_test_level

    @Property("QVariantList", notify=settingsHistoryChanged)
    def settingsHistoryEntries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._settings_history_entries]

    @Property(bool, notify=settingsChanged)
    def vocabularyLearningEnabled(self) -> bool:
        config = getattr(self._controller, "config", None)
        return bool(getattr(config, "vocabulary_learning_enabled", True))

    @Property("QVariantList", notify=vocabularyChanged)
    def vocabularyEntries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._vocabulary_entries]

    @Slot(result=bool)
    def checkOnboardingModels(self) -> bool:
        if not self.onboardingPending or self._model_setup_running:
            return False
        callback = self._model_status_callback
        if callback is None:
            self._model_setup_state = "ready"
            self._model_setup_message = "語音模型已就緒"
            self.modelSetupChanged.emit()
            return True
        return self._start_model_setup_operation(
            "check",
            callback,
            state="checking",
            message="正在確認本機語音模型…",
        )

    @Slot(result=bool)
    def repairOnboardingModels(self) -> bool:
        if not self.onboardingPending or self._model_setup_running:
            return False
        callback = self._model_provision_callback
        if callback is None:
            self.actionFailed.emit("此安裝環境未提供模型下載服務")
            return False
        return self._start_model_setup_operation(
            "repair",
            callback,
            state="downloading",
            message="正在準備從 Hugging Face 下載語音模型…",
            receives_progress=True,
        )

    def _start_model_setup_operation(
        self,
        operation: str,
        callback: Callable[..., Any],
        *,
        state: str,
        message: str,
        receives_progress: bool = False,
    ) -> bool:
        self._model_setup_generation += 1
        generation = self._model_setup_generation
        self._model_setup_running = True
        self._model_setup_state = state
        self._model_setup_message = message
        self._model_setup_completed = 0
        self._model_setup_total = 0
        self._model_setup_asset = ""
        self._model_setup_operation = operation
        self.modelSetupChanged.emit()

        def run() -> None:
            def emit_progress(value: object) -> None:
                with suppress(RuntimeError):
                    self._modelSetupProgressReceived.emit(generation, value)

            try:
                result = callback(emit_progress) if receives_progress else callback()
            except Exception as error:
                result = {
                    "state": "error",
                    "ready": False,
                    "message": f"模型{('下載' if operation == 'repair' else '檢查')}失敗：{error}",
                }
            with suppress(RuntimeError):
                self._modelSetupCompleted.emit(generation, result)

        threading.Thread(
            target=run,
            name=f"speech-journal-model-{operation}",
            daemon=True,
        ).start()
        return True

    @Slot(int, object)
    def _finish_model_setup_progress(self, generation: int, result: object) -> None:
        if generation != self._model_setup_generation or not self._model_setup_running:
            return
        normalized = _normalized_model_setup_result(
            result,
            default_state="downloading",
        )
        self._model_setup_state = str(normalized["state"])
        self._model_setup_message = str(normalized["message"])
        self._model_setup_completed = int(normalized["completed"])
        self._model_setup_total = int(normalized["total"])
        self._model_setup_asset = str(normalized["asset"])
        self.modelSetupChanged.emit()

    @Slot(int, object)
    def _finish_model_setup(self, generation: int, result: object) -> None:
        if generation != self._model_setup_generation:
            return
        operation = getattr(self, "_model_setup_operation", "check")
        normalized = _normalized_model_setup_result(
            result,
            default_state="error" if operation == "repair" else "not_ready",
        )
        self._model_setup_running = False
        self._model_setup_state = str(normalized["state"])
        self._model_setup_message = str(normalized["message"])
        self._model_setup_completed = int(normalized["completed"])
        self._model_setup_total = int(normalized["total"])
        self._model_setup_asset = str(normalized["asset"])
        self.modelSetupChanged.emit()
        if bool(normalized["ready"]):
            if operation == "repair":
                self.actionSucceeded.emit("語音模型下載完成")
        elif operation == "repair":
            self.actionFailed.emit(self._model_setup_message or "語音模型下載未完成，請重試")

    @Slot(str, bool, bool, result=bool)
    def advanceOnboarding(
        self,
        records_root: str,
        startup_enabled: bool,
        update_check_enabled: bool,
    ) -> bool:
        if not self.onboardingPending or self._onboarding_step >= 4:
            return False
        if self._onboarding_step == 1:
            if not self._test_records_folder(records_root):
                return False
        elif self._onboarding_step == 2:
            self._onboarding_startup_enabled = bool(startup_enabled)
            self._onboarding_update_check_enabled = bool(update_check_enabled)
            self.rescanMicrophones()
        elif self._onboarding_step == 3 and not self.onboardingMicrophoneReady:
            self._report_error("請先選擇一個麥克風或跟隨 Windows 預設")
            return False
        self._onboarding_step += 1
        self.onboardingChanged.emit()
        return True

    @Slot(result=bool)
    def retreatOnboarding(self) -> bool:
        if not self.onboardingPending or self._onboarding_step <= 0:
            return False
        self._onboarding_step -= 1
        self.onboardingChanged.emit()
        return True

    @Slot(result=bool)
    def deferOnboarding(self) -> bool:
        if self.onboardingCompleted:
            return False
        base = getattr(self._controller, "config", None)
        action = getattr(self._controller, "update_settings", None)
        if base is None or not callable(action):
            self._report_error("目前無法保存稍後設定狀態")
            return False
        deferred = replace(
            base,
            microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
            onboarding_completed=False,
            startup_enabled=False,
            update_check_enabled=False,
        )
        if not self._run_action(lambda: action(deferred)):
            return False
        self._onboarding_deferred = True
        self._consent_microphone_test_required = False
        self._onboarding_step = 0
        self._selected_microphone_key = ""
        self._reset_microphone_test()
        self._trigger_update_check(False)
        self.settingsChanged.emit()
        self.onboardingChanged.emit()
        self.microphoneSelectionChanged.emit()
        self.microphoneSetupChanged.emit()
        self._apply_window_mode()
        self.actionSucceeded.emit("已延後首次設定；目前不會錄音或建立登入自啟")
        return True

    @Slot(result=bool)
    def openOnboarding(self) -> bool:
        if self.onboardingCompleted:
            return False
        self._onboarding_deferred = False
        self._consent_microphone_test_required = False
        self._onboarding_step = 0
        self._onboarding_records_root = str(
            getattr(getattr(self._controller, "config", None), "records_root", "") or ""
        )
        self._onboarding_records_tested = False
        self._onboarding_startup_enabled = False
        self._onboarding_update_check_enabled = False
        self._selected_microphone_key = ""
        self._reset_microphone_test()
        self.rescanMicrophones()
        self.onboardingChanged.emit()
        self.microphoneSelectionChanged.emit()
        self.microphoneSetupChanged.emit()
        self._apply_window_mode()
        self.checkOnboardingModels()
        return True

    @Slot(result=bool)
    def startOnboardingRecording(self) -> bool:
        if not self.onboardingPending or self._onboarding_step != 4:
            self._report_error("請先完成首次設定的所有步驟")
            return False
        if not self._onboarding_records_tested:
            self._report_error("請先確認日記資料夾可寫入")
            return False
        if not self.onboardingModelsReady:
            self._report_error("語音模型尚未就緒，請先重試下載後再開始錄音")
            return False
        selection = self._selection_for_key(self._selected_microphone_key)
        if selection is None:
            self._report_error("請先選擇麥克風")
            return False
        base = getattr(self._controller, "config", None)
        action = getattr(self._controller, "update_settings", None)
        if base is None or not callable(action):
            self._report_error("目前無法儲存首次設定")
            return False

        startup_enabled = self._onboarding_startup_enabled
        if startup_enabled and not self._apply_startup_setting(True, announce=False):
            startup_enabled = False
        config = replace(
            base,
            records_root=self._onboarding_records_root,
            microphone=selection,
            onboarding_completed=True,
            startup_enabled=startup_enabled,
            update_check_enabled=self._onboarding_update_check_enabled,
        )
        try:
            config.validate()
        except Exception as error:
            if startup_enabled:
                self._apply_startup_setting(False, announce=False)
            self._report_error(str(error))
            return False
        if not self._run_action(lambda: action(config)):
            if startup_enabled:
                self._apply_startup_setting(False, announce=False)
            return False

        self._onboarding_deferred = False
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.settingsChanged.emit()
        self.snapshotChanged.emit()
        self.onboardingChanged.emit()
        self.microphoneSetupChanged.emit()
        self.controllerStartChanged.emit()
        self._refresh_settings_history()
        self._apply_window_mode()
        self._trigger_update_check(self._onboarding_update_check_enabled)
        self._consent_microphone_test_required = True
        self.actionSucceeded.emit("首次設定已完成，正在測試麥克風")
        self.testSelectedMicrophone()
        return True

    def _test_records_folder(self, records_root: str) -> bool:
        base = getattr(self._controller, "config", None)
        if base is None:
            self._report_error("找不到目前設定")
            return False
        try:
            candidate = replace(base, records_root=records_root.strip(), startup_enabled=False)
            candidate.validate()
            directory = Path(candidate.records_root)
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / f".auto-speech-journal-write-test-{uuid.uuid4().hex}.tmp"
            try:
                with probe.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write("write-test\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                probe.unlink(missing_ok=True)
        except Exception as error:
            self._onboarding_records_tested = False
            self._report_error(f"日記資料夾無法寫入：{error}")
            self.onboardingChanged.emit()
            return False
        self._onboarding_records_root = candidate.records_root
        self._onboarding_records_tested = True
        self.onboardingChanged.emit()
        self.actionSucceeded.emit("日記資料夾可正常寫入")
        return True

    def _apply_startup_setting(self, enabled: bool, *, announce: bool = True) -> bool:
        callback = self._startup_setting_callback
        if callback is None:
            self._report_error("此安裝環境尚未提供登入自啟服務，設定未變更")
            return False
        try:
            result = callback(bool(enabled))
            if result is False:
                raise RuntimeError("登入自啟服務拒絕這次變更")
            available = bool(getattr(result, "available", True))
            effective = bool(getattr(result, "enabled", enabled))
            if not available and enabled:
                self._report_error("Windows 工作排程器目前不可用，已改為手動啟動")
                return False
            if effective != enabled:
                self._report_error("登入自啟狀態未套用，設定已維持原值")
                return False
        except Exception as error:
            self._report_error(f"無法更新登入自啟設定：{error}")
            return False
        if announce:
            self.actionSucceeded.emit("已更新登入自啟設定")
        return True

    def _trigger_update_check(self, enabled: bool) -> None:
        self._update_check_generation += 1
        generation = self._update_check_generation
        self._update_check_requested = bool(enabled)
        if not enabled:
            self._set_update_notice(False, "", "")
        callback = self._update_check_callback
        if callback is None:
            return
        try:
            result = callback(
                enabled=bool(enabled),
                callback=lambda value: self._emit_update_check_result(generation, value),
            )
        except Exception:
            # Offline, rate-limit and service errors must never affect recording.
            return
        if result is not None and not isinstance(result, bool):
            self._emit_update_check_result(generation, result)

    def _emit_update_check_result(self, generation: int, result: Any) -> None:
        with suppress(RuntimeError):
            self._updateCheckCompleted.emit(generation, result)

    @Slot(int, object)
    def _finish_update_check(self, generation: int, result: Any) -> None:
        if generation != self._update_check_generation or not self._update_check_requested:
            return
        if isinstance(result, dict):
            getter = result.get
        else:
            def getter(name: str, default: Any = None) -> Any:
                return getattr(result, name, default)
        available = bool(getter("available", getter("update_available", False)))
        version = str(getter("version", getter("latest_version", "")) or "").strip()
        url = str(getter("release_url", getter("url", "")) or "").strip()
        if not available or not url:
            self._set_update_notice(False, "", "")
            return
        label = f"有新版本 {version} 可下載" if version else "有新版本可下載"
        self._set_update_notice(True, label, url)

    def _set_update_notice(self, available: bool, text: str, url: str) -> None:
        values = (bool(available), text, url)
        if values == (
            self._update_available,
            self._update_available_text,
            self._update_release_url,
        ):
            return
        self._update_available, self._update_available_text, self._update_release_url = values
        self.updateCheckChanged.emit()

    @Slot(result=bool)
    def openUpdateRelease(self) -> bool:
        url = QUrl(self._update_release_url)
        if (
            not self._update_available
            or not url.isValid()
            or url.scheme().casefold() != "https"
            or url.host().casefold() != "github.com"
        ):
            self._report_error("更新下載連結無效")
            return False
        return bool(QDesktopServices.openUrl(url))

    @Slot(result=int)
    def rescanMicrophones(self) -> int:
        try:
            provider = self._microphone_device_provider
            if provider is None:
                from .audio import list_wasapi_input_devices

                provider = list_wasapi_input_devices
            devices = list(provider())
            scan_error = ""
        except Exception as error:
            devices = []
            scan_error = str(error).strip() or "無法列出麥克風"

        default_device = next(
            (device for device in devices if bool(getattr(device, "is_default", False))),
            None,
        )
        options: list[dict[str, Any]] = [
            {
                "key": MicrophoneMode.SYSTEM_DEFAULT.value,
                "label": (
                    "跟隨 Windows 預設"
                    + (
                        f"（目前：{getattr(default_device, 'name', '')}）"
                        if default_device is not None
                        else "（目前沒有預設裝置）"
                    )
                ),
                "name": str(getattr(default_device, "name", "") or ""),
                "mode": MicrophoneMode.SYSTEM_DEFAULT.value,
                "available": default_device is not None,
                "selectable": default_device is not None,
                "offline": False,
                "isDefault": True,
                "detail": (
                    "執行中會自動跟隨 Windows 預設輸入裝置"
                    if default_device is not None
                    else "連接麥克風後再重新掃描"
                ),
            }
        ]
        fingerprints: dict[str, Any] = {}
        if default_device is not None:
            fingerprints[MicrophoneMode.SYSTEM_DEFAULT.value] = default_device.fingerprint()

        device_entries = [
            (device, device.fingerprint())
            for device in devices
        ]
        fixed_key_counts: dict[str, int] = {}
        for _, fingerprint in device_entries:
            key = _microphone_key_for_fingerprint(fingerprint)
            fixed_key_counts[key] = fixed_key_counts.get(key, 0) + 1

        for device, fingerprint in device_entries:
            key = _microphone_key_for_fingerprint(fingerprint)
            binding_available = bool(
                getattr(device, "fixed_binding_available", True)
            ) and fixed_key_counts.get(key, 0) == 1
            binding_error = str(getattr(device, "binding_error", "") or "").strip()
            if fixed_key_counts.get(key, 0) > 1:
                binding_error = "同一穩定識別對應多個端點，無法安全固定"
            label = str(getattr(device, "name", "") or "未命名麥克風")
            if bool(getattr(device, "is_default", False)):
                label += "（Windows 預設）"
            if not binding_available:
                label += "（無法安全固定）"
            options.append(
                {
                    "key": key,
                    "label": label,
                    "name": str(getattr(device, "name", "") or ""),
                    "mode": MicrophoneMode.FIXED.value,
                    "available": True,
                    "selectable": binding_available,
                    "offline": False,
                    "isDefault": bool(getattr(device, "is_default", False)),
                    "detail": binding_error or str(getattr(device, "host_api", "") or ""),
                }
            )
            fingerprints[key] = fingerprint

        configured = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        configured_key = _microphone_key_for_selection(configured)
        preferred = getattr(configured, "preferred_device", None)
        if (
            _enum_value(getattr(configured, "mode", ""))
            == MicrophoneMode.FIXED.value
            and preferred is not None
            and configured_key not in fingerprints
        ):
            preferred_name = str(getattr(preferred, "name", "") or "").strip().casefold()
            preferred_host = str(getattr(preferred, "host_api", "") or "").strip().casefold()
            identity_matches = [
                device
                for device in devices
                if str(getattr(device, "name", "") or "").strip().casefold()
                == preferred_name
                and (
                    not preferred_host
                    or str(getattr(device, "host_api", "") or "").strip().casefold()
                    == preferred_host
                )
            ]
            if len(identity_matches) == 1 and bool(
                getattr(identity_matches[0], "fixed_binding_available", True)
            ):
                configured_key = _microphone_key_for_fingerprint(
                    identity_matches[0].fingerprint()
                )
        if (
            _enum_value(getattr(configured, "mode", ""))
            == MicrophoneMode.FIXED.value
            and configured_key
            and configured_key not in fingerprints
        ):
            preferred_name = str(getattr(preferred, "name", "") or "偏好麥克風")
            options.append(
                {
                    "key": configured_key,
                    "label": f"{preferred_name}（目前離線）",
                    "name": preferred_name,
                    "mode": MicrophoneMode.FIXED.value,
                    "available": False,
                    "selectable": True,
                    "offline": True,
                    "isDefault": False,
                    "detail": "偏好已保留；失效時會暫用 Windows 預設",
                }
            )
            fingerprints[configured_key] = preferred

        previous_key = self._selected_microphone_key
        available_keys = {str(option["key"]) for option in options}
        selectable_keys = {
            str(option["key"])
            for option in options
            if bool(option.get("selectable", False))
        }
        if self.microphoneSetupPending:
            selected_key = previous_key if previous_key in selectable_keys else ""
        else:
            selected_key = (
                previous_key
                if previous_key in selectable_keys
                or (previous_key == configured_key and previous_key in available_keys)
                else configured_key
            )
        self._microphone_options = options
        self._microphone_fingerprints = fingerprints
        self._microphone_scan_error = scan_error
        self._microphone_has_selectable_route = any(
            bool(option.get("selectable", False)) for option in options
        )
        self._selected_microphone_key = selected_key
        self.microphoneDevicesChanged.emit()
        if selected_key != previous_key:
            self.microphoneSelectionChanged.emit()
        return len(devices)

    @Slot(str, result=bool)
    def selectMicrophone(self, key: str) -> bool:
        normalized = key.strip()
        option = next(
            (
                item
                for item in self._microphone_options
                if str(item.get("key", "")) == normalized
            ),
            None,
        )
        if option is None or not bool(option.get("selectable", False)):
            self.actionFailed.emit("這個麥克風目前無法安全選用")
            return False
        if normalized != self._selected_microphone_key:
            self._selected_microphone_key = normalized
            self.microphoneSelectionChanged.emit()
        self._reset_microphone_test()
        return True

    @Slot()
    def resetMicrophoneSelection(self) -> None:
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        key = (
            ""
            if self.microphoneSetupPending
            else _microphone_key_for_selection(selection)
        )
        if key != self._selected_microphone_key:
            self._selected_microphone_key = key
            self.microphoneSelectionChanged.emit()
        self._reset_microphone_test()

    @Slot(result=bool)
    def deferMicrophoneAfterStartFailure(self) -> bool:
        if not self.recordingEngineNeedsStart or self.onboardingPending:
            return False
        action = getattr(self._controller, "skip_microphone_setup", None)
        if not self._run_action(
            action,
            success="已改為稍後設定麥克風；目前不會錄音",
        ):
            return False
        self._after_microphone_configuration()
        QTimer.singleShot(0, self.startControllerIfReady)
        return True

    @Slot(result=bool)
    def startControllerIfReady(self) -> bool:
        if not self.onboardingCompleted or self._controller_started:
            return False
        if self._consent_microphone_test_required:
            if not self.microphoneTestRunning:
                self.testSelectedMicrophone()
            return False
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        if _enum_value(getattr(selection, "mode", "")) not in {
            MicrophoneMode.SYSTEM_DEFAULT.value,
            MicrophoneMode.FIXED.value,
        }:
            return False
        action = getattr(self._controller, "start", None)
        if not callable(action):
            self._report_error("目前無法啟動錄音引擎")
            self.controllerStartChanged.emit()
            return False
        if not self._run_action(action):
            self.controllerStartChanged.emit()
            return False
        self._controller_started = True
        self.controllerStartChanged.emit()
        return True

    @Slot(result=bool)
    def retryPreferredInput(self) -> bool:
        action = getattr(self._controller, "retry_preferred_input", None)
        return self._run_action(action, success="正在切回偏好麥克風")

    @Slot(result=bool)
    def testSelectedMicrophone(self) -> bool:
        if not self.onboardingCompleted:
            self.actionFailed.emit("完成首次設定並按下「開始錄音」後才能測試麥克風")
            return False
        if self.microphoneTestRunning:
            return False
        key = self._selected_microphone_key
        fingerprint = self._microphone_fingerprints.get(key)
        if fingerprint is None:
            self.actionFailed.emit("所選麥克風目前無法測試，請重新掃描")
            return False

        request_id = uuid.uuid4().hex
        self._microphone_test_request_id = request_id
        self._microphone_test_state = "running"
        self._microphone_test_message = "正在測試 800 毫秒的輸入音量…"
        self._microphone_test_level = 0.0
        self.microphoneTestChanged.emit()

        option = next(
            (
                item
                for item in self._microphone_options
                if str(item.get("key", "")) == key
            ),
            {},
        )
        selected_name = str(option.get("name", "") or "")
        if selected_name and selected_name == self.activeInputName:
            rms_dbfs = _safe_float(getattr(self._snapshot, "rms_dbfs", -120.0))
            peak_dbfs = _safe_float(getattr(self._snapshot, "peak_dbfs", -120.0))
            rms = 10 ** (rms_dbfs / 20)
            peak = 10 ** (peak_dbfs / 20)
            self._finish_microphone_test(request_id, True, "", peak, rms)
            return True

        threading.Thread(
            target=self._measure_microphone_in_background,
            args=(
                request_id,
                fingerprint,
                key == MicrophoneMode.SYSTEM_DEFAULT.value,
            ),
            name="speech-journal-microphone-test",
            daemon=True,
        ).start()
        QTimer.singleShot(
            MICROPHONE_TEST_TIMEOUT_MS,
            lambda: self._timeout_microphone_test(request_id),
        )
        return True

    def _timeout_microphone_test(self, request_id: str) -> None:
        if (
            request_id != self._microphone_test_request_id
            or not self.microphoneTestRunning
        ):
            return
        self._microphone_test_request_id = ""
        self._microphone_test_state = "error"
        self._microphone_test_message = (
            "麥克風測試逾時，設定已保存；請重新掃描後從設定頁重試。"
        )
        self._microphone_test_level = 0.0
        self.microphoneTestChanged.emit()
        self.actionFailed.emit(self._microphone_test_message)

    @Slot(str, bool, str, float, float)
    def _finish_microphone_test(
        self,
        request_id: str,
        ok: bool,
        error: str,
        peak: float,
        rms: float,
    ) -> None:
        if request_id != self._microphone_test_request_id:
            return
        self._microphone_test_request_id = ""
        self._microphone_test_level = max(0.0, min(1.0, float(peak) * 5.0))
        start_after_test = self._consent_microphone_test_required
        if not ok:
            self._microphone_test_state = "error"
            self._microphone_test_message = (
                f"麥克風測試失敗：{error}。設定已保存，可從設定頁重試。"
            )
            self.actionFailed.emit(self._microphone_test_message)
        elif rms < 0.0001:
            self._microphone_test_state = "warning"
            self._microphone_test_message = "連線成功，但幾乎沒有收到聲音，請確認音量與權限。"
        else:
            self._microphone_test_state = "success"
            self._microphone_test_message = f"測試成功，RMS {rms:.6f}、peak {peak:.6f}"
            self.actionSucceeded.emit("麥克風測試成功")
        if start_after_test and ok:
            self._consent_microphone_test_required = False
        self.microphoneTestChanged.emit()
        if start_after_test and ok:
            QTimer.singleShot(0, self.startControllerIfReady)

    def _measure_microphone_in_background(
        self,
        request_id: str,
        fingerprint: Any,
        follow_system_default: bool,
    ) -> None:
        try:
            from .audio import measure_input_level

            if follow_system_default:
                result = measure_input_level(
                    fingerprint,
                    duration_ms=800,
                    follow_system_default=True,
                )
            else:
                result = measure_input_level(fingerprint, duration_ms=800)
        except Exception as error:
            self._emit_microphone_test_result(
                request_id,
                False,
                str(error).strip() or "未知錯誤",
                0.0,
                0.0,
            )
            return
        self._emit_microphone_test_result(
            request_id,
            True,
            "",
            float(getattr(result, "peak", 0.0) or 0.0),
            float(getattr(result, "rms", 0.0) or 0.0),
        )

    def _emit_microphone_test_result(
        self,
        request_id: str,
        ok: bool,
        error: str,
        peak: float,
        rms: float,
    ) -> None:
        if request_id != self._microphone_test_request_id:
            return
        with suppress(RuntimeError):
            self._microphoneTestCompleted.emit(request_id, ok, error, peak, rms)

    def _selection_for_key(self, key: str) -> MicrophoneSelection | None:
        option = next(
            (
                item
                for item in self._microphone_options
                if str(item.get("key", "")) == key
            ),
            None,
        )
        if option is None or not bool(option.get("selectable", False)):
            return None
        if key == MicrophoneMode.SYSTEM_DEFAULT.value:
            return MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        fingerprint = self._microphone_fingerprints.get(key)
        if not key.startswith("fixed:") or fingerprint is None:
            return None
        return MicrophoneSelection(
            mode=MicrophoneMode.FIXED,
            preferred_device=fingerprint,
        )

    def _configure_microphone(
        self,
        selection: MicrophoneSelection,
        *,
        announce: bool = True,
    ) -> bool:
        action = getattr(self._controller, "configure_microphone", None)
        if callable(action):
            return self._run_action(
                lambda: action(selection),
                success="麥克風偏好已儲存" if announce else "",
            )
        config = getattr(self._controller, "config", None)
        update = getattr(self._controller, "update_settings", None)
        if config is None or not callable(update):
            self._report_error("目前無法儲存麥克風設定")
            return False
        return self._run_action(
            lambda: update(replace(config, microphone=selection)),
            success="麥克風偏好已儲存" if announce else "",
        )

    def _after_microphone_configuration(self) -> None:
        configured = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        key = _microphone_key_for_selection(configured)
        if key != self._selected_microphone_key:
            self._selected_microphone_key = key
            self.microphoneSelectionChanged.emit()
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.settingsChanged.emit()
        self.snapshotChanged.emit()
        self.microphoneSetupChanged.emit()
        self.controllerStartChanged.emit()
        self._refresh_settings_history()
        self._apply_window_mode()

    def _reset_microphone_test(self) -> None:
        self._microphone_test_request_id = ""
        self._microphone_test_state = "idle"
        self._microphone_test_message = ""
        self._microphone_test_level = 0.0
        self.microphoneTestChanged.emit()

    @Slot(result=int)
    def rescanFonts(self) -> int:
        count = self._rescan_fonts()
        configured = self._font_catalog.canonical_family(
            str(
                getattr(getattr(self._controller, "config", None), "ui_font_family", "")
                or ""
            )
        )
        if configured in self._available_font_families:
            self._set_runtime_appearance(configured, self._ui_font_size)
        self.actionSucceeded.emit(f"已找到 {count} 種本機字體")
        return count

    @Slot()
    def openFontFolder(self) -> None:
        folder = self._font_catalog.primary_directory
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._report_error(f"無法建立字體資料夾：{error}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot(str, int, result=bool)
    def applyAppearance(self, font_family: str, font_size: int) -> bool:
        family = font_family.strip()
        if family not in self._available_font_families:
            self._report_error("請先將字體放入本機字體資料夾並重新掃描")
            return False
        base = getattr(self._controller, "config", None)
        if base is None:
            self._report_error("找不到目前設定")
            return False
        raw = base.to_dict()
        raw.update(ui_font_family=family, ui_font_size=int(font_size))
        try:
            config = AppConfig.from_dict(raw)
        except Exception as error:
            self._report_error(str(error))
            return False
        action = getattr(self._controller, "update_settings", None)
        if not callable(action):
            self._report_error("目前無法儲存介面設定")
            return False
        if not self._run_action(lambda: action(config), success="字體設定已套用"):
            return False
        self._set_runtime_appearance(config.ui_font_family, config.ui_font_size)
        self.settingsChanged.emit()
        self._refresh_settings_history()
        return True

    @Slot(bool)
    def refresh(self, force_timeline: bool = False) -> None:
        try:
            tick = getattr(self._controller, "tick", None)
            if callable(tick):
                tick()
        except Exception as error:  # controller owns the durable error state
            self._report_error(str(error))

        previous_snapshot = self._snapshot
        self._snapshot = getattr(self._controller, "snapshot", previous_snapshot)
        if self._snapshot != previous_snapshot:
            self.snapshotChanged.emit()

        current_day = self._local_now().date()
        if current_day != self._day:
            self._day = current_day
            force_timeline = True
            self.dateChanged.emit()
            self.sceneChanged.emit()

        self._refresh_scene()
        self._refresh_timeline(force=force_timeline)

    @Slot()
    def togglePause(self) -> None:
        self._run_action(getattr(self._controller, "toggle_pause", None))

    @Slot()
    def toggleExpanded(self) -> None:
        if self.microphoneSetupPending:
            return
        if self._expanded:
            self.collapseToCompact()
            return
        self._capture_compact_position()
        self._expanded = True
        self.expandedChanged.emit()
        self._apply_window_mode()
        self._refresh_timeline(force=True)

    @Slot()
    def collapseToCompact(self) -> None:
        if not self._expanded:
            return
        self._capture_expanded_size()
        self._expanded = False
        self.expandedChanged.emit()
        self._apply_window_mode()

    @Slot()
    def handleNativeClose(self) -> None:
        if self._allow_close or self._window is None:
            return
        if self._expanded:
            self.collapseToCompact()
        else:
            self._window.showMinimized()

    @Slot(int, int)
    def rememberCompactPosition(self, x: int, y: int) -> None:
        if not self._expanded and not self.microphoneSetupPending:
            self._compact_position = QPoint(int(x), int(y))

    @Slot(int, int)
    def rememberExpandedSize(self, width: int, height: int) -> None:
        if not self._expanded:
            return
        candidate = QSize(int(width), int(height))
        if (
            candidate.width() < EXPANDED_MIN_WIDTH
            or candidate.height() < EXPANDED_MIN_HEIGHT
        ):
            return
        self._set_expanded_size(candidate)

    @Slot()
    def persistWindowState(self) -> None:
        if self._expanded:
            self._capture_expanded_size()
        else:
            self._capture_compact_position()
        if self._compact_position is not None:
            self._settings.setValue("compactX", self._compact_position.x())
            self._settings.setValue("compactY", self._compact_position.y())
            self._settings.setValue("windowPosition", self._compact_position)
        self._settings.setValue("expandedWidth", self._expanded_size.width())
        self._settings.setValue("expandedHeight", self._expanded_size.height())
        self._settings.sync()

    @Slot(str)
    def beginEdit(self, segment_id: str) -> None:
        self._timeline_model.begin_edit(segment_id)

    @Slot(str, str)
    def updateDraft(self, segment_id: str, text: str) -> None:
        self._timeline_model.update_draft(segment_id, text)

    @Slot(str)
    def cancelEdit(self, segment_id: str) -> None:
        self._timeline_model.cancel_edit(segment_id)

    @Slot(str)
    def saveEdit(self, segment_id: str) -> None:
        text = self._timeline_model.draft_for(segment_id).strip()
        if not text:
            self._report_edit_error(segment_id, "修正文字不可為空")
            return
        action = getattr(self._controller, "correct_segment", None)
        if not callable(action):
            action = getattr(self._controller, "correct_current", None)
        if not callable(action):
            self._report_edit_error(segment_id, "目前無法修正此片段")
            return
        try:
            if getattr(action, "__name__", "") == "correct_current":
                action(text)
            else:
                action(segment_id, text)
        except Exception as error:
            self._report_edit_error(segment_id, str(error))
            return
        self._timeline_model.finish_edit(segment_id, text)
        self.actionSucceeded.emit("已儲存修正")
        self.refresh(force_timeline=True)

    @Slot(result="QVariantList")
    def availableHours(self) -> list[str]:
        try:
            action = getattr(self._controller, "available_hours", None)
            return list(action()) if callable(action) else []
        except Exception as error:
            self._report_error(str(error))
            return []

    @Slot(str, result=bool)
    def deleteHour(self, hour_key: str) -> bool:
        action = getattr(self._controller, "delete_hour", None)
        if not callable(action):
            self._report_error("目前無法刪除時段")
            return False
        if self._run_action(lambda: action(hour_key), success=f"已刪除 {hour_key}"):
            self.refresh(force_timeline=True)
            return True
        return False

    @Slot(result=int)
    def refreshVocabulary(self) -> int:
        self._refresh_vocabulary()
        return len(self._vocabulary_entries)

    @Slot(str, result=bool)
    def deleteVocabularyTerm(self, term: str) -> bool:
        action = getattr(self._controller, "delete_vocabulary_term", None)
        if not callable(action):
            self._report_error("目前無法刪除校正字典詞語")
            return False
        try:
            deleted = bool(action(term))
        except Exception as error:
            self._report_error(str(error))
            return False
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.snapshotChanged.emit()
        self._refresh_vocabulary()
        if deleted:
            self.actionSucceeded.emit(f"已刪除「{term}」")
        return deleted

    @Slot(result=bool)
    def clearVocabulary(self) -> bool:
        action = getattr(self._controller, "clear_vocabulary", None)
        if not callable(action):
            self._report_error("目前無法清空校正字典")
            return False
        if not self._run_action(action, success="已清空校正字典"):
            return False
        self._refresh_vocabulary()
        return True

    @Slot(bool, result=bool)
    def setVocabularyLearningEnabled(self, enabled: bool) -> bool:
        action = getattr(self._controller, "set_vocabulary_learning_enabled", None)
        if not callable(action):
            self._report_error("目前無法變更校正字典自動學習設定")
            return False
        if not self._run_action(
            lambda: action(bool(enabled)),
            success="已更新校正字典自動學習設定",
        ):
            return False
        self.settingsChanged.emit()
        self._refresh_settings_history()
        return True

    @Slot()
    def openRecordsFolder(self) -> None:
        action = getattr(self._controller, "open_records_folder", None)
        self._run_action(action)

    @Slot()
    def openSettingsHistoryFile(self) -> None:
        action = getattr(self._controller, "open_settings_history_file", None)
        self._run_action(action, success="已開啟設定紀錄")

    @Slot(str, result=str)
    def chooseRecordsFolder(self, initial: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        selected = QFileDialog.getExistingDirectory(None, "選擇紀錄資料夾", initial)
        return selected or initial

    @Slot(str, int, int, int, result=bool)
    @Slot(str, int, int, int, str, result=bool)
    @Slot(str, int, int, int, str, bool, bool, result=bool)
    def applySettings(
        self,
        records_root: str,
        preview_interval_ms: int,
        endpoint_silence_ms: int,
        max_segment_ms: int,
        microphone_key: str = "",
        startup_enabled: bool | None = None,
        update_check_enabled: bool | None = None,
    ) -> bool:
        base = getattr(self._controller, "config", None)
        if base is None:
            self._report_error("找不到目前設定")
            return False
        requested_key = microphone_key.strip()
        current_selection = getattr(base, "microphone", None)
        current_key = _microphone_key_for_selection(current_selection)
        if not bool(getattr(base, "onboarding_completed", False)) and requested_key:
            self._report_error("請先從首次設定按下「開始錄音」再變更麥克風")
            return False
        requested_selection = None
        if requested_key and requested_key != current_key:
            requested_selection = self._selection_for_key(requested_key)
            if requested_selection is None:
                self._report_error("所選麥克風目前無法套用，請重新掃描")
                return False
        raw = base.to_dict()
        raw.update(
            records_root=records_root.strip(),
            preview_interval_ms=int(preview_interval_ms),
            endpoint_silence_ms=int(endpoint_silence_ms),
            max_segment_ms=int(max_segment_ms),
            startup_enabled=(
                bool(startup_enabled)
                if startup_enabled is not None
                else bool(getattr(base, "startup_enabled", False))
            ),
            update_check_enabled=(
                bool(update_check_enabled)
                if update_check_enabled is not None
                else bool(getattr(base, "update_check_enabled", False))
            ),
        )
        try:
            config = AppConfig.from_dict(raw)
            if requested_selection is not None:
                config = replace(config, microphone=requested_selection)
                config.validate()
        except Exception as error:
            self._report_error(str(error))
            return False
        action = getattr(self._controller, "update_settings", None)
        if not callable(action):
            self._report_error("目前無法儲存設定")
            return False
        startup_changed = config.startup_enabled != bool(
            getattr(base, "startup_enabled", False)
        )
        if startup_changed and not self._apply_startup_setting(config.startup_enabled):
            return False
        if not self._run_action(lambda: action(config)):
            if startup_changed:
                self._apply_startup_setting(
                    bool(getattr(base, "startup_enabled", False)),
                    announce=False,
                )
            self.controllerStartChanged.emit()
            return False
        if requested_key:
            self._selected_microphone_key = requested_key
            self.microphoneSelectionChanged.emit()
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.settingsChanged.emit()
        self.snapshotChanged.emit()
        self._refresh_settings_history()
        self._trigger_update_check(config.update_check_enabled)
        message = (
            "設定已儲存，正在切換麥克風"
            if requested_selection is not None
            else "設定已儲存"
        )
        self.actionSucceeded.emit(message)
        if self.recordingEngineNeedsStart:
            QTimer.singleShot(0, self.startControllerIfReady)
        return True

    @Slot()
    def exitApplication(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._microphone_test_request_id = ""
        self.persistWindowState()
        self._poll_timer.stop()
        stop = getattr(self._controller, "stop", None)
        try:
            stopped = stop(suppress_errors=True) if callable(stop) else True
        except Exception as error:
            stopped = False
            self._report_error(str(error))
        if stopped is False:
            self._stopping = False
            self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
            self.snapshotChanged.emit()
            self._poll_timer.start()
            self.actionFailed.emit("尚有錄音等待安全寫入；完成後再按一次結束程式。")
            return
        self._allow_close = True
        self.allowCloseChanged.emit()
        if self._window is not None:
            self._window.close()
        self._application.quit()

    @Slot()
    def showAboutToOpen(self) -> None:
        self._refresh_timeline(force=True)

    def shutdown(self) -> None:
        self._microphone_test_request_id = ""
        self._model_setup_generation += 1
        self._model_setup_running = False
        self._poll_timer.stop()
        self.persistWindowState()

    def _rescan_fonts(self) -> int:
        with suppress(OSError):
            self._font_catalog.primary_directory.mkdir(parents=True, exist_ok=True)
        local_families = self._font_catalog.rescan()
        families = set(local_families)
        if self._fallback_ui_font_family:
            families.add(self._fallback_ui_font_family)
        updated = tuple(sorted(families, key=str.casefold))
        if updated != self._available_font_families:
            self._available_font_families = updated
            self.availableFontsChanged.emit()
        if self._ui_font_family not in updated:
            self._set_runtime_appearance(
                self._fallback_ui_font_family,
                self._ui_font_size,
            )
        return len(local_families)

    def _load_configured_appearance(self) -> None:
        config = getattr(self._controller, "config", None)
        requested_family = self._font_catalog.canonical_family(
            str(getattr(config, "ui_font_family", "") or "")
        )
        requested_size = int(getattr(config, "ui_font_size", DEFAULT_UI_FONT_SIZE))
        family = (
            requested_family
            if requested_family in self._available_font_families
            else self._fallback_ui_font_family
        )
        self._set_runtime_appearance(family, requested_size)

    def _set_runtime_appearance(self, family: str, size: int) -> None:
        bounded_size = min(MAX_UI_FONT_SIZE, max(MIN_UI_FONT_SIZE, int(size)))
        changed = family != self._ui_font_family or bounded_size != self._ui_font_size
        self._ui_font_family = family
        self._ui_font_size = bounded_size
        font = QFont(family)
        font.setPixelSize(bounded_size)
        self._application.setFont(font)
        if changed:
            self.appearanceChanged.emit()

    def _refresh_settings_history(self) -> None:
        entries = self._read_settings_history()
        if entries == self._settings_history_entries:
            return
        self._settings_history_entries = entries
        self.settingsHistoryChanged.emit()

    def _refresh_vocabulary(self) -> None:
        entries = self._read_vocabulary_entries()
        if entries == self._vocabulary_entries:
            return
        self._vocabulary_entries = entries
        self.vocabularyChanged.emit()

    def _read_vocabulary_entries(self) -> list[dict[str, Any]]:
        action = getattr(self._controller, "learned_vocabulary", None)
        if not callable(action):
            return []
        try:
            counts = dict(action())
            entries = [
                {"term": str(term), "count": int(count)}
                for term, count in counts.items()
                if str(term).strip() and int(count) > 0
            ]
        except Exception:
            return []
        return sorted(entries, key=lambda entry: (-entry["count"], entry["term"]))

    def _read_settings_history(self) -> list[dict[str, Any]]:
        action = getattr(self._controller, "recent_settings_history", None)
        if not callable(action):
            return []
        try:
            entries = tuple(action(5))
        except Exception:
            return []
        return [self._format_settings_history_entry(entry) for entry in entries]

    @staticmethod
    def _format_settings_history_entry(entry: Any) -> dict[str, Any]:
        timestamp = getattr(entry, "timestamp_utc", None)
        if isinstance(timestamp, str):
            with suppress(ValueError):
                timestamp = datetime.fromisoformat(timestamp)
        timestamp_label = "時間未知"
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=TAIPEI)
            timestamp_label = timestamp.astimezone(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")

        fields = tuple(getattr(entry, "changed_fields", ()) or ())
        before = dict(getattr(entry, "before", {}) or {})
        after = dict(getattr(entry, "after", {}) or {})
        labels = [SETTINGS_FIELD_LABELS.get(field, field) for field in fields]
        details = [
            (
                f"{SETTINGS_FIELD_LABELS.get(field, field)}："
                f"{JournalViewModel._format_settings_value(field, before.get(field))} → "
                f"{JournalViewModel._format_settings_value(field, after.get(field))}"
            )
            for field in fields
        ]
        return {
            "timestamp": timestamp_label,
            "summary": f"{timestamp_label} · 已變更{'、'.join(labels)}",
            "details": "\n".join(details),
            "changedFields": list(fields),
        }

    @staticmethod
    def _format_settings_value(field: str, value: Any) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if value is None or value == "":
            return "未設定"
        if field.endswith("_ms"):
            return f"{value} 毫秒"
        if field == "ui_font_size":
            return f"{value} px"
        if field == "ui_font_family":
            return FONT_FAMILY_DISPLAY_NAMES.get(str(value), str(value))
        if field == "microphone.mode":
            return {
                MicrophoneMode.PENDING.value: "尚未選擇",
                MicrophoneMode.SKIPPED.value: "稍後設定",
                MicrophoneMode.SYSTEM_DEFAULT.value: "跟隨 Windows 預設",
                MicrophoneMode.FIXED.value: "固定裝置",
            }.get(str(value), str(value))
        return str(value)

    def _refresh_scene(self) -> None:
        desired = self._desired_scene(self._snapshot)
        if desired == self._scene_key:
            return
        now = self._monotonic()
        if now - self._scene_changed_at < SCENE_HOLD_SECONDS:
            return
        self._scene_key = desired
        self._scene_changed_at = now
        self.sceneChanged.emit()

    def _refresh_timeline(self, *, force: bool = False) -> None:
        snapshot = self._snapshot
        explicit_revision = getattr(snapshot, "timeline_revision", None)
        if explicit_revision is None:
            key: object = (
                self._day,
                getattr(snapshot, "current_segment_id", None),
                getattr(snapshot, "correctable_segment_id", None),
                getattr(snapshot, "final_text", ""),
                getattr(snapshot, "backlog", 0),
            )
            revision = self._timeline_revision + (1 if key != self._timeline_key else 0)
        else:
            revision = int(explicit_revision)
            key = (self._day, revision)
        if not force and key == self._timeline_key:
            return

        rows = self._read_timeline_rows(snapshot)
        self.timelineUpdating.emit()
        added = self._timeline_model.replace_rows(rows)
        self._timeline_key = key
        if revision != self._timeline_revision:
            self._timeline_revision = revision
            self.timelineRevisionChanged.emit()
        self.timelineUpdated.emit(added)

    def _read_timeline_rows(self, snapshot: Any) -> list[_TimelineRow]:
        timeline_action = getattr(self._controller, "timeline_for_date", None)
        if callable(timeline_action):
            try:
                timeline = timeline_action(self._day)
                return self._flatten_timeline(timeline)
            except Exception as error:
                self._report_error(f"讀取今日時間軸失敗：{error}")
        return self._fallback_rows(snapshot)

    @staticmethod
    def _flatten_timeline(timeline: Any) -> list[_TimelineRow]:
        rows: list[_TimelineRow] = []
        for hour in tuple(getattr(timeline, "hours", ()) or ()):
            hour_key = str(getattr(hour, "hour_key", "") or "")
            hour_label = str(getattr(hour, "label", "") or "")
            segments = tuple(getattr(hour, "segments", ()) or ())
            for index, segment in enumerate(segments):
                rows.append(
                    _TimelineRow(
                        segment_id=str(getattr(segment, "segment_id", "") or ""),
                        hour_key=str(getattr(segment, "hour_key", hour_key) or hour_key),
                        hour_label=hour_label or JournalViewModel._hour_label(hour_key),
                        is_hour_start=index == 0,
                        hour_segment_count=len(segments),
                        time_label=str(getattr(segment, "time_label", "") or ""),
                        text=str(
                            getattr(
                                segment,
                                "text",
                                getattr(segment, "display_text", ""),
                            )
                            or ""
                        ),
                        status_label=str(getattr(segment, "status_label", "") or ""),
                        editable=bool(getattr(segment, "editable", False)),
                        state=_enum_value(getattr(segment, "state", "")),
                        last_error=str(getattr(segment, "last_error", "") or ""),
                    )
                )
        return rows

    @staticmethod
    def _fallback_rows(snapshot: Any) -> list[_TimelineRow]:
        segment_id = str(getattr(snapshot, "correctable_segment_id", "") or "")
        text = str(
            getattr(snapshot, "correctable_text", "")
            or getattr(snapshot, "final_text", "")
            or ""
        )
        if not segment_id or not text:
            return []
        hour_key = str(getattr(snapshot, "current_hour_key", "") or "")
        return [
            _TimelineRow(
                segment_id=segment_id,
                hour_key=hour_key,
                hour_label=JournalViewModel._hour_label(hour_key),
                is_hour_start=True,
                hour_segment_count=1,
                time_label="",
                text=text,
                status_label="已定稿",
                editable=True,
                state="final_ready",
                last_error="",
            )
        ]

    @staticmethod
    def _hour_label(hour_key: str) -> str:
        tail = hour_key.rsplit("_", 1)[-1]
        return f"{tail}:00" if tail.isdigit() else hour_key

    @staticmethod
    def _desired_scene(snapshot: Any) -> str:
        if snapshot is None:
            return "stopped"
        state = _enum_value(getattr(snapshot, "state", "stopped"))
        severity = _enum_value(getattr(snapshot, "severity", "info"))
        if state == "error" or severity == "error":
            return "error"
        if state == "degraded" or severity == "warning":
            return "degraded"
        if bool(getattr(snapshot, "paused", False)) or state == "paused":
            return "paused"
        if state == "starting":
            return "starting"
        if state == "stopped":
            return "stopped"
        if bool(getattr(snapshot, "speech_active", False)):
            return "capturing"
        if int(getattr(snapshot, "backlog", 0) or 0) > 0:
            return "finalizing"
        return "listening"

    def _apply_window_mode(self, *, initial: bool = False) -> None:
        window = self._window
        if window is None:
            return
        center = QPoint(
            window.x() + max(window.width(), 1) // 2,
            window.y() + max(window.height(), 1) // 2,
        )
        screen = (
            QGuiApplication.screenAt(center)
            or window.screen()
            or QGuiApplication.primaryScreen()
        )
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        setup_pending = self.microphoneSetupPending
        if not self._expanded and not setup_pending:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = window.isVisible()
        window.setFlags(flags)

        if setup_pending:
            window.setMinimumSize(QSize(0, 0))
            window.setMaximumSize(QSize(SETUP_WIDTH, SETUP_HEIGHT))
            window.setMinimumSize(QSize(SETUP_WIDTH, SETUP_HEIGHT))
            window.resize(SETUP_WIDTH, SETUP_HEIGHT)
        elif self._expanded:
            if screen is not None:
                size = self._clamp_expanded_size(
                    self._expanded_size,
                    screen.availableGeometry().size(),
                )
                maximum = QSize(
                    max(EXPANDED_MIN_WIDTH, screen.availableGeometry().width()),
                    max(EXPANDED_MIN_HEIGHT, screen.availableGeometry().height()),
                )
            else:
                size = self._expanded_size
                maximum = QSize(16_777_215, 16_777_215)
            self._set_expanded_size(size)
            window.setMinimumSize(QSize(0, 0))
            window.setMaximumSize(maximum)
            window.setMinimumSize(QSize(EXPANDED_MIN_WIDTH, EXPANDED_MIN_HEIGHT))
            window.resize(size)
        else:
            # Changing flags may recreate the native window. Apply the compact
            # constraints after flags and clear the larger workspace minimum first.
            window.setMinimumSize(QSize(0, 0))
            window.setMaximumSize(QSize(COMPACT_WIDTH, COMPACT_HEIGHT))
            window.setMinimumSize(QSize(COMPACT_WIDTH, COMPACT_HEIGHT))
            window.resize(COMPACT_WIDTH, COMPACT_HEIGHT)

        width, height = window.width(), window.height()
        if (self._expanded or setup_pending) and screen is not None:
            available = screen.availableGeometry()
            window.setPosition(
                available.x() + (available.width() - width) // 2,
                available.y() + (available.height() - height) // 2,
            )
        elif self._compact_position is not None:
            self._compact_position = self._clamp_compact_position(
                self._compact_position,
                QSize(width, height),
            )
            window.setPosition(self._compact_position)
        elif initial and screen is not None:
            available = screen.availableGeometry()
            window.setPosition(
                available.right() - width - 24,
                available.top() + 24,
            )
            self._compact_position = window.position()
        if was_visible:
            window.show()

    def _capture_compact_position(self) -> None:
        if self._window is not None and not self._expanded:
            self._compact_position = self._window.position()

    def _capture_expanded_size(self) -> None:
        if self._window is None or not self._expanded:
            return
        self._set_expanded_size(QSize(self._window.width(), self._window.height()))

    def _set_expanded_size(self, size: QSize) -> None:
        bounded = QSize(
            max(EXPANDED_MIN_WIDTH, int(size.width())),
            max(EXPANDED_MIN_HEIGHT, int(size.height())),
        )
        if bounded == self._expanded_size:
            return
        self._expanded_size = bounded
        self.expandedSizeChanged.emit()

    def _read_position(self) -> QPoint | None:
        raw_x = self._settings.value("compactX")
        raw_y = self._settings.value("compactY")
        if raw_x is not None and raw_y is not None:
            try:
                return QPoint(int(raw_x), int(raw_y))
            except (TypeError, ValueError):
                pass
        legacy = self._settings.value("windowPosition")
        return legacy if isinstance(legacy, QPoint) else None

    def _read_expanded_size(self) -> QSize:
        try:
            width = int(self._settings.value("expandedWidth", EXPANDED_WIDTH))
            height = int(self._settings.value("expandedHeight", EXPANDED_HEIGHT))
        except (TypeError, ValueError):
            return QSize(EXPANDED_WIDTH, EXPANDED_HEIGHT)
        return QSize(
            max(EXPANDED_MIN_WIDTH, width),
            max(EXPANDED_MIN_HEIGHT, height),
        )

    @staticmethod
    def _clamp_expanded_size(size: QSize, available: QSize) -> QSize:
        max_width = max(EXPANDED_MIN_WIDTH, available.width())
        max_height = max(EXPANDED_MIN_HEIGHT, available.height())
        return QSize(
            min(max(EXPANDED_MIN_WIDTH, size.width()), max_width),
            min(max(EXPANDED_MIN_HEIGHT, size.height()), max_height),
        )

    @staticmethod
    def _clamp_compact_position(position: QPoint, size: QSize) -> QPoint:
        screens = tuple(QGuiApplication.screens())
        primary = QGuiApplication.primaryScreen()
        if not screens or primary is None:
            return position

        window_rect = QRect(position, size)
        target = next(
            (
                screen
                for screen in screens
                if screen.availableGeometry().intersects(window_rect)
            ),
            primary,
        )
        available = target.availableGeometry()
        max_x = max(available.left(), available.right() - size.width() + 1)
        max_y = max(available.top(), available.bottom() - size.height() + 1)
        return QPoint(
            min(max(position.x(), available.left()), max_x),
            min(max(position.y(), available.top()), max_y),
        )

    def _run_action(self, action: Callable[[], Any] | None, *, success: str = "") -> bool:
        if not callable(action):
            self._report_error("目前無法執行此操作")
            return False
        try:
            action()
        except Exception as error:
            self._report_error(str(error))
            return False
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.snapshotChanged.emit()
        if success:
            self.actionSucceeded.emit(success)
        return True

    def _report_error(self, message: str) -> None:
        text = message.strip() or "操作失敗"
        reporter = getattr(self._controller, "report_ui_error", None)
        if callable(reporter):
            reporter(text)
            self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
            self.snapshotChanged.emit()
        self.actionFailed.emit(text)

    def _report_edit_error(self, segment_id: str, message: str) -> None:
        text = message.strip() or "操作失敗"
        self._timeline_model.set_edit_error(segment_id, text)
        self._report_error(text)

    def _local_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=TAIPEI)
        return value.astimezone(TAIPEI)


__all__ = [
    "COMPACT_HEIGHT",
    "COMPACT_WIDTH",
    "EXPANDED_HEIGHT",
    "EXPANDED_MIN_HEIGHT",
    "EXPANDED_MIN_WIDTH",
    "EXPANDED_WIDTH",
    "FONT_DIRECTORY_ENV",
    "JournalViewModel",
    "LocalFontCatalog",
    "POLL_INTERVAL_MS",
    "TimelineListModel",
]
