from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_speech_journal.config import (
    AppConfig,
    DeviceFingerprint,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.controller import AUDIO_LEVEL_FLOOR_DBFS, JournalController
from auto_speech_journal.types import (
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


@dataclass
class FakeRecord:
    segment_id: str
    hour_key: str = "2026-07-12_09"
    display_text: str = ""


class FakeStorage:
    def __init__(self) -> None:
        self.records: dict[str, FakeRecord] = {}
        self.correct_calls: list[tuple[str, str, bool]] = []
        self.pending = 0

    def add_captured(self, segment: CapturedSegment) -> FakeRecord:
        record = FakeRecord(segment.segment_id, display_text=segment.preview_text)
        self.records[segment.segment_id] = record
        self.pending += 1
        return record

    def apply_final(self, result: FinalResult) -> FakeRecord:
        record = self.records[result.segment_id]
        if result.success:
            record.display_text = result.normalized_text
            self.pending = max(0, self.pending - 1)
        return record

    def get_segment(self, segment_id: str) -> FakeRecord:
        return self.records[segment_id]

    def correct_segment(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn_vocabulary: bool = True,
    ) -> FakeRecord:
        self.correct_calls.append((segment_id, corrected_text, learn_vocabulary))
        record = self.records[segment_id]
        record.display_text = corrected_text
        return record

    def mark_finalizing(self, segment_id: str) -> FakeRecord:
        return self.records[segment_id]

    def count_pending(self) -> int:
        return self.pending

    def list_segments(self, *, states=None, limit=None) -> list[FakeRecord]:
        del states
        records = list(self.records.values()) if self.pending else []
        return records if limit is None else records[:limit]

    def list_hours(self) -> list[str]:
        return sorted({record.hour_key for record in self.records.values()}, reverse=True)


class FakeExporter:
    def __init__(self) -> None:
        self.exported: list[str] = []
        self.rebuilt: list[str] = []
        self.deleted: list[str] = []
        self.deadlines: list[timedelta] = []
        self.fail_next_export = False
        self.delete_cleanup_failures: tuple[Path, ...] = ()
        self.pending_deletions: tuple[Path, ...] = ()
        self.deletion_retry_count = 0
        self.delete_segment_ids: tuple[str, ...] = ()

    def export_segment(self, segment_id: str) -> None:
        if self.fail_next_export:
            self.fail_next_export = False
            raise OSError("disk busy")
        self.exported.append(segment_id)

    def rebuild_hour(self, hour_key: str) -> None:
        self.rebuilt.append(hour_key)

    def delete_hour(self, hour_key: str) -> object:
        self.deleted.append(hour_key)
        return SimpleNamespace(
            deleted=SimpleNamespace(segment_ids=self.delete_segment_ids),
            audio_cleanup_failures=self.delete_cleanup_failures,
        )

    def export_due_provisionals(self, *, deadline: timedelta) -> list[object]:
        self.deadlines.append(deadline)
        return []

    def retry_pending_deletions(self) -> object:
        self.deletion_retry_count += 1
        return SimpleNamespace(
            completed_segment_ids=(),
            pending_paths=self.pending_deletions,
        )


class FakeWorkers:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.actions: list[str] = []
        self.hotword_updates: list[list[str]] = []
        self.submitted: list[CapturedSegment] = []
        self.input_selections: list[MicrophoneSelection] = []

    def start(self) -> None:
        self.actions.append("start")

    def pause(self) -> None:
        self.actions.append("pause")

    def resume(self) -> None:
        self.actions.append("resume")

    def reconfigure_input(
        self,
        selection: MicrophoneSelection,
        *,
        request_id: str | None = None,
    ) -> str:
        self.input_selections.append(selection)
        identifier = request_id or f"request-{len(self.input_selections)}"
        self.actions.append(f"reconfigure:{selection.mode.value}")
        return identifier

    def retry_preferred_input(self, *, request_id: str | None = None) -> str:
        self.actions.append("retry_preferred")
        return request_id or "retry-request"

    def submit(self, segment: CapturedSegment) -> bool:
        self.submitted.append(segment)
        return True

    def update_hotwords(self, hotwords: list[str]) -> None:
        self.hotword_updates.append(hotwords)

    def stop(self) -> None:
        self.actions.append("stop")

    def poll_events(self) -> list[object]:
        events, self.events = self.events, []
        return events


class StagedWorkers(FakeWorkers):
    def __init__(self, captured: CapturedSegment, final: FinalResult) -> None:
        super().__init__()
        self.captured = captured
        self.final = final
        self._pending_finalizations = 0

    def stop_recorder(self, timeout: float = 10.0) -> None:
        del timeout
        self.actions.append("stop_recorder")
        self.events.append(self.captured)

    @property
    def pending_finalizations(self) -> int:
        return self._pending_finalizations

    def stop_finalizer(self, timeout: float = 10.0) -> None:
        del timeout
        self.actions.append("stop_finalizer")

    def submit(self, segment: CapturedSegment) -> bool:
        accepted = super().submit(segment)
        self._pending_finalizations = 1
        self.events.append(self.final)
        return accepted

    def poll_events(self) -> list[object]:
        events = super().poll_events()
        if any(isinstance(event, FinalResult) for event in events):
            self._pending_finalizations = 0
        return events


class FakeVocabulary:
    def __init__(self, storage: FakeStorage) -> None:
        self.storage = storage
        self.calls: list[tuple[str, str, bool]] = []
        self.counts = {"專有名詞": 2}

    def apply_correction(
        self,
        segment_id: str,
        corrected_text: str,
        *,
        learn: bool = True,
    ) -> object:
        self.calls.append((segment_id, corrected_text, learn))
        record = self.storage.records[segment_id]
        record.display_text = corrected_text
        return SimpleNamespace(segment=record)

    def hotwords(self, *, minimum_count: int = 1) -> tuple[str, ...]:
        assert minimum_count == 2
        return tuple(term for term, count in self.counts.items() if count >= minimum_count)

    def term_counts(self) -> dict[str, int]:
        return dict(self.counts)

    def delete_term(self, term: str) -> bool:
        return self.counts.pop(term, None) is not None

    def clear(self) -> int:
        count = len(self.counts)
        self.counts.clear()
        return count


def make_controller(
    *,
    monotonic=lambda: 0.0,
    config: AppConfig | None = None,
    save_config_callback=None,
):
    storage = FakeStorage()
    exporter = FakeExporter()
    workers = FakeWorkers()
    vocabulary = FakeVocabulary(storage)
    controller = JournalController(
        storage=storage,
        exporter=exporter,
        workers=workers,
        config=config
        or AppConfig(
            final_deadline_ms=10_000,
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
        ),
        vocabulary=vocabulary,
        save_config_callback=save_config_callback,
        monotonic=monotonic,
    )
    return controller, storage, exporter, workers, vocabulary


def test_pending_and_skipped_start_without_constructing_workers() -> None:
    for mode in (MicrophoneMode.PENDING, MicrophoneMode.SKIPPED):
        created: list[AppConfig] = []
        controller = JournalController(
            storage=FakeStorage(),
            exporter=FakeExporter(),
            workers=None,
            workers_factory=lambda config, target=created: target.append(config)
            or FakeWorkers(),
            config=AppConfig(microphone=MicrophoneSelection(mode=mode)),
        )

        controller.start()

        assert created == []
        assert controller.snapshot.input_route.value == mode.value
        assert controller.snapshot.severity == Severity.WARNING


def test_configure_before_start_saves_then_lazy_factory_uses_selection() -> None:
    order: list[str] = []
    created: list[FakeWorkers] = []
    selected = MicrophoneSelection(
        mode=MicrophoneMode.FIXED,
        preferred_device=DeviceFingerprint(name="USB microphone"),
    )

    def factory(config: AppConfig) -> FakeWorkers:
        order.append(f"factory:{config.microphone.mode.value}")
        worker = FakeWorkers()
        created.append(worker)
        return worker

    controller = JournalController(
        storage=FakeStorage(),
        exporter=FakeExporter(),
        workers=None,
        workers_factory=factory,
        config=AppConfig(),
        save_config_callback=lambda config: order.append(
            f"save:{config.microphone.mode.value}"
        ),
    )

    assert controller.configure_microphone(selected) is None
    assert order == ["save:fixed"]
    controller.start()

    assert order == ["save:fixed", "factory:fixed"]
    assert created[0].actions == ["start"]
    assert controller.config.microphone == selected


def test_lazy_worker_start_failure_is_not_marked_started_and_same_selection_retries() -> None:
    class FailOnceWorkers(FakeWorkers):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 1

        def start(self) -> None:
            super().start()
            if self.failures:
                self.failures -= 1
                raise OSError("transient worker start failure")

    workers = FailOnceWorkers()
    saves: list[AppConfig] = []
    selected = MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(name="USB microphone"),
    )
    controller = JournalController(
        storage=FakeStorage(),
        exporter=FakeExporter(),
        workers=None,
        workers_factory=lambda _config: workers,
        config=AppConfig(),
        save_config_callback=saves.append,
    )
    controller.start()

    with pytest.raises(OSError, match="transient worker start failure"):
        controller.configure_microphone(selected)

    assert controller.config.microphone == selected
    assert not controller._workers_started
    assert workers.actions == ["start"]
    assert len(saves) == 1

    assert controller.configure_microphone(selected) is None

    assert controller._workers_started
    assert workers.actions == ["start", "start"]
    assert len(saves) == 1


def test_live_microphone_change_writes_history_before_reconfigure() -> None:
    order: list[str] = []

    class History:
        path = Path("history.jsonl")

        def append_change(self, _before, _after):
            order.append("history")
            return None

    class OrderedWorkers(FakeWorkers):
        def reconfigure_input(self, selection, *, request_id=None):
            order.append("reconfigure")
            return super().reconfigure_input(selection, request_id=request_id)

    workers = OrderedWorkers()
    controller = JournalController(
        storage=FakeStorage(),
        exporter=FakeExporter(),
        workers=workers,
        config=AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        ),
        save_config_callback=lambda _config: order.append("save"),
        settings_history_store=History(),
    )
    controller.start()

    request_id = controller.configure_microphone(
        MicrophoneSelection(
            mode=MicrophoneMode.FIXED,
            preferred_device=DeviceFingerprint(name="Desk microphone"),
        )
    )

    assert request_id == "request-1"
    assert order == ["save", "history", "reconfigure"]
    assert controller.snapshot.input_switching is True


