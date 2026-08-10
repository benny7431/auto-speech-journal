from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QSize

from auto_speech_journal import __version__
from auto_speech_journal.audio import InputDevice
from auto_speech_journal.config import (
    AppConfig,
    DeviceFingerprint,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.controller import ControllerSnapshot
from auto_speech_journal.timeline import (
    DayTimelineView,
    TimelineHourView,
    TimelineSegmentView,
)
from auto_speech_journal.types import InputRoute, SegmentState, WorkerState
from auto_speech_journal.ui_models import (
    EXPANDED_HEIGHT,
    EXPANDED_MIN_HEIGHT,
    EXPANDED_MIN_WIDTH,
    EXPANDED_WIDTH,
    SPI_GETCLIENTAREAANIMATION,
    JournalViewModel,
    LocalFontCatalog,
    _windows_reduced_motion,
)


class _TimelineController:
    def __init__(self) -> None:
        self.config = AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
            onboarding_completed=True,
        )
        self.saved_configs: list[AppConfig] = []
        self.microphone_calls: list[object] = []
        self.started = False
        self.vocabulary_counts: dict[str, int] = {}
        self.vocabulary_calls: list[object] = []
        self.snapshot = ControllerSnapshot(
            state=WorkerState.RECORDING,
            timeline_revision=1,
        )
        self.segments = [self.segment("existing", SegmentState.FINAL_READY)]

    @staticmethod
    def segment(
        segment_id: str,
        state: SegmentState,
    ) -> TimelineSegmentView:
        return TimelineSegmentView(
            segment_id=segment_id,
            time_label="[09:02:03]",
            text="既有聲跡" if state == SegmentState.FINAL_READY else "",
            status_label="已定稿" if state == SegmentState.FINAL_READY else "待定稿",
            editable=state == SegmentState.FINAL_READY,
            hour_key="2026-07-12_09",
            state=state,
        )

    def tick(self) -> None:
        pass

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

    def advance_revision(self) -> None:
        self.snapshot = replace(
            self.snapshot,
            timeline_revision=self.snapshot.timeline_revision + 1,
        )

    def update_settings(self, config: AppConfig) -> None:
        self.config = config
        self.saved_configs.append(config)

    def configure_microphone(self, selection: MicrophoneSelection) -> None:
        self.microphone_calls.append(("configure", selection))
        self.config = replace(self.config, microphone=selection)

    def skip_microphone_setup(self) -> None:
        self.microphone_calls.append("skip")
        self.config = replace(
            self.config,
            microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
        )

    def retry_preferred_input(self) -> None:
        self.microphone_calls.append("retry")

    def start(self) -> None:
        self.started = True

    def learned_vocabulary(self) -> dict[str, int]:
        return dict(self.vocabulary_counts)

    def delete_vocabulary_term(self, term: str) -> bool:
        self.vocabulary_calls.append(("delete", term))
        return self.vocabulary_counts.pop(term, None) is not None

    def clear_vocabulary(self) -> int:
        self.vocabulary_calls.append("clear")
        count = len(self.vocabulary_counts)
        self.vocabulary_counts.clear()
        return count

    def set_vocabulary_learning_enabled(self, enabled: bool) -> None:
        self.vocabulary_calls.append(("learning", enabled))
        self.config = replace(self.config, vocabulary_learning_enabled=enabled)


def _view_model(qapp, tmp_path) -> tuple[JournalViewModel, _TimelineController]:
    controller = _TimelineController()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    return JournalViewModel(controller, qapp, settings=settings), controller


def _input_device(
    name: str,
    index: int,
    *,
    is_default: bool = False,
    fixed_binding_available: bool = True,
    binding_error: str = "",
) -> InputDevice:
    return InputDevice(
        index=index,
        name=name,
        host_api="Windows WASAPI",
        endpoint_id=f"wasapi:windows wasapi:{name.casefold()}",
        default_sample_rate=48_000,
        max_input_channels=1,
        is_default=is_default,
        fixed_binding_available=fixed_binding_available,
        binding_error=binding_error,
    )


def test_onboarding_selection_does_not_save_or_start_before_explicit_confirmation(
    qapp,
    tmp_path,
):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    devices = [_input_device("Built-in Mic", 2, is_default=True)]
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: devices,
    )

    assert view_model.microphoneSetupPending is True
    assert view_model.rescanMicrophones() == 1
    assert view_model.microphoneOptions[0]["key"] == "system_default"
    assert view_model.selectedMicrophoneKey == ""
    assert view_model.selectMicrophone("system_default") is True
    qapp.processEvents()

    assert controller.config.microphone.mode is MicrophoneMode.PENDING
    assert controller.started is False
    assert view_model.microphoneSetupPending is True


