from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig, MicrophoneMode, MicrophoneSelection
from .settings_history import SettingsHistoryEntry
from .timeline import DayTimelineView, build_day_timeline
from .types import (
    AudioLevelUpdate,
    CapturedSegment,
    FinalResult,
    InputRoute,
    InputRouteUpdate,
    PartialUpdate,
    SegmentState,
    Severity,
    WorkerKind,
    WorkerState,
    WorkerStatus,
)

LOGGER = logging.getLogger("auto_speech_journal.controller")
JournalEvent = (
    AudioLevelUpdate
    | PartialUpdate
    | CapturedSegment
    | FinalResult
    | WorkerStatus
    | InputRouteUpdate
)

AUDIO_LEVEL_FLOOR_DBFS = -120.0
AUDIO_LEVEL_STALE_SECONDS = 0.5
INACTIVE_RECORDER_STATES = frozenset(
    {
        WorkerState.STARTING,
        WorkerState.READY,
        WorkerState.PAUSED,
        WorkerState.ERROR,
        WorkerState.STOPPED,
    }
)


class StoragePort(Protocol):
    def add_captured(self, segment: CapturedSegment) -> Any: ...

    def apply_final(self, result: FinalResult) -> Any: ...

    def get_segment(self, segment_id: str) -> Any: ...

    def correct_segment(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn_vocabulary: bool = True,
    ) -> Any: ...

    def mark_finalizing(self, segment_id: str) -> Any: ...

    def count_pending(self) -> int: ...

    def list_segments(
        self,
        *,
        states: Sequence[SegmentState] | None = None,
        limit: int | None = None,
    ) -> Sequence[Any]: ...

    def list_hours(self) -> Sequence[str]: ...

    def list_day_segments(self, day_key: str) -> Sequence[Any]: ...


class ExporterPort(Protocol):
    def set_records_root(self, records_root: Path) -> None: ...

    def export_segment(self, segment_id: str) -> Any: ...

    def rebuild_hour(self, hour_key: str) -> Any: ...

    def delete_hour(self, hour_key: str) -> Any: ...

    def export_due_provisionals(self, *, deadline: timedelta) -> Sequence[Any]: ...

    def retry_pending_deletions(self) -> Any: ...


class VocabularyPort(Protocol):
    def apply_correction(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn: bool = True,
    ) -> Any: ...

    def hotwords(self, *, minimum_count: int = 1) -> Sequence[str]: ...

    def term_counts(self) -> Mapping[str, int]: ...

    def delete_term(self, term: str) -> bool: ...

    def clear(self) -> int: ...