def test_route_update_separates_preferred_from_active_and_retry() -> None:
    selected = MicrophoneSelection(
        mode=MicrophoneMode.FIXED,
        preferred_device=DeviceFingerprint(name="Preferred USB"),
    )
    controller, _, _, workers, _ = make_controller(config=AppConfig(microphone=selected))
    controller.start()

    controller.handle_event(
        InputRouteUpdate(
            request_id="switch-1",
            preferred_input_name="Preferred USB",
            active_input_name="Laptop microphone",
            input_route=InputRoute.FALLBACK,
            preferred_input_available=True,
            reason="using Windows default until manually retried",
        )
    )

    assert controller.snapshot.preferred_input_name == "Preferred USB"
    assert controller.snapshot.active_input_name == "Laptop microphone"
    assert controller.snapshot.input_route == InputRoute.FALLBACK
    assert controller.snapshot.preferred_input_available is True
    assert controller.snapshot.state == WorkerState.DEGRADED
    assert controller.retry_preferred_input() == "retry-request"
    assert workers.actions[-1] == "retry_preferred"
    assert controller.snapshot.input_switching is True


def test_worker_events_are_persisted_and_reflected_in_snapshot(tmp_path):
    controller, storage, exporter, workers, _ = make_controller()
    started = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    captured = CapturedSegment(
        segment_id="s1",
        audio_path=tmp_path / "s1.flac",
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=2),
        preview_text="預覽",
    )
    final = FinalResult(
        segment_id="s1",
        raw_text="最終",
        normalized_text="最終文字",
        engine_profile="cpu-int8",
    )
    workers.events.extend([captured, final])

    controller.start()
    assert controller.poll_workers() == 2

    assert storage.records["s1"].display_text == "最終文字"
    assert exporter.exported == ["s1"]
    assert [segment.segment_id for segment in workers.submitted] == ["s1"]
    assert controller.snapshot.final_text == "最終文字"
    assert controller.snapshot.current_hour_key == "2026-07-12_09"
    assert controller.snapshot.backlog == 0
    assert workers.hotword_updates == [["專有名詞"]]