def test_onboarding_model_repair_is_background_and_gates_recording(
    qapp,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    provision_started = threading.Event()
    release_provision = threading.Event()
    microphone_opened = threading.Event()

    def provision(progress):
        progress(
            {
                "state": "provisioning",
                "ready": False,
                "message": "正在續傳模型",
                "completed": 50,
                "total": 100,
                "asset": "whisper/model.bin",
            }
        )
        provision_started.set()
        assert release_provision.wait(timeout=2)
        return {
            "state": "ready",
            "ready": True,
            "message": "模型修復完成",
            "completed": 100,
            "total": 100,
        }

    def measure(*_args, **_kwargs):
        microphone_opened.set()
        return SimpleNamespace(peak=0.2, rms=0.1)

    monkeypatch.setattr("auto_speech_journal.audio.measure_input_level", measure)
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [
            _input_device("Built-in Mic", 2, is_default=True)
        ],
        model_status_callback=lambda: {
            "state": "not_ready",
            "ready": False,
            "message": "模型尚未就緒",
        },
        model_provision_callback=provision,
    )
    assert view_model.checkOnboardingModels() is True
    deadline = time.monotonic() + 1
    while view_model.onboardingModelBusy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert view_model.onboardingModelsReady is False

    records_root = str(tmp_path / "journal")
    for _ in range(3):
        assert view_model.advanceOnboarding(records_root, False, False) is True
    assert view_model.selectMicrophone("system_default") is True
    assert view_model.advanceOnboarding(records_root, False, False) is True

    assert view_model.startOnboardingRecording() is False
    assert controller.saved_configs == []
    assert controller.started is False
    assert microphone_opened.is_set() is False

    started_at = time.monotonic()
    assert view_model.repairOnboardingModels() is True
    assert time.monotonic() - started_at < 0.2
    assert provision_started.wait(timeout=1)
    assert view_model.onboardingModelBusy is True
    release_provision.set()
    deadline = time.monotonic() + 1
    while not view_model.onboardingModelsReady and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert view_model.onboardingModelsReady is True
    assert view_model.onboardingModelProgress == 1.0
    assert view_model.startOnboardingRecording() is True
    deadline = time.monotonic() + 1
    while not controller.started and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert microphone_opened.is_set() is True
    assert controller.started is True


def test_onboarding_can_defer_while_model_repair_is_running(qapp, tmp_path):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    provision_started = threading.Event()
    release_provision = threading.Event()

    def provision(_progress):
        provision_started.set()
        assert release_provision.wait(timeout=2)
        return {"state": "ready", "ready": True, "message": "模型修復完成"}

    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        model_status_callback=lambda: False,
        model_provision_callback=provision,
    )

    assert view_model.repairOnboardingModels() is True
    assert provision_started.wait(timeout=1)
    assert view_model.deferOnboarding() is True
    assert view_model.onboardingPending is False
    assert controller.config.onboarding_completed is False
    assert controller.started is False

    release_provision.set()
    deadline = time.monotonic() + 1
    while view_model.onboardingModelBusy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert controller.started is False


