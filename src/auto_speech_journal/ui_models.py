from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
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

from .config import (
    DEFAULT_UI_FONT_SIZE,
    MAX_UI_FONT_SIZE,
    MIN_UI_FONT_SIZE,
    AppConfig,
    MicrophoneMode,
    MicrophoneSelection,
)
from .paths import AppPaths
from .scene_assets import validate_runtime_scenes

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
SCENE_DIRECTORY_ENV = "AUTO_SPEECH_JOURNAL_SCENE_DIR"
SUPPORTED_FONT_SUFFIXES = frozenset({".ttf", ".otf"})
SCENE_STATE_KEYS = frozenset(
    {
        "starting",
        "listening",
        "capturing",
        "finalizing",
        "paused",
        "degraded",
        "error",
        "stopped",
    }
)
SCENE_MONTH_KEYS = frozenset(f"{value:02d}" for value in range(1, 13))
SCENE_VARIANTS = frozenset({"compact", "workspace"})

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
    "startup_enabled": "開機自動啟動",
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


def _packaged_variant_matrix_ready(directory: Path) -> bool:
    """Keep partial v2 migrations dormant; prototypes use the explicit env root."""
    manifest_path = directory / "manifest.json"
    try:
        manifest_stat = manifest_path.stat()
    except OSError:
        return False
    return _packaged_variant_matrix_ready_cached(
        directory.resolve(),
        manifest_stat.st_mtime_ns,
        manifest_stat.st_size,
        validate_runtime_scenes,
    )