def test_pause_resume_and_stop_delegate_to_workers():
    controller, _, _, workers, _ = make_controller()
    controller.start()
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-24.0,
            peak_dbfs=-12.0,
            speech_active=True,
            segment_id="active",
        )
    )

    controller.pause()
    assert controller.snapshot.paused is True
    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.peak_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.speech_active is False
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-20.0,
            peak_dbfs=-10.0,
            speech_active=True,
            segment_id="queued-after-pause",
        )
    )
    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    controller.resume()
    assert controller.snapshot.paused is False
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-22.0,
            peak_dbfs=-11.0,
            speech_active=True,
            segment_id="resumed",
        )
    )
    assert controller.snapshot.speech_active is True
    controller.stop()

    assert workers.actions == ["start", "pause", "resume", "stop"]
    assert controller.snapshot.state == WorkerState.STOPPED
    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.speech_active is False


def test_audio_levels_update_snapshot_and_new_segment_clears_previous_partial():
    controller, _, _, _, _ = make_controller()
    measured = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    controller.start()
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-30.0,
            peak_dbfs=-18.0,
            speech_active=True,
            segment_id="s1",
            measured_at_utc=measured,
        )
    )

    assert controller.snapshot.rms_dbfs == -30.0
    assert controller.snapshot.peak_dbfs == -18.0
    assert controller.snapshot.speech_active is True
    assert controller.snapshot.audio_segment_id == "s1"
    assert controller.snapshot.last_audio_at_utc == measured

    controller.handle_event(
        PartialUpdate(segment_id="s1", text="上一段預覽", started_at_utc=measured)
    )
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-28.0,
            peak_dbfs=-16.0,
            speech_active=True,
            segment_id="s1",
        )
    )
    assert controller.snapshot.partial_text == "上一段預覽"

    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-80.0,
            peak_dbfs=-70.0,
            speech_active=False,
            segment_id=None,
        )
    )
    assert controller.snapshot.partial_text == "上一段預覽"
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-26.0,
            peak_dbfs=-14.0,
            speech_active=True,
            segment_id="s2",
        )
    )

    assert controller.snapshot.partial_text == ""
    assert controller.snapshot.audio_segment_id == "s2"


def test_level_before_partial_clears_previous_segment_then_keeps_new_text():
    controller, _, _, _, _ = make_controller()
    measured = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    controller.start()
    controller.handle_event(
        PartialUpdate(segment_id="old", text="舊段預覽", started_at_utc=measured)
    )

    assert controller.snapshot.message == "正在錄音與預覽辨識"
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-30.0,
            peak_dbfs=-18.0,
            speech_active=True,
            segment_id="new",
        )
    )
    assert controller.snapshot.partial_text == ""

    controller.handle_event(
        PartialUpdate(segment_id="new", text="新段首字", started_at_utc=measured)
    )
    assert controller.snapshot.partial_text == "新段首字"