def test_onboarding_commits_once_then_starts_and_invokes_opt_in_services(
    qapp,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        records_root=str(tmp_path / "initial"),
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
        startup_enabled=False,
        update_check_enabled=False,
    )
    startup_calls: list[bool] = []
    update_calls: list[bool] = []
    microphone_open_calls: list[object] = []
    measurement_started = threading.Event()
    release_measurement = threading.Event()

    def measure_input(*args, **kwargs):
        microphone_open_calls.append((args, kwargs))
        measurement_started.set()
        assert release_measurement.wait(timeout=1)
        return SimpleNamespace(peak=0.2, rms=0.1)

    monkeypatch.setattr(
        "auto_speech_journal.audio.measure_input_level",
        measure_input,
    )

    def update_check(*, enabled: bool, callback) -> bool:
        update_calls.append(enabled)
        callback(
            {
                "update_available": True,
                "latest_version": "0.2.1",
                "release_url": "https://github.com/benny7431/auto-speech-journal/releases/tag/v0.2.1",
            }
        )
        return True

    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [
            _input_device("Built-in Mic", 2, is_default=True)
        ],
        startup_setting_callback=lambda enabled: startup_calls.append(enabled),
        update_check_callback=update_check,
    )
    view_model.rescanMicrophones()

    records_root = str(tmp_path / "chosen-journal")
    assert view_model.advanceOnboarding(records_root, False, False) is True
    assert view_model.advanceOnboarding(records_root, False, False) is True
    assert view_model.advanceOnboarding(records_root, True, True) is True
    assert view_model.selectMicrophone("system_default") is True
    assert view_model.testSelectedMicrophone() is False
    assert view_model.advanceOnboarding(records_root, True, True) is True

    assert controller.saved_configs == []
    assert controller.started is False
    assert microphone_open_calls == []
    assert view_model.startOnboardingRecording() is True
    assert measurement_started.wait(timeout=1)

    assert len(controller.saved_configs) == 1
    assert controller.config.onboarding_completed is True
    assert controller.config.records_root == str((tmp_path / "chosen-journal").resolve())
    assert controller.config.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT
    assert controller.config.startup_enabled is True
    assert controller.config.update_check_enabled is True
    assert startup_calls == [True]
    assert update_calls == [True]
    assert len(microphone_open_calls) == 1
    assert controller.started is False

    release_measurement.set()
    deadline = time.monotonic() + 1
    while not controller.started and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert controller.started is True
    assert view_model.updateAvailableText == "有新版本 0.2.1 可下載"


def test_onboarding_folder_probe_is_temporary_and_does_not_persist(qapp, tmp_path):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    chosen = tmp_path / "new-journal"

    assert view_model.advanceOnboarding(str(chosen), False, False) is True
    assert view_model.advanceOnboarding(str(chosen), False, False) is True

    assert chosen.is_dir()
    assert list(chosen.iterdir()) == []
    assert controller.saved_configs == []
    assert controller.config.records_root != str(chosen)


def test_post_consent_microphone_failure_keeps_config_and_retries_before_worker_start(
    qapp,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )

    def fail_measurement(*_args, **_kwargs):
        raise OSError("microphone permission denied")

    monkeypatch.setattr(
        "auto_speech_journal.audio.measure_input_level",
        fail_measurement,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [
            _input_device("Built-in Mic", 2, is_default=True)
        ],
    )
    view_model.rescanMicrophones()
    records_root = str(tmp_path / "journal")
    for _ in range(3):
        assert view_model.advanceOnboarding(records_root, False, False) is True
    assert view_model.selectMicrophone("system_default") is True
    assert view_model.advanceOnboarding(records_root, False, False) is True

    assert view_model.startOnboardingRecording() is True
    deadline = time.monotonic() + 1
    while view_model.microphoneTestState != "error" and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert controller.config.onboarding_completed is True
    assert controller.config.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT
    assert controller.started is False
    assert view_model.recordingEngineNeedsStart is True

    monkeypatch.setattr(
        "auto_speech_journal.audio.measure_input_level",
        lambda *_args, **_kwargs: SimpleNamespace(peak=0.2, rms=0.1),
    )
    assert view_model.startControllerIfReady() is False
    deadline = time.monotonic() + 1
    while not controller.started and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert controller.started is True


def test_settings_flags_use_injected_services_and_update_notice(qapp, tmp_path):
    controller = _TimelineController()
    startup_calls: list[bool] = []
    update_calls: list[bool] = []

    def update_check(*, enabled: bool, callback) -> bool:
        update_calls.append(enabled)
        callback(
            {
                "update_available": enabled,
                "latest_version": "0.3.0",
                "release_url": "https://github.com/benny7431/auto-speech-journal/releases/tag/v0.3.0",
            }
        )
        return True

    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        startup_setting_callback=lambda enabled: startup_calls.append(enabled),
        update_check_callback=update_check,
    )
    config = controller.config

    assert view_model.applySettings(
        config.records_root,
        config.preview_interval_ms,
        config.endpoint_silence_ms,
        config.max_segment_ms,
        "",
        True,
        True,
    ) is True
    qapp.processEvents()

    assert startup_calls == [True]
    assert update_calls == [True]
    assert controller.config.startup_enabled is True
    assert controller.config.update_check_enabled is True
    assert view_model.updateAvailable is True
    assert view_model.updateAvailableText == "有新版本 0.3.0 可下載"