class WorkersPort(Protocol):
    def start(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def reconfigure_input(
        self,
        selection: MicrophoneSelection,
        *,
        request_id: str | None = None,
    ) -> str: ...

    def retry_preferred_input(self, *, request_id: str | None = None) -> str: ...

    def submit(self, segment: CapturedSegment) -> bool: ...

    def update_hotwords(self, hotwords: list[str]) -> None: ...

    def stop(self) -> None: ...

    def stop_recorder(self, timeout: float = 10.0) -> None: ...

    @property
    def pending_finalizations(self) -> int: ...

    def stop_finalizer(self, timeout: float = 10.0) -> None: ...

    def poll_events(self) -> list[JournalEvent]: ...


class SettingsHistoryPort(Protocol):
    path: Path

    def append_change(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> SettingsHistoryEntry | None: ...

    def read_recent(self, limit: int = 5) -> Sequence[SettingsHistoryEntry]: ...

    def ensure_file(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    state: WorkerState = WorkerState.STOPPED
    severity: Severity = Severity.INFO
    message: str = "尚未啟動"
    paused: bool = False
    partial_text: str = ""
    final_text: str = ""
    current_segment_id: str | None = None
    correctable_segment_id: str | None = None
    correctable_text: str = ""
    current_hour_key: str | None = None
    backlog: int = 0
    last_error: str | None = None
    rms_dbfs: float = AUDIO_LEVEL_FLOOR_DBFS
    peak_dbfs: float = AUDIO_LEVEL_FLOOR_DBFS
    speech_active: bool = False
    audio_segment_id: str | None = None
    last_audio_at_utc: datetime | None = None
    timeline_revision: int = 0
    preferred_input_name: str | None = None
    active_input_name: str | None = None
    input_route: InputRoute = InputRoute.PENDING
    input_switching: bool = False
    preferred_input_available: bool = False
    input_route_reason: str | None = None


class JournalController:
    """Coordinates workers, durable storage and user actions without depending on Qt."""

    def __init__(
        self,
        *,
        storage: StoragePort,
        exporter: ExporterPort,
        workers: WorkersPort | None,
        config: AppConfig,
        workers_factory: Callable[[AppConfig], WorkersPort] | None = None,
        vocabulary: VocabularyPort | None = None,
        save_config_callback: Callable[[AppConfig], None] | None = None,
        settings_history_store: SettingsHistoryPort | None = None,
        folder_opener: Callable[[Path], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.storage = storage
        self.exporter = exporter
        self.workers = workers
        self._workers_factory = workers_factory
        self.vocabulary = vocabulary
        self._config = config
        self._active_records_root = Path(config.records_root).expanduser().resolve()
        self._save_config_callback = save_config_callback
        self._settings_history_store = settings_history_store
        self._folder_opener = folder_opener or _open_folder
        self._monotonic = monotonic
        self._sleep = sleep
        preferred = config.microphone.preferred_device
        self._snapshot = ControllerSnapshot(
            backlog=self._pending_count(0),
            preferred_input_name=preferred.name if preferred is not None else None,
            input_route=_selection_input_route(config.microphone),
        )
        self._listeners: list[Callable[[ControllerSnapshot], None]] = []
        self._lock = threading.RLock()
        self._started = False
        self._workers_started = False
        self._closed = False
        self._last_maintenance = float("-inf")
        self._submitted_ids: set[str] = set()
        self._retry_after: dict[str, float] = {}
        self._deleted_segment_ids: set[str] = set()
        self._export_retry_hours: set[str] = set()
        self._worker_statuses: dict[WorkerKind, WorkerStatus] = {}
        self._pause_requested = False
        self._provisional_export_failed = False
        self._cleanup_degraded = False
        self._last_submission_error: str | None = None
        self._last_audio_monotonic: float | None = None

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def workers_started(self) -> bool:
        """Whether the recorder process group is available for live actions."""

        return self._workers_started

    @property
    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            return self._snapshot

    def bind_workers(self, workers: WorkersPort) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("cannot replace workers while running")
            self.workers = workers

    def subscribe(
        self, listener: Callable[[ControllerSnapshot], None]
    ) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)
            current = self._snapshot
        listener(current)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def start(self) -> None:
        selection = self._config.microphone
        if not self._config.onboarding_completed:
            self._started = False
            self._closed = False
            self._update(
                state=WorkerState.READY,
                severity=Severity.WARNING,
                message="尚未同意開始錄音；請先完成首次設定",
                input_route=_selection_input_route(selection),
                input_switching=False,
                input_route_reason="等待首次設定確認",
                **self._silent_audio_changes(),
            )
            return
        if self._started and (
            self._workers_started
            or selection.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}
        ):
            return
        if selection.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}:
            self._started = True
            self._closed = False
            message = (
                "請先選擇麥克風"
                if selection.mode == MicrophoneMode.PENDING
                else "尚未設定麥克風；可稍後從設定選擇"
            )
            self._update(
                state=WorkerState.READY,
                severity=Severity.WARNING,
                message=message,
                input_route=_selection_input_route(selection),
                input_switching=False,
                input_route_reason=message,
                **self._silent_audio_changes(),
            )
            return
        workers = self._ensure_workers()
        self._update(state=WorkerState.STARTING, message="正在啟動錄音與轉錄引擎")
        try:
            workers.start()
        except Exception as error:
            self._record_error("啟動失敗", error)
            raise
        self._started = True
        self._workers_started = True
        self._closed = False
        self._refresh_hotwords()
        self._retry_pending(self._monotonic())

    def pause(self) -> None:
        workers = self._require_workers()
        workers.pause()
        self._pause_requested = True
        self._update(
            state=WorkerState.PAUSED,
            paused=True,
            message="已暫停新錄音；已入佇列的片段仍會完成轉錄",
            **self._silent_audio_changes(),
        )

    def resume(self) -> None:
        workers = self._require_workers()
        workers.resume()
        self._pause_requested = False
        self._update(state=WorkerState.RECORDING, paused=False, message="正在錄音")

    def toggle_pause(self) -> None:
        if self.snapshot.paused:
            self.resume()
        else:
            self.pause()

    def stop(self, *, suppress_errors: bool = False) -> bool:
        if self._closed:
            return True
        try:
            if self.workers is not None and self._workers_started:
                if self._supports_staged_stop(self.workers):
                    self._staged_stop(self.workers)
                else:
                    self.workers.stop()
        except Exception as error:
            self._record_error("停止 worker 失敗", error)
            if not suppress_errors:
                raise
            return False
        self._started = False
        self._workers_started = False
        self._closed = True
        self._update(
            state=WorkerState.STOPPED,
            paused=False,
            message="已停止",
            **self._silent_audio_changes(),
        )
        return True

    @staticmethod
    def _supports_staged_stop(workers: WorkersPort) -> bool:
        return bool(
            callable(getattr(workers, "stop_recorder", None))
            and hasattr(workers, "pending_finalizations")
            and callable(getattr(workers, "stop_finalizer", None))
        )

    def _staged_stop(self, workers: WorkersPort) -> None:
        timeout = max(0.1, self._config.final_deadline_ms / 1_000)
        workers.stop_recorder(timeout=timeout)
        deadline = self._monotonic() + timeout
        pending = 0
        while True:
            self.poll_workers()
            pending = max(0, int(workers.pending_finalizations))
            if pending == 0:
                break
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(0.05, remaining))
        if pending:
            LOGGER.warning(
                "Stopping with %d durable finalizations left for next startup",
                pending,
            )
        workers.stop_finalizer(timeout=timeout)
        self.poll_workers()

    def poll_workers(self) -> int:
        """Drain worker events without blocking; safe to call from a UI timer."""
        if self.workers is None or not self._workers_started:
            return 0
        try:
            events: Sequence[JournalEvent] = self.workers.poll_events()
        except Exception as error:
            self._record_error("讀取 worker 事件失敗", error)
            return 0
        for event in events:
            self.handle_event(event)
        return len(events)

    def tick(self) -> int:
        """Drain events, then publish provisional text whose final deadline expired."""
        event_count = self.poll_workers()
        now = self._monotonic()
        self._expire_stale_audio(now)
        if now - self._last_maintenance < 1.0:
            return event_count
        self._last_maintenance = now
        try:
            self.exporter.export_due_provisionals(
                deadline=timedelta(milliseconds=self._config.final_deadline_ms)
            )
        except Exception as error:
            self._provisional_export_failed = True
            message = f"匯出逾時預覽文字失敗，將自動重試：{error}"
            LOGGER.exception("Exporting overdue provisional text failed")
            self._update(
                state=WorkerState.DEGRADED,
                severity=Severity.ERROR,
                message=message,
                last_error=message,
            )
        else:
            if self._provisional_export_failed:
                self._provisional_export_failed = False
                severity = self._aggregate_worker_severity()
                self._update(
                    state=self._aggregate_worker_state(),
                    severity=severity,
                    message="已恢復每小時紀錄寫入",
                    last_error=(
                        self.snapshot.last_error if severity == Severity.ERROR else None
                    ),
                )
        self._retry_exports()
        self._retry_pending_deletions()
        self._retry_pending(now)
        return event_count

    def handle_event(self, event: JournalEvent) -> None:
        if isinstance(event, AudioLevelUpdate):
            self._handle_audio_level(event)
        elif isinstance(event, PartialUpdate):
            self._handle_partial(event)
        elif isinstance(event, CapturedSegment):
            self._handle_captured(event)
        elif isinstance(event, FinalResult):
            self._handle_final(event)
        elif isinstance(event, InputRouteUpdate):
            self._handle_input_route(event)
        elif isinstance(event, WorkerStatus):
            self._handle_status(event)
        else:
            raise TypeError(f"unsupported worker event: {type(event)!r}")

    def correct_segment(self, segment_id: str, corrected_text: str) -> Any:
        corrected = corrected_text.strip()
        if not corrected:
            raise ValueError("修正文字不可為空")
        learn = self._config.vocabulary_learning_enabled
        if self.vocabulary is None:
            updated = self.storage.correct_segment(
                segment_id,
                corrected,
                learn_vocabulary=learn,
            )
        else:
            correction = self.vocabulary.apply_correction(
                segment_id,
                corrected,
                learn=learn,
            )
            updated = getattr(correction, "segment", None)
            if updated is None:
                updated = self.storage.get_segment(segment_id)
            self._refresh_hotwords()
        hour_key = getattr(updated, "hour_key", None)
        self._update(
            timeline_changed=True,
            final_text=str(getattr(updated, "display_text", corrected) or corrected),
            correctable_segment_id=segment_id,
            correctable_text=str(getattr(updated, "display_text", corrected) or corrected),
            current_hour_key=hour_key or self.snapshot.current_hour_key,
            message="已儲存修正",
            severity=Severity.INFO,
        )
        if hour_key:
            try:
                self.exporter.rebuild_hour(hour_key)
            except Exception as error:
                self._export_retry_hours.add(hour_key)
                self._record_error("重建修正後的小時紀錄失敗", error)
                raise
        return updated

    def correct_current(self, corrected_text: str) -> Any:
        segment_id = self.snapshot.correctable_segment_id
        if not segment_id:
            raise LookupError("目前沒有可修正的片段")
        return self.correct_segment(segment_id, corrected_text)

    def available_hours(self) -> list[str]:
        return list(self.storage.list_hours())

    def learned_vocabulary(self) -> dict[str, int]:
        if self.vocabulary is None:
            return {}
        return dict(self.vocabulary.term_counts())

    def delete_vocabulary_term(self, term: str) -> bool:
        if self.vocabulary is None:
            raise RuntimeError("校正字典尚未啟用")
        normalized = term.strip()
        if not normalized:
            raise ValueError("詞語不可為空")
        deleted = self.vocabulary.delete_term(normalized)
        if deleted:
            self._refresh_hotwords()
            self._update(message=f"已從校正字典刪除「{normalized}」")
        else:
            self._update(message=f"校正字典中已沒有「{normalized}」")
        return deleted

    def clear_vocabulary(self) -> int:
        if self.vocabulary is None:
            raise RuntimeError("校正字典尚未啟用")
        count = self.vocabulary.clear()
        self._refresh_hotwords()
        self._update(message=f"已清空校正字典（{count} 詞）")
        return count

    def set_vocabulary_learning_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        if enabled == self._config.vocabulary_learning_enabled:
            self._update(message="校正字典自動學習設定沒有變更")
            return
        self.update_settings(
            replace(self._config, vocabulary_learning_enabled=enabled)
        )
        state = "啟用" if enabled else "停用"
        self._update(message=f"已{state}校正字典自動學習")

    def timeline_for_date(self, day: date) -> DayTimelineView:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise TypeError("day must be a datetime.date")
        day_key = day.isoformat()
        list_day_segments = getattr(self.storage, "list_day_segments", None)
        if callable(list_day_segments):
            records = list_day_segments(day_key)
        else:
            records = [
                record
                for record in self.storage.list_segments()
                if str(getattr(record, "hour_key", "")).startswith(f"{day_key}_")
            ]
        timezone_name = str(getattr(self.storage, "timezone_name", "Asia/Taipei"))
        return build_day_timeline(
            day_key,
            records,
            timezone_name=timezone_name,
        )

    def delete_hour(self, hour_key: str | None = None) -> Any:
        key = hour_key or self.snapshot.current_hour_key
        if not key:
            raise LookupError("目前沒有可刪除的小時紀錄")
        result = self.exporter.delete_hour(key)
        self._export_retry_hours.discard(key)
        self._refresh_hotwords()
        deleted = getattr(result, "deleted", None)
        deleted_ids = set(getattr(deleted, "segment_ids", ()))
        self._deleted_segment_ids.update(deleted_ids)
        for segment_id in deleted_ids:
            self._submitted_ids.discard(segment_id)
            self._retry_after.pop(segment_id, None)
        cleanup_failures = tuple(
            getattr(
                result,
                "cleanup_failures",
                getattr(result, "audio_cleanup_failures", ()),
            )
        )
        if cleanup_failures:
            self._cleanup_degraded = True
            message = (
                f"已刪除 {key} 的資料庫內容，但 {len(cleanup_failures)} 個檔案仍被占用；"
                "將持續重試清理"
            )
        else:
            message = f"已刪除 {key} 的記錄"
        changes: dict[str, Any] = {
            "backlog": self._pending_count(self.snapshot.backlog),
            "message": message,
            "severity": Severity.ERROR if cleanup_failures else Severity.INFO,
            "last_error": message if cleanup_failures else None,
        }
        if cleanup_failures:
            changes["state"] = (
                WorkerState.ERROR
                if self._aggregate_worker_state() == WorkerState.ERROR
                else WorkerState.DEGRADED
            )
        if key == self.snapshot.current_hour_key:
            changes.update(
                final_text="",
                partial_text="",
                current_segment_id=None,
                correctable_segment_id=None,
                correctable_text="",
                current_hour_key=None,
            )
        elif self.snapshot.correctable_segment_id in deleted_ids:
            changes.update(
                final_text="",
                correctable_segment_id=None,
                correctable_text="",
            )
        self._update(timeline_changed=bool(deleted_ids), **changes)
        return result

    def open_records_folder(self) -> None:
        path = self._active_records_root
        path.mkdir(parents=True, exist_ok=True)
        self._folder_opener(path)

    def report_ui_error(self, message: str) -> None:
        """Surface a UI action failure in the main window without notifications."""

        text = message.strip() or "操作失敗"
        LOGGER.error("UI action failed: %s", text)
        self._update(
            state=WorkerState.DEGRADED,
            severity=Severity.ERROR,
            message=text,
            last_error=text,
        )

    def configure_microphone(self, selection: MicrophoneSelection) -> str | None:
        selection.validate()
        if selection.mode not in {
            MicrophoneMode.SYSTEM_DEFAULT,
            MicrophoneMode.FIXED,
        }:
            raise ValueError("configure_microphone requires system_default or fixed mode")
        return self.update_settings(replace(self._config, microphone=selection))

    def skip_microphone_setup(self) -> str | None:
        return self.update_settings(
            replace(
                self._config,
                microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
            )
        )

    def retry_preferred_input(self) -> str:
        if self._config.microphone.mode != MicrophoneMode.FIXED:
            raise ValueError("no fixed preferred microphone is configured")
        if not self._workers_started:
            raise RuntimeError("recorder is not running")
        workers = self._require_workers()
        self._update(
            input_switching=True,
            message="正在切回偏好麥克風",
        )
        try:
            return workers.retry_preferred_input()
        except Exception as error:
            self._update(input_switching=False)
            self._record_error("切回偏好麥克風失敗", error)
            raise

    def update_settings(self, config: AppConfig) -> str | None:
        raw_root = config.records_root.strip()
        if not raw_root:
            raise ValueError("紀錄資料夾不可為空")
        records_root = Path(raw_root).expanduser()
        if not records_root.is_absolute():
            raise ValueError("紀錄資料夾必須是絕對路徑")
        records_root = records_root.resolve()
        raw = config.to_dict()
        raw["records_root"] = str(records_root)
        validated = AppConfig.from_dict(raw)
        previous_config = self._config
        before = previous_config.to_dict()
        after = validated.to_dict()
        if before == after:
            if (
                not self._closed
                and not self._workers_started
                and validated.onboarding_completed
                and validated.microphone.mode
                in {MicrophoneMode.SYSTEM_DEFAULT, MicrophoneMode.FIXED}
            ):
                self._update(message="設定沒有變更；正在重試啟動錄音")
                self.start()
                return None
            self._update(message="設定沒有變更")
            return None
        if self._save_config_callback is None:
            raise RuntimeError("save_config callback is not configured")
        records_root.mkdir(parents=True, exist_ok=True)
        root_changed = records_root != self._active_records_root
        switch_root_now = root_changed and not self._workers_started
        previous_microphone = previous_config.microphone
        microphone_changed = validated.microphone != previous_microphone
        previous_snapshot = self.snapshot
        previous_workers = self.workers
        previous_started = self._started
        previous_workers_started = self._workers_started
        previous_closed = self._closed
        previous_pause_requested = self._pause_requested
        persisted = False
        root_switched = False
        try:
            self._save_config_callback(validated)
            persisted = True
            self._config = validated
            if switch_root_now:
                self._set_exporter_records_root(records_root)
                self._active_records_root = records_root
                root_switched = True
            request_id = (
                self._activate_microphone_selection(
                    validated.microphone,
                    previous=previous_microphone,
                )
                if microphone_changed
                else None
            )
        except Exception as error:
            rollback_errors = self._rollback_settings_transaction(
                previous_config=previous_config,
                previous_snapshot=previous_snapshot,
                previous_workers=previous_workers,
                previous_started=previous_started,
                previous_workers_started=previous_workers_started,
                previous_closed=previous_closed,
                previous_pause_requested=previous_pause_requested,
                microphone_changed=microphone_changed,
                root_switched=root_switched,
                persisted=persisted,
            )
            if rollback_errors:
                detail = "; ".join(str(item) for item in rollback_errors)
                raise RuntimeError(f"設定套用失敗，且回復未完整完成：{detail}") from error
            raise
        history_failed = False
        if self._settings_history_store is not None:
            try:
                self._settings_history_store.append_change(before, after)
            except Exception:
                history_failed = True
                LOGGER.exception("Settings saved, but writing settings history failed")
        suffix = "；設定歷程寫入失敗" if history_failed else ""
        self._update(
            message=(
                "設定已儲存；正在切換麥克風" + suffix
                if microphone_changed and self.snapshot.input_switching
                else "設定已儲存；麥克風會在開始錄音時套用" + suffix
                if microphone_changed
                else "設定已儲存；紀錄資料夾已切換" + suffix
                if root_switched
                else "設定已儲存；紀錄資料夾會在下次啟動切換，目前仍使用原資料夾" + suffix
                if root_changed
                else "設定已儲存；音訊或模型變更會在下次啟動生效" + suffix
            )
        )
        return request_id

    def _set_exporter_records_root(self, records_root: Path) -> None:
        setter = getattr(self.exporter, "set_records_root", None)
        if not callable(setter):
            raise RuntimeError("exporter does not support changing records_root")
        setter(records_root)

    def _rollback_settings_transaction(
        self,
        *,
        previous_config: AppConfig,
        previous_snapshot: ControllerSnapshot,
        previous_workers: WorkersPort | None,
        previous_started: bool,
        previous_workers_started: bool,
        previous_closed: bool,
        previous_pause_requested: bool,
        microphone_changed: bool,
        root_switched: bool,
        persisted: bool,
    ) -> list[Exception]:
        errors: list[Exception] = []
        current_workers = self.workers
        if microphone_changed and current_workers is not None:
            try:
                if not previous_workers_started:
                    current_workers.stop()
                elif previous_config.microphone.mode in {
                    MicrophoneMode.SYSTEM_DEFAULT,
                    MicrophoneMode.FIXED,
                }:
                    reconfigure = getattr(current_workers, "reconfigure_input", None)
                    if callable(reconfigure):
                        reconfigure(previous_config.microphone)
            except Exception as rollback_error:
                LOGGER.exception("Unable to restore microphone after settings failure")
                errors.append(rollback_error)
        self.workers = previous_workers
        self._started = previous_started
        self._workers_started = previous_workers_started
        self._closed = previous_closed
        self._pause_requested = previous_pause_requested
        self._config = previous_config
        if root_switched:
            try:
                previous_root = Path(previous_config.records_root).expanduser().resolve()
                self._set_exporter_records_root(previous_root)
                self._active_records_root = previous_root
            except Exception as rollback_error:
                LOGGER.exception("Unable to restore records_root after settings failure")
                errors.append(rollback_error)
        self._restore_snapshot(previous_snapshot)
        if persisted and self._save_config_callback is not None:
            try:
                self._save_config_callback(previous_config)
            except Exception as rollback_error:
                LOGGER.exception("Unable to restore persisted config after settings failure")
                errors.append(rollback_error)
        return errors

    def _restore_snapshot(self, snapshot: ControllerSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                LOGGER.exception("Controller listener failed")

    def _activate_microphone_selection(
        self,
        selection: MicrophoneSelection,
        *,
        previous: MicrophoneSelection,
    ) -> str | None:
        preferred = selection.preferred_device
        route = _selection_input_route(selection)
        if selection.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}:
            if self._workers_started and self.workers is not None:
                self.workers.pause()
                self._pause_requested = True
            message = (
                "請先選擇麥克風"
                if selection.mode == MicrophoneMode.PENDING
                else "尚未設定麥克風；可稍後從設定選擇"
            )
            self._update(
                state=WorkerState.READY,
                severity=Severity.WARNING,
                message=message,
                paused=self._pause_requested,
                preferred_input_name=None,
                active_input_name=None,
                input_route=route,
                input_switching=False,
                preferred_input_available=False,
                input_route_reason=message,
                **self._silent_audio_changes(),
            )
            return None

        self._update(
            state=WorkerState.STARTING if self._started else self.snapshot.state,
            preferred_input_name=preferred.name if preferred is not None else None,
            input_route=route,
            input_switching=self._started,
            preferred_input_available=False,
            input_route_reason=None,
            message="正在切換麥克風" if self._started else "麥克風設定已儲存",
        )

        if not self._started:
            return None

        if not self._workers_started:
            workers = self._ensure_workers()
            try:
                workers.start()
            except Exception as error:
                self._update(input_switching=False)
                self._record_error("啟動錄音失敗", error)
                raise
            self._workers_started = True
            self._closed = False
            self._refresh_hotwords()
            self._retry_pending(self._monotonic())
            return None

        workers = self._require_workers()
        try:
            request_id = workers.reconfigure_input(selection)
            if previous.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}:
                workers.resume()
                self._pause_requested = False
                self._update(paused=False)
            return request_id
        except Exception as error:
            self._update(input_switching=False)
            self._record_error("切換麥克風失敗", error)
            raise

    def recent_settings_history(
        self,
        limit: int = 5,
    ) -> Sequence[SettingsHistoryEntry]:
        if self._settings_history_store is None:
            return ()
        return self._settings_history_store.read_recent(limit)

    def open_settings_history_file(self) -> None:
        if self._settings_history_store is None:
            raise RuntimeError("settings history store is not configured")
        self._folder_opener(self._settings_history_store.ensure_file())

    def _handle_partial(self, update: PartialUpdate) -> None:
        snapshot = self.snapshot
        changes: dict[str, Any] = {
            "partial_text": update.text,
            "current_segment_id": update.segment_id,
        }
        if (
            snapshot.severity == Severity.INFO
            and snapshot.last_error is None
            and snapshot.state
            not in {
                WorkerState.PAUSED,
                WorkerState.DEGRADED,
                WorkerState.ERROR,
                WorkerState.STOPPED,
            }
        ):
            changes["message"] = "正在錄音與預覽辨識"
        self._update(**changes)

    def _handle_audio_level(self, update: AudioLevelUpdate) -> None:
        if not self._recorder_accepts_audio_levels():
            return
        self._last_audio_monotonic = self._monotonic()
        snapshot = self.snapshot
        new_segment = bool(
            update.segment_id
            and update.segment_id != snapshot.audio_segment_id
            and update.segment_id != snapshot.current_segment_id
        )
        self._update(
            rms_dbfs=update.rms_dbfs,
            peak_dbfs=update.peak_dbfs,
            speech_active=update.speech_active,
            audio_segment_id=update.segment_id,
            last_audio_at_utc=update.measured_at_utc,
            partial_text="" if new_segment else snapshot.partial_text,
        )

    def _recorder_accepts_audio_levels(self) -> bool:
        if self._pause_requested:
            return False
        recorder = self._worker_statuses.get(WorkerKind.RECORDER)
        return recorder is None or recorder.state not in INACTIVE_RECORDER_STATES

    def _expire_stale_audio(self, now: float) -> None:
        last_audio = self._last_audio_monotonic
        if last_audio is None or now - last_audio < AUDIO_LEVEL_STALE_SECONDS:
            return
        self._last_audio_monotonic = None
        snapshot = self.snapshot
        if (
            snapshot.rms_dbfs != AUDIO_LEVEL_FLOOR_DBFS
            or snapshot.peak_dbfs != AUDIO_LEVEL_FLOOR_DBFS
            or snapshot.speech_active
        ):
            self._update(**self._silent_audio_changes(reset_clock=False))

    def _silent_audio_changes(self, *, reset_clock: bool = True) -> dict[str, Any]:
        if reset_clock:
            self._last_audio_monotonic = None
        return {
            "rms_dbfs": AUDIO_LEVEL_FLOOR_DBFS,
            "peak_dbfs": AUDIO_LEVEL_FLOOR_DBFS,
            "speech_active": False,
        }

    def _handle_captured(self, segment: CapturedSegment) -> None:
        try:
            record = self.storage.add_captured(segment)
        except Exception as error:
            self._record_error("儲存錄音片段失敗", error)
            return
        submitted = self._submit_segment(segment)
        changes: dict[str, Any] = {
            "current_segment_id": segment.segment_id,
            "correctable_segment_id": segment.segment_id,
            "correctable_text": str(
                getattr(record, "display_text", "") or segment.preview_text
            ),
            "current_hour_key": getattr(record, "hour_key", None)
            or self.snapshot.current_hour_key,
            "partial_text": segment.preview_text or self.snapshot.partial_text,
            "backlog": self._pending_count(self.snapshot.backlog),
            "message": (
                "片段已安全儲存，等待最終轉錄"
                if submitted
                else self._last_submission_error
                or "最終轉錄佇列已滿；片段已保存並會自動重試"
            ),
        }
        if not submitted:
            changes.update(
                state=WorkerState.DEGRADED,
                severity=(
                    Severity.ERROR
                    if self._last_submission_error is not None
                    else Severity.WARNING
                ),
                last_error=self._last_submission_error,
            )
        self._update(timeline_changed=True, **changes)

    def _handle_final(self, result: FinalResult) -> None:
        self._submitted_ids.discard(result.segment_id)
        if result.segment_id in self._deleted_segment_ids:
            return
        try:
            record = self.storage.apply_final(result)
            if record is None:
                record = self.storage.get_segment(result.segment_id)
        except Exception as error:
            self._retry_after[result.segment_id] = self._monotonic() + 5.0
            self._record_error("儲存最終轉錄失敗", error)
            return

        export_error: Exception | None = None
        cleanup_failures: tuple[Path, ...] = ()
        if result.success:
            try:
                export_result = self.exporter.export_segment(result.segment_id)
                cleanup_failures = tuple(
                    getattr(export_result, "audio_cleanup_failures", ())
                )
            except Exception as error:
                export_error = error
                hour_key = getattr(record, "hour_key", None)
                if hour_key:
                    self._export_retry_hours.add(hour_key)
                LOGGER.exception("Writing final transcript to Markdown failed")

        if result.success:
            text = str(
                getattr(record, "display_text", "")
                or result.normalized_text
                or result.raw_text
            )
            if export_error is not None:
                message = f"最終文字已保存，但 Markdown 寫入失敗並將重試：{export_error}"
            elif cleanup_failures:
                self._cleanup_degraded = True
                message = (
                    f"最終文字已寫入，但 {len(cleanup_failures)} 個音訊檔仍被占用；"
                    "將持續重試"
                )
            else:
                message = "最終轉錄已寫入每小時紀錄"
            severity = (
                Severity.ERROR
                if export_error is not None or cleanup_failures
                else Severity.INFO
            )
            last_error = message if severity == Severity.ERROR else None
            self._retry_after.pop(result.segment_id, None)
        else:
            text = self.snapshot.final_text
            message = result.error or "最終轉錄失敗，將稍後重試"
            severity = Severity.ERROR
            last_error = message
            self._retry_after[result.segment_id] = self._monotonic() + 5.0
        clear_partial = result.segment_id == self.snapshot.current_segment_id
        correctable_text = str(
            getattr(record, "display_text", "") or self.snapshot.correctable_text
        )
        self._update(
            timeline_changed=True,
            final_text=text,
            partial_text="" if clear_partial else self.snapshot.partial_text,
            current_segment_id=result.segment_id,
            correctable_segment_id=result.segment_id,
            correctable_text=correctable_text,
            current_hour_key=getattr(record, "hour_key", None)
            or self.snapshot.current_hour_key,
            backlog=self._pending_count(self.snapshot.backlog),
            message=message,
            severity=severity,
            last_error=last_error,
        )

    def _handle_input_route(self, update: InputRouteUpdate) -> None:
        degraded = update.input_route in {
            InputRoute.FALLBACK,
            InputRoute.UNAVAILABLE,
            InputRoute.PENDING,
            InputRoute.SKIPPED,
        }
        changes: dict[str, Any] = {
            "preferred_input_name": update.preferred_input_name,
            "active_input_name": update.active_input_name,
            "input_route": update.input_route,
            "input_switching": update.input_switching,
            "preferred_input_available": update.preferred_input_available,
            "input_route_reason": update.reason,
        }
        if update.reason:
            changes["message"] = update.reason
        if degraded and not self._pause_requested:
            changes["state"] = WorkerState.DEGRADED
            if self.snapshot.severity != Severity.ERROR:
                changes["severity"] = Severity.WARNING
        elif not update.input_switching:
            changes["state"] = self._aggregate_worker_state(route=update.input_route)
            changes["severity"] = self._aggregate_worker_severity(route=update.input_route)
        self._update(**changes)

    def _handle_status(self, status: WorkerStatus) -> None:
        log_level = {
            Severity.INFO: logging.INFO,
            Severity.WARNING: logging.WARNING,
            Severity.ERROR: logging.ERROR,
        }[status.severity]
        LOGGER.log(
            log_level,
            "%s status=%s message=%s queue=%d metadata=%s",
            status.worker.value,
            status.state.value,
            status.message,
            status.queue_size,
            status.metadata,
        )
        self._worker_statuses[status.worker] = status
        if status.worker == WorkerKind.RECORDER:
            if status.state == WorkerState.PAUSED:
                self._pause_requested = True
            elif status.state == WorkerState.RECORDING:
                self._pause_requested = False
        statuses = tuple(self._worker_statuses.values())
        state = self._aggregate_worker_state()
        severity = self._aggregate_worker_severity()
        representative = (
            status
            if status.severity == severity
            else next(
                (item for item in statuses if item.severity == severity),
                status,
            )
        )
        backlog = max(
            *(item.queue_size for item in statuses),
            self._pending_count(self.snapshot.backlog),
        )
        changes: dict[str, Any] = {
            "state": state,
            "severity": severity,
            "message": representative.message or representative.state.value,
            "paused": self._pause_requested,
            "backlog": backlog,
            "last_error": (
                representative.message
                if state == WorkerState.ERROR or severity == Severity.ERROR
                else None
            ),
        }
        if (
            self.snapshot.input_route in {InputRoute.FALLBACK, InputRoute.UNAVAILABLE}
            and self.snapshot.input_route_reason
            and state != WorkerState.ERROR
        ):
            changes["message"] = self.snapshot.input_route_reason
        if (
            status.worker == WorkerKind.RECORDER
            and status.state in INACTIVE_RECORDER_STATES
        ):
            changes.update(self._silent_audio_changes())
        self._update(**changes)

    def _aggregate_worker_state(
        self,
        *,
        route: InputRoute | None = None,
    ) -> WorkerState:
        statuses = tuple(self._worker_statuses.values())
        states = {item.state for item in statuses}
        if WorkerState.ERROR in states:
            return WorkerState.ERROR
        if WorkerState.DEGRADED in states:
            return WorkerState.DEGRADED
        effective_route = route or self.snapshot.input_route
        if effective_route in {InputRoute.FALLBACK, InputRoute.UNAVAILABLE}:
            return WorkerState.DEGRADED
        if self._pause_requested:
            return WorkerState.PAUSED
        if any(
            item.worker == WorkerKind.RECORDER and item.state == WorkerState.RECORDING
            for item in statuses
        ):
            return WorkerState.RECORDING
        if WorkerState.STARTING in states:
            return WorkerState.STARTING
        if statuses and all(item.state == WorkerState.STOPPED for item in statuses):
            return WorkerState.STOPPED
        if statuses:
            return WorkerState.READY
        if not self._started:
            return WorkerState.STOPPED
        return WorkerState.PAUSED if self._pause_requested else WorkerState.STARTING

    def _aggregate_worker_severity(
        self,
        *,
        route: InputRoute | None = None,
    ) -> Severity:
        severity = max(
            (status.severity for status in self._worker_statuses.values()),
            key=_severity_rank,
            default=Severity.INFO,
        )
        effective_route = route or self.snapshot.input_route
        if (
            effective_route in {InputRoute.FALLBACK, InputRoute.UNAVAILABLE}
            and severity == Severity.INFO
        ):
            return Severity.WARNING
        return severity

    def _pending_count(self, fallback: int) -> int:
        try:
            return max(0, int(self.storage.count_pending()))
        except Exception:
            LOGGER.debug("Unable to read pending count", exc_info=True)
            return fallback

    def _submit_segment(self, segment: CapturedSegment) -> bool:
        self._last_submission_error = None
        if segment.segment_id in self._submitted_ids:
            return True
        workers = self._require_workers()
        try:
            accepted = workers.submit(segment)
        except Exception as error:
            self._last_submission_error = f"提交最終轉錄失敗：{error}"
            LOGGER.warning("Unable to submit final transcription", exc_info=True)
            return False
        if accepted:
            try:
                self.storage.mark_finalizing(segment.segment_id)
            except Exception as error:
                self._last_submission_error = f"記錄最終轉錄狀態失敗：{error}"
                LOGGER.warning("Unable to mark segment as finalizing", exc_info=True)
                return False
            self._submitted_ids.add(segment.segment_id)
        return accepted

    def _retry_pending(self, now: float) -> None:
        if self.workers is None or not self._workers_started:
            return
        try:
            records = self.storage.list_segments(
                states=(
                    SegmentState.CAPTURED,
                    SegmentState.FINALIZING,
                    SegmentState.RETRY,
                ),
                limit=64,
            )
        except Exception:
            LOGGER.warning("Unable to list pending segments", exc_info=True)
            return
        timeline_changed = False
        for record in records:
            segment_id = str(record.segment_id)
            if segment_id in self._submitted_ids:
                continue
            if now < self._retry_after.get(segment_id, float("-inf")):
                continue
            segment = CapturedSegment(
                segment_id=segment_id,
                audio_path=Path(record.audio_path),
                started_at_utc=record.started_at_utc,
                ended_at_utc=record.ended_at_utc,
                preview_text=str(getattr(record, "provisional_text", "") or ""),
                sample_rate=int(getattr(record, "sample_rate", 16_000)),
                duration_ms=int(getattr(record, "duration_ms", 0)),
                preview_raw_text=str(getattr(record, "provisional_raw", "") or ""),
                leading_overlap_ms=int(
                    getattr(record, "leading_overlap_ms", 0)
                ),
                previous_segment_id=getattr(record, "previous_segment_id", None),
            )
            previous_state = getattr(record, "state", None)
            submitted = self._submit_segment(segment)
            if submitted and previous_state in {
                SegmentState.CAPTURED,
                SegmentState.RETRY,
            }:
                timeline_changed = True
            if not submitted:
                if self._last_submission_error is not None:
                    self._update(
                        state=WorkerState.DEGRADED,
                        severity=Severity.ERROR,
                        message=self._last_submission_error,
                        last_error=self._last_submission_error,
                    )
                break
        if timeline_changed:
            self._update(timeline_changed=True)

    def _retry_exports(self) -> None:
        for hour_key in tuple(sorted(self._export_retry_hours)):
            try:
                self.exporter.rebuild_hour(hour_key)
            except Exception:
                LOGGER.warning("Unable to retry hour export %s", hour_key, exc_info=True)
            else:
                self._export_retry_hours.discard(hour_key)
                severity = self._aggregate_worker_severity()
                self._update(
                    state=self._aggregate_worker_state(),
                    message=f"已補寫 {hour_key} 的每小時紀錄",
                    severity=severity,
                    last_error=(
                        self.snapshot.last_error if severity == Severity.ERROR else None
                    ),
                )

    def _retry_pending_deletions(self) -> None:
        try:
            result = self.exporter.retry_pending_deletions()
        except Exception as error:
            self._cleanup_degraded = True
            message = f"重試刪除音訊失敗：{error}"
            LOGGER.warning("Retrying durable audio deletions failed", exc_info=True)
            self._update(
                state=WorkerState.DEGRADED,
                severity=Severity.ERROR,
                message=message,
                last_error=message,
            )
            return

        pending_paths = tuple(getattr(result, "pending_paths", ()))
        if pending_paths:
            self._cleanup_degraded = True
            message = f"{len(pending_paths)} 個音訊檔仍被占用；將持續重試刪除"
            worker_state = self._aggregate_worker_state()
            self._update(
                state=(
                    WorkerState.ERROR
                    if worker_state == WorkerState.ERROR
                    else WorkerState.DEGRADED
                ),
                severity=Severity.ERROR,
                message=message,
                backlog=self._pending_count(self.snapshot.backlog),
                last_error=message,
            )
            return

        if not self._cleanup_degraded:
            return
        self._cleanup_degraded = False
        severity = self._aggregate_worker_severity()
        self._update(
            state=self._aggregate_worker_state(),
            severity=severity,
            message="已完成先前受阻的音訊刪除",
            backlog=self._pending_count(self.snapshot.backlog),
            last_error=None if severity != Severity.ERROR else self.snapshot.last_error,
        )

    def _refresh_hotwords(self) -> None:
        if self.workers is None or self.vocabulary is None:
            return
        try:
            self.workers.update_hotwords(
                list(self.vocabulary.hotwords(minimum_count=2))
            )
        except Exception:
            LOGGER.warning("Unable to refresh hotwords", exc_info=True)

    def _record_error(self, prefix: str, error: Exception) -> None:
        message = f"{prefix}: {error}"
        LOGGER.exception(prefix)
        self._update(
            state=WorkerState.ERROR,
            severity=Severity.ERROR,
            message=message,
            last_error=message,
        )

    def _update(self, *, timeline_changed: bool = False, **changes: Any) -> None:
        with self._lock:
            if timeline_changed:
                changes["timeline_revision"] = self._snapshot.timeline_revision + 1
            self._snapshot = replace(self._snapshot, **changes)
            snapshot = self._snapshot
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                LOGGER.exception("Controller listener failed")

    def _require_workers(self) -> WorkersPort:
        if self.workers is None:
            raise RuntimeError("workers are not bound")
        return self.workers

    def _ensure_workers(self) -> WorkersPort:
        if self.workers is None:
            if self._workers_factory is None:
                raise RuntimeError("workers are not bound and no workers_factory is configured")
            self.workers = self._workers_factory(self._config)
        return self.workers


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.WARNING: 1,
        Severity.ERROR: 2,
    }[severity]


def _selection_input_route(selection: MicrophoneSelection) -> InputRoute:
    return {
        MicrophoneMode.PENDING: InputRoute.PENDING,
        MicrophoneMode.SKIPPED: InputRoute.SKIPPED,
        MicrophoneMode.SYSTEM_DEFAULT: InputRoute.SYSTEM_DEFAULT,
        MicrophoneMode.FIXED: InputRoute.PREFERRED,
    }[selection.mode]