def test_partial_before_level_preserves_first_hypothesis_for_same_segment():
    controller, _, _, _, _ = make_controller()
    measured = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    controller.start()
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-40.0,
            peak_dbfs=-25.0,
            speech_active=False,
            segment_id="old",
        )
    )
    controller.handle_event(
        PartialUpdate(segment_id="new", text="不能消失的首字", started_at_utc=measured)
    )

    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-28.0,
            peak_dbfs=-16.0,
            speech_active=True,
            segment_id="new",
        )
    )

    assert controller.snapshot.audio_segment_id == "new"
    assert controller.snapshot.current_segment_id == "new"
    assert controller.snapshot.partial_text == "不能消失的首字"


@pytest.mark.parametrize("learning_enabled", [True, False])
def test_correction_uses_vocabulary_once_then_rebuilds_hour(
    learning_enabled: bool,
):
    controller, storage, exporter, workers, vocabulary = make_controller(
        config=AppConfig(
            final_deadline_ms=10_000,
            vocabulary_learning_enabled=learning_enabled,
        )
    )
    storage.records["s1"] = FakeRecord("s1", display_text="舊文字")

    updated = controller.correct_segment("s1", "  新名詞  ")

    assert updated.display_text == "新名詞"
    assert vocabulary.calls == [("s1", "新名詞", learning_enabled)]
    assert storage.correct_calls == []
    assert exporter.rebuilt == ["2026-07-12_09"]
    assert workers.hotword_updates == [["專有名詞"]]


def test_learned_vocabulary_lists_terms_and_counts():
    controller, _, _, _, vocabulary = make_controller()
    vocabulary.counts = {"正確名詞": 4, "另一個詞": 1}

    assert controller.learned_vocabulary() == {"正確名詞": 4, "另一個詞": 1}


def test_delete_vocabulary_term_removes_one_term_and_refreshes_hotwords():
    controller, _, _, workers, vocabulary = make_controller()
    vocabulary.counts = {"要刪的詞": 3, "保留的詞": 2}

    assert controller.delete_vocabulary_term("  要刪的詞  ") is True

    assert controller.learned_vocabulary() == {"保留的詞": 2}
    assert workers.hotword_updates == [["保留的詞"]]
    assert controller.delete_vocabulary_term("不存在") is False
    assert workers.hotword_updates == [["保留的詞"]]


def test_clear_vocabulary_removes_all_terms_and_clears_worker_hotwords():
    controller, _, _, workers, vocabulary = make_controller()
    vocabulary.counts = {"第一個詞": 3, "低頻詞": 1}

    assert controller.clear_vocabulary() == 2

    assert controller.learned_vocabulary() == {}
    assert workers.hotword_updates == [[]]


def test_vocabulary_learning_toggle_is_persisted(tmp_path: Path):
    saved: list[AppConfig] = []
    original = AppConfig(
        records_root=str(tmp_path / "records"),
        vocabulary_learning_enabled=True,
    )
    controller, _, _, _, _ = make_controller(
        config=original,
        save_config_callback=saved.append,
    )

    controller.set_vocabulary_learning_enabled(False)

    assert len(saved) == 1
    assert saved[0].vocabulary_learning_enabled is False
    assert controller.config.vocabulary_learning_enabled is False


def test_vocabulary_learning_toggle_failure_preserves_config(tmp_path: Path):
    attempted: list[AppConfig] = []

    def fail_to_save(config: AppConfig) -> None:
        attempted.append(config)
        raise OSError("config is locked")

    original = AppConfig(
        records_root=str(tmp_path / "records"),
        vocabulary_learning_enabled=True,
    )
    controller, _, _, _, _ = make_controller(
        config=original,
        save_config_callback=fail_to_save,
    )

    with pytest.raises(OSError, match="config is locked"):
        controller.set_vocabulary_learning_enabled(False)

    assert attempted[0].vocabulary_learning_enabled is False
    assert controller.config is original
    assert controller.config.vocabulary_learning_enabled is True


def test_delete_hour_goes_through_exporter_and_clears_current_snapshot():
    controller, _, exporter, workers, _ = make_controller()
    started = datetime(2026, 7, 12, tzinfo=UTC)
    controller.handle_event(
        CapturedSegment(
            segment_id="s1",
            audio_path=Path("s1.flac"),
            started_at_utc=started,
            ended_at_utc=started + timedelta(seconds=1),
            preview_text="內容",
        )
    )

    controller.delete_hour()

    assert exporter.deleted == ["2026-07-12_09"]
    assert workers.hotword_updates == [["專有名詞"]]
    assert controller.snapshot.current_segment_id is None
    assert controller.snapshot.current_hour_key is None


def test_tick_drains_events_and_throttles_deadline_exports():
    times = iter([0.0, 0.0, 0.5, 1.1])
    controller, _, exporter, workers, _ = make_controller(monotonic=lambda: next(times))
    controller.start()
    workers.events.append(
        WorkerStatus(
            worker=WorkerKind.RECORDER,
            state=WorkerState.RECORDING,
            message="錄音中",
        )
    )

    assert controller.tick() == 1
    assert controller.tick() == 0
    assert controller.tick() == 0
    assert exporter.deadlines == [timedelta(seconds=10), timedelta(seconds=10)]