def test_update_opt_out_ignores_stale_in_flight_callbacks(qapp, tmp_path):
    controller = _TimelineController()
    callbacks: list[tuple[bool, object]] = []

    def update_check(*, enabled: bool, callback) -> bool:
        callbacks.append((enabled, callback))
        return True

    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        update_check_callback=update_check,
    )
    config = controller.config
    args = (
        config.records_root,
        config.preview_interval_ms,
        config.endpoint_silence_ms,
        config.max_segment_ms,
        "",
        False,
    )

    assert view_model.applySettings(*args, True) is True
    assert view_model.applySettings(*args, False) is True
    assert [enabled for enabled, _ in callbacks] == [True, False]

    stale_result = {
        "update_available": True,
        "latest_version": "9.9.9",
        "release_url": "https://github.com/benny7431/auto-speech-journal/releases/tag/v9.9.9",
    }
    for _, callback in callbacks:
        callback(stale_result)
    qapp.processEvents()

    assert controller.config.update_check_enabled is False
    assert view_model.updateAvailable is False
    assert view_model.updateAvailableText == ""


def test_unavailable_task_scheduler_keeps_startup_disabled(qapp, tmp_path):
    controller = _TimelineController()
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        startup_setting_callback=lambda _enabled: SimpleNamespace(
            available=False,
            enabled=False,
        ),
    )
    config = controller.config

    assert view_model.applySettings(
        config.records_root,
        config.preview_interval_ms,
        config.endpoint_silence_ms,
        config.max_segment_ms,
        "",
        True,
        False,
    ) is False

    assert controller.config.startup_enabled is False
    assert controller.saved_configs == []


def test_settings_failure_rolls_back_external_startup_task(qapp, tmp_path):
    controller = _TimelineController()
    startup_calls: list[bool] = []

    def fail_update(_config: AppConfig) -> None:
        raise OSError("config write failed")

    controller.update_settings = fail_update  # type: ignore[method-assign]
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        startup_setting_callback=lambda enabled: startup_calls.append(enabled),
    )
    config = controller.config

    assert view_model.applySettings(
        config.records_root,
        config.preview_interval_ms,
        config.endpoint_silence_ms,
        config.max_segment_ms,
        "",
        True,
        False,
    ) is False

    assert startup_calls == [True, False]
    assert controller.config.startup_enabled is False


def test_failed_first_start_retries_after_settings_are_saved(qapp, tmp_path):
    controller = _TimelineController()
    attempts: list[int] = []

    def flaky_start() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("microphone open failed")
        controller.started = True

    controller.start = flaky_start
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [_input_device("Built-in Mic", 2, is_default=True)],
    )
    assert view_model.startControllerIfReady() is False
    assert attempts == [1]
    assert view_model.recordingEngineNeedsStart is True

    config = controller.config
    assert view_model.applySettings(
        config.records_root,
        config.preview_interval_ms,
        config.endpoint_silence_ms,
        config.max_segment_ms,
        "",
    ) is True
    qapp.processEvents()

    assert attempts == [1, 2]
    assert controller.started is True
    assert view_model.recordingEngineNeedsStart is False


def test_failed_first_start_can_defer_even_when_catalog_has_a_device(qapp, tmp_path):
    controller = _TimelineController()
    attempts = 0

    def fail_once_then_start() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("microphone permission denied")
        controller.started = True

    controller.start = fail_once_then_start
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [_input_device("Built-in Mic", 2, is_default=True)],
    )
    assert view_model.startControllerIfReady() is False
    assert view_model.recordingEngineNeedsStart is True

    assert view_model.deferMicrophoneAfterStartFailure() is True
    qapp.processEvents()

    assert controller.config.microphone.mode is MicrophoneMode.SKIPPED
    assert controller.started is False
    assert view_model.recordingEngineNeedsStart is False


def test_onboarding_defer_is_persisted_without_starting(qapp, tmp_path):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [],
    )

    assert view_model.rescanMicrophones() == 0
    assert view_model.deferOnboarding() is True
    qapp.processEvents()

    assert controller.config.microphone.mode is MicrophoneMode.SKIPPED
    assert controller.config.onboarding_completed is False
    assert len(controller.saved_configs) == 1
    assert controller.saved_configs[0].microphone.mode is MicrophoneMode.SKIPPED
    assert controller.started is False
    assert view_model.onboardingPending is False
    assert view_model.recordingControlsEnabled is False
    assert view_model.stateText == "尚未開始錄音"
    assert "完成首次設定" in view_model.partialText

    restarted = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "restarted.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [],
    )
    assert restarted.onboardingPending is False
    assert restarted.openOnboarding() is True
    assert restarted.onboardingPending is True