@lru_cache(maxsize=8)
def _packaged_variant_matrix_ready_cached(
    directory: Path,
    _manifest_mtime_ns: int,
    _manifest_size: int,
    validator: Callable[..., list[str]],
) -> bool:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 2
        or manifest.get("asset_count") != 192
    ):
        return False
    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 192:
        return False
    keys: set[tuple[str, str, str]] = set()
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("status") != "ready":
            continue
        month, state, variant = (
            asset.get("month"),
            asset.get("state"),
            asset.get("variant"),
        )
        if (
            isinstance(month, str)
            and month in SCENE_MONTH_KEYS
            and isinstance(state, str)
            and state in SCENE_STATE_KEYS
            and isinstance(variant, str)
            and variant in SCENE_VARIANTS
        ):
            keys.add((month, state, variant))
    if len(keys) != 192:
        return False
    # The installer and release gate perform full image decoding. The UI repeats
    # the matrix, header and digest checks once per manifest revision so startup
    # never blocks on decoding 192 large backgrounds.
    return not validator(strict=True, root=directory, decode_images=False)


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
    TimeLabelRole = IsHourStartRole + 1
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
    controllerStartChanged = Signal()
    _microphoneTestCompleted = Signal(str, bool, str, float, float)
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
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._application = application
        self._settings = settings or QSettings("AutoSpeechJournal", "Desktop")
        self._clock = clock or (lambda: datetime.now(TAIPEI))
        self._monotonic = monotonic
        self._microphone_device_provider = microphone_device_provider
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
        self._controller_started = False
        self._microphoneTestCompleted.connect(self._finish_microphone_test)
        self._timeline_model = TimelineListModel(self)
        self._poll_timer = QTimer(self)
        self._poll_timer.setObjectName("journalPollTimer")
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)

    def attach_window(self, window: Any) -> None:
        self._window = window
        self._apply_window_mode(initial=True)

    def activate(self) -> None:
        if self.microphoneSetupPending:
            self.rescanMicrophones()
        self.refresh(force_timeline=True)
        self._poll_timer.start()

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
        if bool(getattr(snapshot, "paused", False)):
            return "錄音已暫停"
        state = _enum_value(getattr(snapshot, "state", "stopped"))
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

    @Property(str, notify=snapshotChanged)
    def statusTone(self) -> str:
        if self.paused:
            return "#8B8377"
        severity = _enum_value(getattr(self._snapshot, "severity", "info"))
        if severity == "error":
            return "#B85C4A"
        if severity == "warning":
            return "#B88647"
        return "#718C78"

    @Property(str, notify=sceneChanged)
    def sceneKey(self) -> str:
        return self._scene_key

    @Property(QUrl, notify=sceneChanged)
    def compactSceneSource(self) -> QUrl:
        return self._scene_source("compact")

    @Property(QUrl, notify=sceneChanged)
    def workspaceSceneSource(self) -> QUrl:
        return self._scene_source("workspace")

    @Property(QUrl, notify=sceneChanged)
    def sceneSource(self) -> QUrl:
        """Legacy alias retained for compact clients built against the v1 UI."""
        return self.compactSceneSource

    def _scene_source(self, variant: str) -> QUrl:
        stem = f"{self._day.month:02d}-{self._scene_key}"
        filename = f"{stem}-{variant}.webp"
        month_directory = f"month-{self._day.month:02d}"
        package_root = Path(__file__).resolve().parent
        packaged_scene_root = package_root / "assets" / "scenes"

        candidates: list[Path] = []
        prototype_root = os.environ.get(SCENE_DIRECTORY_ENV, "").strip()
        if prototype_root:
            root = Path(prototype_root).expanduser().resolve(strict=False)
            candidates.extend(
                (
                    root / filename,
                    root / variant / f"{stem}.webp",
                    root / month_directory / filename,
                    root / month_directory / variant / f"{stem}.webp",
                )
            )

        if _packaged_variant_matrix_ready(packaged_scene_root):
            candidates.extend(
                (
                    packaged_scene_root / filename,
                    packaged_scene_root / variant / f"{stem}.webp",
                )
            )
        # Keep legacy paths as a last-resort fallback for incomplete development
        # checkouts; production packages use the validated v2 candidates above.
        candidates.extend(
            (
                packaged_scene_root / f"{stem}.webp",
                package_root / "assets" / f"{stem}.webp",
            )
        )
        for candidate in candidates:
            if candidate.is_file():
                return QUrl.fromLocalFile(str(candidate))
        return QUrl()

    @Property(str, notify=dateChanged)
    def dateLabel(self) -> str:
        weekday = WEEKDAYS[self._day.weekday()]
        return f"{self._day.year} 年 {self._day.month} 月 {self._day.day} 日 · {weekday}"

    @Property(str, notify=dateChanged)
    def monthLabel(self) -> str:
        return f"{self._day.month} 月聲景"

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

    @Property(str, notify=settingsChanged)
    def deviceName(self) -> str:
        return self.preferredInputName

    @Property(bool, notify=microphoneSetupChanged)
    def microphoneSetupPending(self) -> bool:
        selection = getattr(
            getattr(self._controller, "config", None),
            "microphone",
            None,
        )
        return (
            _enum_value(getattr(selection, "mode", ""))
            == MicrophoneMode.PENDING.value
        )

    @Property(bool, notify=controllerStartChanged)
    def recordingEngineNeedsStart(self) -> bool:
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

    @Property("QVariantList", notify=microphoneDevicesChanged)
    def microphoneOptions(self) -> list[dict[str, Any]]:
        return [dict(option) for option in self._microphone_options]

    @Property(bool, notify=microphoneDevicesChanged)
    def microphoneHasAvailableDevices(self) -> bool:
        return self._microphone_has_selectable_route

    @Property(str, notify=microphoneDevicesChanged)
    def microphoneScanError(self) -> str:
        return self._microphone_scan_error

    @Property(str, notify=microphoneSelectionChanged)
    def selectedMicrophoneKey(self) -> str:
        return self._selected_microphone_key

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
        if self.microphoneSetupPending:
            return self.completeMicrophoneSetup()
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
    def completeMicrophoneSetup(self) -> bool:
        selection = self._selection_for_key(self._selected_microphone_key)
        if selection is None:
            self.actionFailed.emit("請先選擇一個麥克風或跟隨 Windows 預設")
            return False
        if not self._configure_microphone(selection):
            return False
        self._after_microphone_configuration()
        QTimer.singleShot(0, self.startControllerIfReady)
        return True

    @Slot(result=bool)
    def skipMicrophoneSetup(self) -> bool:
        if self._microphone_has_selectable_route:
            self.actionFailed.emit("已有可用麥克風，請先選擇後再開始")
            return False
        action = getattr(self._controller, "skip_microphone_setup", None)
        if callable(action):
            if not self._run_action(action, success="已略過麥克風設定，可稍後在設定頁補上"):
                return False
        elif not self._configure_microphone(
            MicrophoneSelection(mode=MicrophoneMode.SKIPPED)
        ):
            return False
        self._after_microphone_configuration()
        QTimer.singleShot(0, self.startControllerIfReady)
        return True

    @Slot(result=bool)
    def deferMicrophoneAfterStartFailure(self) -> bool:
        if not self.recordingEngineNeedsStart or self.microphoneSetupPending:
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
        if self.microphoneSetupPending or self._controller_started:
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
        self._microphone_test_message = "麥克風測試逾時，已停止等待；請重新掃描後再試。"
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
        if not ok:
            self._microphone_test_state = "error"
            self._microphone_test_message = f"麥克風測試失敗：{error}"
            self.actionFailed.emit(self._microphone_test_message)
        elif rms < 0.0001:
            self._microphone_test_state = "warning"
            self._microphone_test_message = "連線成功，但幾乎沒有收到聲音，請確認音量與權限。"
        else:
            self._microphone_test_state = "success"
            self._microphone_test_message = f"測試成功，RMS {rms:.6f}、peak {peak:.6f}"
            self.actionSucceeded.emit("麥克風測試成功")
        self.microphoneTestChanged.emit()

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

    @Slot()
    def minimize(self) -> None:
        if self._window is not None:
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
    def applySettings(
        self,
        records_root: str,
        preview_interval_ms: int,
        endpoint_silence_ms: int,
        max_segment_ms: int,
        microphone_key: str = "",
    ) -> bool:
        base = getattr(self._controller, "config", None)
        if base is None:
            self._report_error("找不到目前設定")
            return False
        requested_key = microphone_key.strip()
        current_selection = getattr(base, "microphone", None)
        current_key = _microphone_key_for_selection(current_selection)
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
        if not self._run_action(lambda: action(config)):
            self.controllerStartChanged.emit()
            return False
        if requested_key:
            self._selected_microphone_key = requested_key
            self.microphoneSelectionChanged.emit()
        self._snapshot = getattr(self._controller, "snapshot", self._snapshot)
        self.settingsChanged.emit()
        self.snapshotChanged.emit()
        self._refresh_settings_history()
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

    @Slot(str)
    def openPath(self, path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def shutdown(self) -> None:
        self._microphone_test_request_id = ""
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
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
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
            for index, segment in enumerate(tuple(getattr(hour, "segments", ()) or ())):
                rows.append(
                    _TimelineRow(
                        segment_id=str(getattr(segment, "segment_id", "") or ""),
                        hour_key=str(getattr(segment, "hour_key", hour_key) or hour_key),
                        hour_label=hour_label or JournalViewModel._hour_label(hour_key),
                        is_hour_start=index == 0,
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