def test_error_status_surfaces_in_snapshot():
    controller, _, _, _, _ = make_controller()
    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.FINALIZER,
            state=WorkerState.ERROR,
            message="模型載入失敗",
            severity=Severity.ERROR,
            queue_size=3,
        )
    )

    assert controller.snapshot.state == WorkerState.ERROR
    assert controller.snapshot.last_error == "模型載入失敗"
    assert controller.snapshot.backlog == 3


@pytest.mark.parametrize(
    ("state", "severity", "problem"),
    [
        (WorkerState.ERROR, Severity.ERROR, "GPU OOM，定稿將自動重試"),
        (WorkerState.DEGRADED, Severity.WARNING, "預覽佇列壅塞"),
    ],
)
def test_partial_update_does_not_hide_worker_problem(
    state: WorkerState,
    severity: Severity,
    problem: str,
):
    controller, _, _, _, _ = make_controller()
    controller.start()
    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.FINALIZER,
            state=state,
            message=problem,
            severity=severity,
        )
    )
    previous = controller.snapshot

    controller.handle_event(
        PartialUpdate(
            segment_id="still-previewing",
            text="即時首字",
            started_at_utc=datetime(2026, 7, 12, tzinfo=UTC),
        )
    )

    assert controller.snapshot.partial_text == "即時首字"
    assert controller.snapshot.current_segment_id == "still-previewing"
    assert controller.snapshot.state == previous.state
    assert controller.snapshot.severity == previous.severity
    assert controller.snapshot.message == previous.message
    assert controller.snapshot.last_error == previous.last_error


def test_finalizer_error_preserves_live_meter_but_recorder_error_resets_it():
    controller, _, _, _, _ = make_controller()
    controller.start()
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-32.0,
            peak_dbfs=-20.0,
            speech_active=True,
            segment_id="live",
        )
    )
    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.FINALIZER,
            state=WorkerState.ERROR,
            message="GPU 暫時不可用",
            severity=Severity.ERROR,
        )
    )

    assert controller.snapshot.state == WorkerState.ERROR
    assert controller.snapshot.rms_dbfs == -32.0
    assert controller.snapshot.speech_active is True
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-29.0,
            peak_dbfs=-17.0,
            speech_active=True,
            segment_id="live",
        )
    )
    assert controller.snapshot.rms_dbfs == -29.0
    assert controller.snapshot.last_error == "GPU 暫時不可用"

    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.RECORDER,
            state=WorkerState.ERROR,
            message="麥克風已中斷",
            severity=Severity.ERROR,
        )
    )

    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.peak_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.speech_active is False


@pytest.mark.parametrize(
    "state",
    [
        WorkerState.STARTING,
        WorkerState.READY,
        WorkerState.PAUSED,
        WorkerState.ERROR,
        WorkerState.STOPPED,
    ],
)
def test_inactive_recorder_status_resets_audio_level(state: WorkerState):
    controller, _, _, _, _ = make_controller()
    controller.start()
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-33.0,
            peak_dbfs=-19.0,
            speech_active=True,
            segment_id="active",
        )
    )

    controller.handle_event(
        WorkerStatus(worker=WorkerKind.RECORDER, state=state, message="not recording")
    )

    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.peak_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.speech_active is False


def test_stale_audio_level_resets_after_500ms_without_changing_error_priority():
    now = [0.0]
    controller, _, _, _, _ = make_controller(monotonic=lambda: now[0])
    controller.start()
    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.FINALIZER,
            state=WorkerState.ERROR,
            message="定稿積壓",
            severity=Severity.ERROR,
        )
    )
    controller.handle_event(
        AudioLevelUpdate(
            rms_dbfs=-35.0,
            peak_dbfs=-21.0,
            speech_active=True,
            segment_id="stale",
        )
    )

    now[0] = 0.499
    controller.tick()
    assert controller.snapshot.rms_dbfs == -35.0

    now[0] = 0.5
    controller.tick()

    assert controller.snapshot.rms_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.peak_dbfs == AUDIO_LEVEL_FLOOR_DBFS
    assert controller.snapshot.speech_active is False
    assert controller.snapshot.audio_segment_id == "stale"
    assert controller.snapshot.state == WorkerState.ERROR
    assert controller.snapshot.severity == Severity.ERROR
    assert controller.snapshot.last_error == "定稿積壓"


def test_finalizer_ready_does_not_hide_recorder_recording_state():
    controller, _, _, _, _ = make_controller()
    controller.handle_event(
        WorkerStatus(worker=WorkerKind.RECORDER, state=WorkerState.RECORDING)
    )
    controller.handle_event(
        WorkerStatus(worker=WorkerKind.FINALIZER, state=WorkerState.READY)
    )
    controller.handle_event(
        WorkerStatus(worker=WorkerKind.PREVIEW, state=WorkerState.READY)
    )

    assert controller.snapshot.state == WorkerState.RECORDING