def test_skipped_microphone_allows_saving_non_microphone_settings(qapp, tmp_path):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.SKIPPED),
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [],
    )
    view_model.rescanMicrophones()

    assert view_model.selectedMicrophoneKey == ""
    assert view_model.settingsMicrophoneSelectionValid is True
    assert view_model.applySettings(
        controller.config.records_root,
        500,
        controller.config.endpoint_silence_ms,
        controller.config.max_segment_ms,
        "",
    ) is True
    qapp.processEvents()

    assert controller.config.preview_interval_ms == 500
    assert controller.config.microphone.mode is MicrophoneMode.SKIPPED


def test_pending_microphone_setup_can_be_skipped_when_no_route_is_selectable(
    qapp,
    tmp_path,
):
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(mode=MicrophoneMode.PENDING),
        onboarding_completed=False,
    )
    unsafe_device = _input_device(
        "Ambiguous Mic",
        3,
        fixed_binding_available=False,
        binding_error="multiple matching endpoints",
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [unsafe_device],
    )

    assert view_model.rescanMicrophones() == 1
    assert all(not option["selectable"] for option in view_model.microphoneOptions)
    assert view_model.deferOnboarding() is True
    qapp.processEvents()

    assert controller.config.microphone.mode is MicrophoneMode.SKIPPED
    assert controller.started is False
    assert view_model.onboardingPending is False


def test_microphone_catalog_keeps_offline_preference_and_disables_ambiguous_fixed(
    qapp,
    tmp_path,
):
    preferred = DeviceFingerprint(
        name="Offline USB Mic",
        host_api="Windows WASAPI",
        endpoint_id="wasapi:1:offline usb mic",
    )
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(
            mode=MicrophoneMode.FIXED,
            preferred_device=preferred,
        ),
    )
    duplicate = _input_device(
        "Duplicate Mic",
        4,
        is_default=True,
        fixed_binding_available=False,
        binding_error="duplicate WASAPI name",
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [duplicate],
    )

    view_model.rescanMicrophones()
    options = {item["key"]: item for item in view_model.microphoneOptions}

    assert options["system_default"]["selectable"] is True
    assert options["fixed:wasapi:windows wasapi:duplicate mic"]["selectable"] is False
    assert options["fixed:wasapi:1:offline usb mic"]["offline"] is True
    assert view_model.selectedMicrophoneKey == "fixed:wasapi:1:offline usb mic"


def test_settings_reject_an_unsafe_fixed_key_even_if_its_fingerprint_is_cataloged(
    qapp,
    tmp_path,
):
    controller = _TimelineController()
    unsafe_device = _input_device(
        "Ambiguous Mic",
        4,
        fixed_binding_available=False,
        binding_error="duplicate WASAPI name",
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [unsafe_device],
    )
    view_model.rescanMicrophones()
    unsafe_key = "fixed:wasapi:windows wasapi:ambiguous mic"

    assert view_model.selectMicrophone(unsafe_key) is False
    assert view_model.applySettings(
        controller.config.records_root,
        controller.config.preview_interval_ms,
        controller.config.endpoint_silence_ms,
        controller.config.max_segment_ms,
        unsafe_key,
    ) is False
    assert controller.saved_configs == []


def test_catalog_disables_fixed_entries_that_share_one_stable_key(qapp, tmp_path):
    endpoint_id = "wasapi:windows wasapi:mic array"
    devices = [
        replace(_input_device("Mic  Array", 4), endpoint_id=endpoint_id),
        replace(_input_device("Mic Array", 5), endpoint_id=endpoint_id),
    ]
    controller = _TimelineController()
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: devices,
    )

    view_model.rescanMicrophones()
    duplicate_options = [
        option
        for option in view_model.microphoneOptions
        if option["key"] == f"fixed:{endpoint_id}"
    ]

    assert len(duplicate_options) == 2
    assert all(not option["selectable"] for option in duplicate_options)


def test_microphone_catalog_reconciles_legacy_endpoint_by_unique_identity(
    qapp,
    tmp_path,
):
    preferred = DeviceFingerprint(
        name="USB Mic",
        host_api="Windows WASAPI",
        endpoint_id="wasapi:1:usb mic",
    )
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(
            mode=MicrophoneMode.FIXED,
            preferred_device=preferred,
        ),
    )
    live_device = _input_device("USB Mic", 7, is_default=True)
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [live_device],
    )

    view_model.rescanMicrophones()
    stable_key = "fixed:wasapi:windows wasapi:usb mic"
    matching_options = [
        option
        for option in view_model.microphoneOptions
        if option["name"] == "USB Mic" and option["mode"] == MicrophoneMode.FIXED.value
    ]

    assert view_model.selectedMicrophoneKey == stable_key
    assert len(matching_options) == 1
    assert matching_options[0]["offline"] is False


def test_nonactive_microphone_test_runs_off_gui_thread(
    qapp,
    qtbot,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    device = _input_device("USB Mic", 7, is_default=True)
    calls: list[tuple[object, int]] = []

    def measure(fingerprint, *, duration_ms):
        calls.append((fingerprint, duration_ms))
        return type("Level", (), {"peak": 0.25, "rms": 0.1})()

    monkeypatch.setattr("auto_speech_journal.audio.measure_input_level", measure)
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [device],
    )
    view_model.rescanMicrophones()
    assert view_model.selectMicrophone("fixed:wasapi:windows wasapi:usb mic") is True

    assert view_model.testSelectedMicrophone() is True
    assert view_model.microphoneTestRunning is True
    qtbot.waitUntil(lambda: view_model.microphoneTestState == "success")

    assert calls[0][1] == 800
    assert view_model.microphoneTestLevel > 0


def test_system_default_microphone_test_follows_the_live_default(
    qapp,
    qtbot,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    device = _input_device("Built-in Mic", 2, is_default=True)
    calls: list[tuple[object, int, bool]] = []

    def measure(fingerprint, *, duration_ms, follow_system_default=False):
        calls.append((fingerprint, duration_ms, follow_system_default))
        return type("Level", (), {"peak": 0.25, "rms": 0.1})()

    monkeypatch.setattr("auto_speech_journal.audio.measure_input_level", measure)
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [device],
    )
    view_model.rescanMicrophones()

    assert view_model.selectedMicrophoneKey == "system_default"
    assert view_model.testSelectedMicrophone() is True
    qtbot.waitUntil(lambda: view_model.microphoneTestState == "success")

    assert calls == [(device.fingerprint(), 800, True)]


def test_microphone_test_watchdog_unlocks_ui_and_ignores_late_result(
    qapp,
    qtbot,
    tmp_path,
    monkeypatch,
):
    controller = _TimelineController()
    device = _input_device("USB Mic", 7, is_default=True)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def measure(_fingerprint, *, duration_ms):
        assert duration_ms == 800
        started.set()
        release.wait(timeout=2)
        finished.set()
        return type("Level", (), {"peak": 0.25, "rms": 0.1})()

    monkeypatch.setattr("auto_speech_journal.audio.measure_input_level", measure)
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.MICROPHONE_TEST_TIMEOUT_MS",
        25,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        microphone_device_provider=lambda: [device],
    )
    view_model.rescanMicrophones()
    assert view_model.selectMicrophone("fixed:wasapi:windows wasapi:usb mic") is True

    assert view_model.testSelectedMicrophone() is True
    assert started.wait(timeout=1)
    qtbot.waitUntil(lambda: view_model.microphoneTestState == "error")
    assert view_model.microphoneTestRunning is False
    assert "逾時" in view_model.microphoneTestMessage

    release.set()
    assert finished.wait(timeout=1)
    qtbot.wait(25)
    assert view_model.microphoneTestState == "error"


def test_fallback_status_keeps_preference_and_exposes_manual_retry(qapp, tmp_path):
    preferred = DeviceFingerprint(
        name="USB Mic",
        host_api="Windows WASAPI",
        endpoint_id="wasapi:1:usb mic",
    )
    controller = _TimelineController()
    controller.config = replace(
        controller.config,
        microphone=MicrophoneSelection(
            mode=MicrophoneMode.FIXED,
            preferred_device=preferred,
        ),
    )
    controller.snapshot = replace(
        controller.snapshot,
        preferred_input_name="USB Mic",
        active_input_name="Built-in Mic",
        input_route=InputRoute.FALLBACK,
        preferred_input_available=True,
        input_route_reason="preferred input disappeared",
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )

    assert view_model.preferredInputName == "USB Mic"
    assert view_model.activeInputName == "Built-in Mic"
    assert view_model.inputFallbackActive is True
    assert view_model.preferredInputAvailable is True
    assert "偏好已保留" in view_model.inputStatusText
    assert view_model.inputRouteNoticeText == view_model.inputStatusText
    assert view_model.retryPreferredInput() is True
    assert controller.microphone_calls[-1] == "retry"


def test_healthy_input_route_has_no_duplicate_supplemental_notice(qapp, tmp_path):
    controller = _TimelineController()
    controller.snapshot = replace(
        controller.snapshot,
        preferred_input_name="USB Mic",
        active_input_name="USB Mic",
        input_route=InputRoute.PREFERRED,
        preferred_input_available=True,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )

    assert view_model.inputStatusText == "目前收音：USB Mic"
    assert view_model.inputRouteNoticeText == ""