def test_failed_markdown_export_is_retried_without_losing_final_text(tmp_path):
    times = iter([0.0, 1.1])
    controller, _, exporter, workers, _ = make_controller(monotonic=lambda: next(times))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    controller.start()
    controller.handle_event(
        CapturedSegment(
            segment_id="retry-export",
            audio_path=tmp_path / "retry-export.flac",
            started_at_utc=started,
            ended_at_utc=started + timedelta(seconds=1),
            preview_text="預覽",
        )
    )
    exporter.fail_next_export = True
    controller.handle_event(
        FinalResult(
            segment_id="retry-export",
            raw_text="最終",
            normalized_text="最終",
            engine_profile="cpu-int8",
        )
    )

    assert controller.snapshot.final_text == "最終"
    assert controller.snapshot.severity == Severity.ERROR
    assert workers.submitted[0].segment_id == "retry-export"

    controller.tick()

    assert exporter.rebuilt == ["2026-07-12_09"]
    assert controller.snapshot.last_error is None


def test_transient_final_storage_failure_resubmits_finalizing_segment(tmp_path):
    class FlakyStorage(FakeStorage):
        def __init__(self) -> None:
            super().__init__()
            self.captured_segments: dict[str, CapturedSegment] = {}
            self.fail_final_once = True

        def add_captured(self, segment: CapturedSegment) -> FakeRecord:
            self.captured_segments[segment.segment_id] = segment
            return super().add_captured(segment)

        def apply_final(self, result: FinalResult) -> FakeRecord:
            if self.fail_final_once:
                self.fail_final_once = False
                raise OSError("transient SQLite write failure")
            return super().apply_final(result)

        def list_segments(self, *, states=None, limit=None) -> list[object]:
            del states
            records = [
                SimpleNamespace(
                    segment_id=segment.segment_id,
                    audio_path=segment.audio_path,
                    started_at_utc=segment.started_at_utc,
                    ended_at_utc=segment.ended_at_utc,
                    provisional_text=segment.preview_text,
                    provisional_raw=segment.preview_raw_text,
                    sample_rate=segment.sample_rate,
                    duration_ms=segment.duration_ms,
                    leading_overlap_ms=segment.leading_overlap_ms,
                    previous_segment_id=segment.previous_segment_id,
                )
                for segment in self.captured_segments.values()
            ]
            return records if limit is None else records[:limit]

    now = [0.0]
    storage = FlakyStorage()
    exporter = FakeExporter()
    workers = FakeWorkers()
    controller = JournalController(
        storage=storage,
        exporter=exporter,
        workers=workers,
        config=AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        ),
        monotonic=lambda: now[0],
    )
    segment = CapturedSegment(
        segment_id="retry-final-storage",
        audio_path=tmp_path / "retry-final-storage.flac",
        started_at_utc=datetime(2026, 7, 12, tzinfo=UTC),
        ended_at_utc=datetime(2026, 7, 12, tzinfo=UTC) + timedelta(seconds=1),
        preview_text="可讀預覽",
    )
    controller.start()
    controller.handle_event(segment)

    controller.handle_event(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="最終",
            normalized_text="最終",
            engine_profile="cuda:int8_float16",
        )
    )
    assert len(workers.submitted) == 1

    now[0] = 6.0
    controller.tick()

    assert [item.segment_id for item in workers.submitted] == [
        segment.segment_id,
        segment.segment_id,
    ]


def test_partial_only_segment_is_not_correctable():
    controller, _, _, _, _ = make_controller()
    controller.handle_event(
        PartialUpdate(
            segment_id="not-persisted",
            text="只有預覽",
            started_at_utc=datetime(2026, 7, 12, tzinfo=UTC),
        )
    )

    assert controller.snapshot.correctable_segment_id is None
    with pytest.raises(LookupError):
        controller.correct_current("不可寫入")


def test_staged_shutdown_drains_captured_and_final_events(tmp_path):
    started = datetime(2026, 7, 12, tzinfo=UTC)
    captured = CapturedSegment(
        segment_id="shutdown-segment",
        audio_path=tmp_path / "shutdown.flac",
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=1),
        preview_text="關閉前預覽",
    )
    final = FinalResult(
        segment_id="shutdown-segment",
        raw_text="關閉前最終",
        normalized_text="關閉前最終",
        engine_profile="cpu-int8",
    )
    storage = FakeStorage()
    exporter = FakeExporter()
    workers = StagedWorkers(captured, final)
    controller = JournalController(
        storage=storage,
        exporter=exporter,
        workers=workers,
        config=AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        ),
        sleep=lambda _seconds: None,
    )
    controller.start()

    controller.stop()

    assert workers.actions == ["start", "stop_recorder", "stop_finalizer"]
    assert storage.records["shutdown-segment"].display_text == "關閉前最終"
    assert exporter.exported == ["shutdown-segment"]


def test_staged_shutdown_can_retry_without_abandoning_recorder() -> None:
    class PendingShutdownWorkers(FakeWorkers):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        @property
        def pending_finalizations(self) -> int:
            return 0

        def stop_recorder(self, timeout: float = 10.0) -> None:
            del timeout
            self.actions.append("stop_recorder")
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("captured audio is not durable yet")

        def stop_finalizer(self, timeout: float = 10.0) -> None:
            del timeout
            self.actions.append("stop_finalizer")

    workers = PendingShutdownWorkers()
    controller = JournalController(
        storage=FakeStorage(),
        exporter=FakeExporter(),
        workers=workers,
        config=AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        ),
    )
    controller.start()

    assert controller.stop(suppress_errors=True) is False
    assert controller.workers_started is True
    assert controller.snapshot.state is WorkerState.ERROR
    assert workers.actions == ["start", "stop_recorder"]

    assert controller.stop(suppress_errors=True) is True
    assert controller.workers_started is False
    assert workers.actions == [
        "start",
        "stop_recorder",
        "stop_recorder",
        "stop_finalizer",
    ]


def test_delete_cleanup_failure_remains_red_until_retry_succeeds(tmp_path):
    times = iter([0.0, 1.1])
    controller, _, exporter, _, _ = make_controller(monotonic=lambda: next(times))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    controller.handle_event(
        CapturedSegment(
            segment_id="delete-pending",
            audio_path=tmp_path / "locked.flac",
            started_at_utc=started,
            ended_at_utc=started + timedelta(seconds=1),
        )
    )
    locked = tmp_path / "locked.flac"
    exporter.delete_cleanup_failures = (locked,)
    exporter.pending_deletions = (locked,)

    controller.delete_hour()
    assert controller.snapshot.severity == Severity.ERROR
    assert "仍被占用" in controller.snapshot.message

    controller.tick()
    assert controller.snapshot.severity == Severity.ERROR
    exporter.pending_deletions = ()
    controller.tick()

    assert controller.snapshot.last_error is None
    assert exporter.deletion_retry_count == 2


def test_settings_require_absolute_records_root_and_switch_only_after_restart(tmp_path):
    storage = FakeStorage()
    exporter = FakeExporter()
    workers = FakeWorkers()
    opened: list[Path] = []
    saved: list[AppConfig] = []
    active_root = (tmp_path / "active").resolve()
    controller = JournalController(
        storage=storage,
        exporter=exporter,
        workers=workers,
        config=AppConfig(records_root=str(active_root)),
        save_config_callback=saved.append,
        folder_opener=opened.append,
    )

    with pytest.raises(ValueError, match="絕對路徑"):
        controller.update_settings(AppConfig(records_root="relative/path"))

    next_root = (tmp_path / "next").resolve()
    controller.update_settings(AppConfig(records_root=str(next_root)))
    controller.open_records_folder()

    assert saved[-1].records_root == str(next_root)
    assert opened == [active_root]
    assert "下次啟動" in controller.snapshot.message


def test_ui_error_is_surfaced_in_red_main_window_state() -> None:
    controller, _, _, _, _ = make_controller()

    controller.report_ui_error("設定路徑無效")

    assert controller.snapshot.state == WorkerState.DEGRADED
    assert controller.snapshot.severity == Severity.ERROR
    assert controller.snapshot.last_error == "設定路徑無效"


def test_timeline_for_date_groups_durable_segments_and_maps_statuses() -> None:
    started = datetime(2026, 7, 12, tzinfo=UTC)
    records = [
        SimpleNamespace(
            segment_id="provisional",
            hour_key="2026-07-12_08",
            started_at_utc=started,
            display_text="暫定文字",
            state=SegmentState.FINALIZING,
            final_text="",
            user_locked=False,
            last_error=None,
        ),
        SimpleNamespace(
            segment_id="final",
            hour_key="2026-07-12_08",
            started_at_utc=started + timedelta(minutes=1),
            display_text="最終文字",
            state=SegmentState.AUDIO_DELETED,
            final_text="最終文字",
            user_locked=False,
            last_error=None,
        ),
        SimpleNamespace(
            segment_id="corrected",
            hour_key="2026-07-12_09",
            started_at_utc=started + timedelta(hours=1),
            display_text="修正文字",
            state=SegmentState.FINAL_READY,
            final_text="模型文字",
            user_locked=True,
            last_error=None,
        ),
        SimpleNamespace(
            segment_id="retry",
            hour_key="2026-07-12_09",
            started_at_utc=started + timedelta(hours=1, minutes=1),
            display_text="可讀預覽",
            state=SegmentState.RETRY,
            final_text="",
            user_locked=False,
            last_error="暫時失敗",
        ),
        SimpleNamespace(
            segment_id="failed",
            hour_key="2026-07-12_09",
            started_at_utc=started + timedelta(hours=1, minutes=2),
            display_text="",
            state=SegmentState.FAILED,
            final_text="",
            user_locked=False,
            last_error="永久失敗",
        ),
        SimpleNamespace(
            segment_id="empty-final",
            hour_key="2026-07-12_09",
            started_at_utc=started + timedelta(hours=1, minutes=3),
            display_text="",
            state=SegmentState.AUDIO_DELETED,
            final_text="",
            user_locked=False,
            last_error=None,
        ),
    ]

    class TimelineStorage(FakeStorage):
        timezone_name = "Asia/Taipei"

        def list_day_segments(self, day_key: str) -> list[object]:
            assert day_key == "2026-07-12"
            return records

    controller = JournalController(
        storage=TimelineStorage(),
        exporter=FakeExporter(),
        workers=FakeWorkers(),
        config=AppConfig(),
    )

    timeline = controller.timeline_for_date(date(2026, 7, 12))
    segments = [segment for hour in timeline.hours for segment in hour.segments]

    assert timeline.day_key == "2026-07-12"
    assert [(hour.hour_key, hour.label) for hour in timeline.hours] == [
        ("2026-07-12_08", "08:00"),
        ("2026-07-12_09", "09:00"),
    ]
    assert [segment.time_label for segment in segments] == [
        "[08:00:00]",
        "[08:01:00]",
        "[09:00:00]",
        "[09:01:00]",
        "[09:02:00]",
    ]
    assert [segment.status_label for segment in segments] == [
        "待定稿",
        "已定稿",
        "已修正",
        "重試中",
        "失敗",
    ]
    assert [segment.editable for segment in segments] == [True, True, True, True, False]
    assert segments[-2].last_error == "暫時失敗"