def test_windows_reduced_motion_reads_client_area_animation_setting():
    def query_with(enabled: bool):
        def query(action, _parameter, destination, _flags):
            assert action == SPI_GETCLIENTAREAANIMATION
            pointer = ctypes.cast(destination, ctypes.POINTER(ctypes.c_int))
            pointer.contents.value = int(enabled)
            return 1

        return query

    assert _windows_reduced_motion(
        platform_name="win32",
        system_parameters_info=query_with(enabled=False),
    )
    assert not _windows_reduced_motion(
        platform_name="win32",
        system_parameters_info=query_with(enabled=True),
    )
    assert not _windows_reduced_motion(
        platform_name="linux",
        system_parameters_info=lambda *_args: (_ for _ in ()).throw(AssertionError),
    )


def test_reduced_motion_is_exposed_as_qml_property(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "auto_speech_journal.ui_models._windows_reduced_motion",
        lambda: True,
    )
    view_model, _ = _view_model(qapp, tmp_path)

    assert view_model.reducedMotion is True


def test_live_partial_presence_excludes_waiting_placeholders(qapp, tmp_path):
    view_model, controller = _view_model(qapp, tmp_path)

    assert view_model.hasPartialText is False
    assert view_model.partialText == "等待你的聲音…"

    controller.snapshot = replace(controller.snapshot, partial_text="  即時片段  ")
    view_model.refresh()

    assert view_model.hasPartialText is True
    assert view_model.partialText == "即時片段"


def test_vocabulary_entries_refresh_sorted_by_count_then_term(qapp, tmp_path):
    view_model, controller = _view_model(qapp, tmp_path)
    controller.vocabulary_counts = {
        "詞二": 3,
        "低頻詞": 1,
        "詞一": 3,
        "忽略零次": 0,
        "": 9,
    }

    assert view_model.refreshVocabulary() == 3
    assert view_model.vocabularyEntries == [
        {"term": "詞一", "count": 3},
        {"term": "詞二", "count": 3},
        {"term": "低頻詞", "count": 1},
    ]

    controller.vocabulary_counts["最高頻"] = 5
    assert view_model.refreshVocabulary() == 4
    assert view_model.vocabularyEntries[0] == {"term": "最高頻", "count": 5}


def test_vocabulary_actions_delete_clear_and_toggle_learning(qapp):
    controller = _TimelineController()
    controller.vocabulary_counts = {"奇怪詞": 4, "保留詞": 2}
    view_model = JournalViewModel(controller, qapp)

    assert view_model.vocabularyLearningEnabled is True
    assert view_model.deleteVocabularyTerm("奇怪詞") is True
    assert controller.vocabulary_calls == [("delete", "奇怪詞")]
    assert view_model.vocabularyEntries == [{"term": "保留詞", "count": 2}]

    assert view_model.setVocabularyLearningEnabled(False) is True
    assert controller.vocabulary_calls[-1] == ("learning", False)
    assert controller.config.vocabulary_learning_enabled is False
    assert view_model.vocabularyLearningEnabled is False

    assert view_model.clearVocabulary() is True
    assert controller.vocabulary_calls[-1] == "clear"
    assert controller.vocabulary_counts == {}
    assert view_model.vocabularyEntries == []


def test_expanded_size_is_bounded_exposed_and_persisted(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    settings.setValue("expandedWidth", 1400)
    settings.setValue("expandedHeight", 900)
    controller = _TimelineController()
    view_model = JournalViewModel(controller, qapp, settings=settings)

    assert (view_model.expandedWidth, view_model.expandedHeight) == (1400, 900)
    view_model._expanded = True
    view_model.rememberExpandedSize(1500, 940)
    view_model.persistWindowState()

    assert int(settings.value("expandedWidth")) == 1500
    assert int(settings.value("expandedHeight")) == 940
    assert view_model._clamp_expanded_size(QSize(2000, 1200), QSize(1366, 768)) == QSize(
        1366, 768
    )
    assert view_model._clamp_expanded_size(QSize(400, 300), QSize(800, 600)) == QSize(
        EXPANDED_MIN_WIDTH, EXPANDED_MIN_HEIGHT
    )


def test_invalid_saved_expanded_size_falls_back_to_defaults(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    settings.setValue("expandedWidth", "invalid")
    settings.setValue("expandedHeight", 100)
    view_model = JournalViewModel(_TimelineController(), qapp, settings=settings)

    assert (view_model.expandedWidth, view_model.expandedHeight) == (
        EXPANDED_WIDTH,
        EXPANDED_HEIGHT,
    )


def test_local_font_catalog_discovers_ttf_and_otf_without_wheel_assets(
    tmp_path, monkeypatch
):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "ink.ttf").write_bytes(b"ttf")
    (font_dir / "brush.OTF").write_bytes(b"otf")
    (font_dir / "ignore.txt").write_text("not a font", encoding="utf-8")
    next_id = iter((11, 12))
    families = {11: ["Ink Family", "墨水體"], 12: ["Brush Family"]}
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.addApplicationFont",
        lambda _path: next(next_id),
    )
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.applicationFontFamilies",
        lambda font_id: families[font_id],
    )

    catalog = LocalFontCatalog((font_dir,))

    assert catalog.primary_directory == font_dir
    assert catalog.rescan() == ("Brush Family", "Ink Family")
    assert catalog.canonical_family("墨水體") == "Ink Family"


def test_local_font_catalog_removes_deleted_font_on_rescan(tmp_path, monkeypatch):
    font_file = tmp_path / "temporary.ttf"
    font_file.write_bytes(b"ttf")
    removed: list[int] = []
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.addApplicationFont",
        lambda _path: 21,
    )
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.applicationFontFamilies",
        lambda _font_id: ["Temporary Ink"],
    )
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.removeApplicationFont",
        lambda font_id: removed.append(font_id) or True,
    )
    catalog = LocalFontCatalog((tmp_path,))
    assert catalog.rescan() == ("Temporary Ink",)

    font_file.unlink()

    assert catalog.rescan() == ()
    assert removed == [21]


def test_view_model_restores_localized_family_alias_as_one_canonical_option(
    qapp, tmp_path, monkeypatch
):
    font_file = tmp_path / "localized.ttf"
    font_file.write_bytes(b"ttf")
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.addApplicationFont",
        lambda _path: 31,
    )
    monkeypatch.setattr(
        "auto_speech_journal.ui_models.QFontDatabase.applicationFontFamilies",
        lambda _font_id: ["Ink Family", "墨水體"],
    )
    controller = _TimelineController()
    controller.config = replace(controller.config, ui_font_family="墨水體")

    view_model = JournalViewModel(controller, qapp, font_directories=(tmp_path,))

    assert "Ink Family" in view_model.availableFontFamilies
    assert "墨水體" not in view_model.availableFontFamilies
    assert view_model.uiFontFamily == "Ink Family"


def test_appearance_choice_persists_and_updates_application_immediately(
    qapp, tmp_path
):
    view_model, controller = _view_model(qapp, tmp_path)
    system_font_family = view_model.systemFontFamily
    view_model._available_font_families = ("Readable Ink",)

    assert view_model.applyAppearance("Readable Ink", 24) is True

    assert controller.saved_configs[-1].ui_font_family == "Readable Ink"
    assert controller.saved_configs[-1].ui_font_size == 24
    assert view_model.uiFontFamily == "Readable Ink"
    assert view_model.uiFontSize == 24
    assert view_model.uiFontScale == 1.5
    assert view_model.systemFontFamily == system_font_family
    assert qapp.font().family() == "Readable Ink"
    assert qapp.font().pixelSize() == 24


def test_view_model_exposes_package_version(qapp, tmp_path) -> None:
    view_model, _controller = _view_model(qapp, tmp_path)

    assert view_model.appVersion == __version__


def test_appearance_rejects_unscanned_font_and_out_of_range_size(qapp, tmp_path):
    view_model, controller = _view_model(qapp, tmp_path)
    original = controller.config

    assert view_model.applyAppearance("Unknown Font", 20) is False
    view_model._available_font_families = (view_model.uiFontFamily,)
    assert view_model.applyAppearance(view_model.uiFontFamily, 27) is False

    assert controller.saved_configs == []
    assert controller.config == original


def test_exit_waits_when_recorder_has_not_reached_durable_storage(qapp, tmp_path):
    view_model, controller = _view_model(qapp, tmp_path)
    failures: list[str] = []
    view_model.actionFailed.connect(failures.append)
    controller.stop = lambda *, suppress_errors=False: False
    view_model.activate()

    view_model.exitApplication()

    assert view_model.allowClose is False
    assert view_model._stopping is False
    assert view_model._poll_timer.isActive()
    assert any("等待安全寫入" in message for message in failures)
    view_model._poll_timer.stop()