def test_timeline_for_date_supports_storage_fakes_without_day_query(tmp_path: Path) -> None:
    controller, storage, _, _, _ = make_controller()
    storage.add_captured(
        CapturedSegment(
            segment_id="fallback",
            audio_path=tmp_path / "fallback.flac",
            started_at_utc=datetime(2026, 7, 12, tzinfo=UTC),
            ended_at_utc=datetime(2026, 7, 12, 0, 0, 1, tzinfo=UTC),
            preview_text="相容資料",
        )
    )

    timeline = controller.timeline_for_date(date(2026, 7, 12))

    assert timeline.hours[0].segments[0].segment_id == "fallback"
    assert timeline.hours[0].segments[0].status_label == "待定稿"


def test_timeline_preserves_microsecond_order_for_segments_in_the_same_second() -> None:
    started = datetime(2026, 7, 12, 0, 0, 0, 100, tzinfo=UTC)
    records = [
        SimpleNamespace(
            segment_id="a-earlier-uuid-sort",
            hour_key="2026-07-12_08",
            started_at_utc=started + timedelta(microseconds=200),
            display_text="較晚",
            state=SegmentState.AUDIO_DELETED,
            final_text="較晚",
            user_locked=False,
            last_error=None,
        ),
        SimpleNamespace(
            segment_id="z-later-uuid-sort",
            hour_key="2026-07-12_08",
            started_at_utc=started,
            display_text="較早",
            state=SegmentState.AUDIO_DELETED,
            final_text="較早",
            user_locked=False,
            last_error=None,
        ),
    ]

    class TimelineStorage(FakeStorage):
        timezone_name = "Asia/Taipei"

        def list_day_segments(self, day_key: str) -> list[object]:
            return records

    controller = JournalController(
        storage=TimelineStorage(),
        exporter=FakeExporter(),
        workers=FakeWorkers(),
        config=AppConfig(),
    )

    timeline = controller.timeline_for_date(date(2026, 7, 12))

    assert [segment.text for segment in timeline.hours[0].segments] == ["較早", "較晚"]


def test_timeline_revision_only_changes_for_durable_timeline_mutations(
    tmp_path: Path,
) -> None:
    controller, _, exporter, _, _ = make_controller()
    started = datetime(2026, 7, 12, tzinfo=UTC)
    controller.start()

    controller.report_ui_message("一般狀態更新")
    controller.handle_event(PartialUpdate("revision", "預覽", started))
    assert controller.snapshot.timeline_revision == 0

    controller.handle_event(
        CapturedSegment(
            segment_id="revision",
            audio_path=tmp_path / "revision.flac",
            started_at_utc=started,
            ended_at_utc=started + timedelta(seconds=1),
            preview_text="預覽",
        )
    )
    assert controller.snapshot.timeline_revision == 1

    controller.handle_event(
        WorkerStatus(
            worker=WorkerKind.RECORDER,
            state=WorkerState.RECORDING,
            message="錄音中",
        )
    )
    assert controller.snapshot.timeline_revision == 1

    controller.handle_event(
        FinalResult(
            segment_id="revision",
            raw_text="最終",
            normalized_text="最終",
            engine_profile="cpu-int8",
        )
    )
    assert controller.snapshot.timeline_revision == 2

    controller.correct_segment("revision", "使用者修正")
    assert controller.snapshot.timeline_revision == 3

    exporter.delete_segment_ids = ("revision",)
    controller.delete_hour("2026-07-12_09")
    assert controller.snapshot.timeline_revision == 4


def test_retrying_recovered_durable_segment_advances_timeline_revision(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 12, tzinfo=UTC)
    record = SimpleNamespace(
        segment_id="recovered",
        audio_path=tmp_path / "recovered.flac",
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=1),
        provisional_text="已復原預覽",
        provisional_raw="",
        sample_rate=16_000,
        duration_ms=1_000,
        leading_overlap_ms=0,
        previous_segment_id=None,
        state=SegmentState.RETRY,
    )

    class RecoveryStorage(FakeStorage):
        def __init__(self) -> None:
            super().__init__()
            self.pending = 1

        def list_segments(self, *, states=None, limit=None) -> list[object]:
            del states, limit
            return [record]

        def mark_finalizing(self, segment_id: str) -> object:
            assert segment_id == record.segment_id
            record.state = SegmentState.FINALIZING
            return record

    controller = JournalController(
        storage=RecoveryStorage(),
        exporter=FakeExporter(),
        workers=FakeWorkers(),
        config=AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT)
        ),
    )

    controller.start()

    assert record.state == SegmentState.FINALIZING
    assert controller.snapshot.timeline_revision == 1
