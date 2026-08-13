from __future__ import annotations

import os
import queue
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from auto_speech_journal.audio import AudioChunk, RecoveredFlac, SpeechAudio, SpoolLimitExceeded
from auto_speech_journal.config import (
    AppConfig,
    DeviceFingerprint,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.preview_engine import PreviewHypothesis
from auto_speech_journal.types import (
    AudioLevelUpdate,
    CapturedSegment,
    FinalResult,
    InputRoute,
    InputRouteRequest,
    InputRouteUpdate,
    PartialUpdate,
    PreviewAudioChunk,
    PreviewEndpointResult,
    PreviewFinalize,
    WorkerCommand,
    WorkerCommandKind,
    WorkerKind,
    WorkerState,
    WorkerStatus,
)
from auto_speech_journal.workers import (
    JournalWorkers,
    RecorderShutdownPending,
    _audio_levels_dbfs,
    _CaptureCandidate,
    _emit,
    _finalizer_loop,
    _InputRouteResolution,
    _preview_loop,
    _recorder_loop,
    _WindowsKillOnCloseJob,
    probe_realtime_models,
)


def test_audio_levels_dbfs_are_finite_and_clamped() -> None:
    rms, peak = _audio_levels_dbfs(np.array([0.0, 1.0, -1.0, 0.0], np.float32))
    silent_rms, silent_peak = _audio_levels_dbfs(np.zeros(1_600, np.float32))
    clipped_rms, clipped_peak = _audio_levels_dbfs(np.array([2.0, -2.0], np.float32))

    assert rms == pytest.approx(-3.0103, abs=0.001)
    assert peak == 0.0
    assert (silent_rms, silent_peak) == (-120.0, -120.0)
    assert (clipped_rms, clipped_peak) == (0.0, 0.0)


def test_replaceable_audio_level_drops_instead_of_blocking_full_queue() -> None:
    events = queue.Queue(maxsize=1)
    events.put(object())
    level = AudioLevelUpdate(-30, -10, False)

    assert not _emit(events, level, replaceable=True)
    assert events.get_nowait() is not level


class FakeVad:
    is_speech_detected = False

    def accept(self, samples):
        return [SpeechAudio(samples, 0, len(samples))]

    def flush(self):
        return []

    def reset(self):
        return None


class Vad:
    is_speech_detected = True

    def accept(self, samples):
        return [SpeechAudio(samples, 0, len(samples))]

    def flush(self):
        return []

    def reset(self):
        return None


class Preview:
    def warmup(self):
        return None

    def accept(self, *_args, **_kwargs):
        return PreviewHypothesis("預覽", False, True)

    def finish(self):
        return PreviewHypothesis("預覽", True, False)

    def reset(self):
        return None

    def close(self):
        return None


class NoSpeechPreview:
    def warmup(self):
        return None

    def finish(self):
        return PreviewHypothesis("", True, False)

    def reset(self):
        return None

    def close(self):
        return None


class NoSpeechVad:
    is_speech_detected = False

    def accept(self, _samples):
        return []

    def flush(self):
        return []

    def reset(self):
        return None


class NoSpeechSpool:
    usage_ratio = 0.0

    @staticmethod
    def can_reserve(_bytes):
        return True


def test_recorder_reconfigure_persists_segment_before_stopping_old_input(tmp_path) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    preferred_a = DeviceFingerprint(name="Input A", endpoint_id="a")
    preferred_b = DeviceFingerprint(name="Input B", endpoint_id="b")
    selection_a = MicrophoneSelection(MicrophoneMode.FIXED, preferred_a)
    selection_b = MicrophoneSelection(MicrophoneMode.FIXED, preferred_b)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    log: list[str] = []
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        def __init__(self, fingerprint):
            self.name = fingerprint.name
            self.running = False
            self.reads = 0

        def start(self):
            self.running = True
            log.append(f"start:{self.name}")
            return SimpleNamespace(name=self.name, index=1)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.name == "Input A" and self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            if self.name == "Input A" and self.reads == 2:
                commands.put(
                    WorkerCommand(
                        WorkerCommandKind.RECONFIGURE_INPUT,
                        InputRouteRequest("switch-to-b", selection_b),
                    )
                )
            elif self.name == "Input B":
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            log.append(f"stop:{self.name}")
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("speech", False, True)

        def finish(self):
            return PreviewHypothesis("speech", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True

        def __init__(self):
            self.samples = None

        def accept(self, samples):
            self.samples = samples
            return []

        def flush(self):
            if self.samples is None:
                return []
            return [SpeechAudio(self.samples, 0, len(self.samples))]

        def reset(self):
            self.samples = None

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            del sample_rate
            log.append("spool")
            path = tmp_path / f"{segment_id}.flac"
            path.write_bytes(b"audio")
            return path

    def resolve(selection, *, include_preferred=True):
        assert include_preferred is True
        fingerprint = selection.preferred_device
        assert fingerprint is not None
        return _InputRouteResolution(
            (_CaptureCandidate(fingerprint, InputRoute.PREFERRED, fingerprint.name),),
            fingerprint.name,
            True,
        )

    config = AppConfig(
        records_root=str(tmp_path / "records"),
        microphone=selection_a,
    )
    _recorder_loop(
        config,
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=Capture,
        route_resolver=resolve,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=12,
    )

    assert "spool" in log, log
    assert log.index("spool") < log.index("stop:Input A") < log.index("start:Input B")
    route_updates = [event for event in events.queue if isinstance(event, InputRouteUpdate)]
    assert any(
        event.request_id == "switch-to-b"
        and event.active_input_name == "Input B"
        and not event.input_switching
        for event in route_updates
    )


def test_fixed_input_open_failure_uses_default_without_losing_preference(tmp_path) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    preferred = DeviceFingerprint(name="Preferred", endpoint_id="preferred")
    fallback = DeviceFingerprint(name="Windows default", endpoint_id="default")
    selection = MicrophoneSelection(MicrophoneMode.FIXED, preferred)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    starts: list[str] = []

    class Capture:
        running = False

        def __init__(self, fingerprint):
            self.name = fingerprint.name

        def start(self):
            starts.append(self.name)
            if self.name == "Preferred":
                raise OSError("busy")
            self.running = True
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            return SimpleNamespace(name=self.name, index=2)

        def stop(self):
            self.running = False

        def read(self, timeout):
            assert timeout == 0.1
            raise queue.Empty

    def resolve(_selection, *, include_preferred=True):
        assert include_preferred is True
        return _InputRouteResolution(
            (
                _CaptureCandidate(preferred, InputRoute.PREFERRED, preferred.name),
                _CaptureCandidate(fallback, InputRoute.FALLBACK, fallback.name),
            ),
            preferred.name,
            True,
        )

    _recorder_loop(
        AppConfig(records_root=str(tmp_path / "records"), microphone=selection),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=Capture,
        route_resolver=resolve,
        preview_factory=lambda: SimpleNamespace(
            warmup=lambda: None,
            finish=lambda: PreviewHypothesis("", True, False),
            reset=lambda: None,
            close=lambda: None,
        ),
        vad_factory=lambda: (
            SimpleNamespace(flush=lambda: [], reset=lambda: None),
            None,
        ),
        spool_factory=lambda: SimpleNamespace(usage_ratio=0.0),
        max_iterations=6,
    )

    assert starts[:2] == ["Preferred", "Windows default"]
    fallback_update = next(
        event
        for event in events.queue
        if isinstance(event, InputRouteUpdate) and event.input_route == InputRoute.FALLBACK
    )
    assert fallback_update.preferred_input_name == "Preferred"
    assert fallback_update.active_input_name == "Windows default"
    assert fallback_update.preferred_input_available is True
    assert "could not be opened" in (fallback_update.reason or "")


def test_system_default_change_is_switched_at_tracking_boundary(tmp_path) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    input_a = DeviceFingerprint(name="Default A", endpoint_id="default-a")
    input_b = DeviceFingerprint(name="Default B", endpoint_id="default-b")
    resolver_calls = [0]
    log: list[str] = []

    def resolve(_selection, *, include_preferred=True):
        assert include_preferred is True
        resolver_calls[0] += 1
        fingerprint = input_a if resolver_calls[0] <= 2 else input_b
        return _InputRouteResolution(
            (
                _CaptureCandidate(
                    fingerprint,
                    InputRoute.SYSTEM_DEFAULT,
                    fingerprint.name,
                ),
            ),
            None,
            True,
        )

    class Capture:
        running = False

        def __init__(self, fingerprint):
            self.name = fingerprint.name

        def start(self):
            self.running = True
            log.append(f"start:{self.name}")
            return SimpleNamespace(name=self.name, index=1)

        def read(self, timeout):
            assert timeout == 0.1
            if self.name == "Default B":
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False
            log.append(f"stop:{self.name}")

    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        AppConfig(
            records_root=str(tmp_path / "records"),
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
        ),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=Capture,
        route_resolver=resolve,
        preview_factory=NoSpeechPreview,
        vad_factory=lambda: (NoSpeechVad(), None),
        spool_factory=NoSpeechSpool,
        monotonic=monotonic,
        max_iterations=10,
    )

    assert log.index("start:Default A") < log.index("stop:Default A")
    assert log.index("stop:Default A") < log.index("start:Default B")
    assert any(
        isinstance(event, InputRouteUpdate)
        and event.input_route == InputRoute.SYSTEM_DEFAULT
        and event.active_input_name == "Default B"
        for event in events.queue
    )


def test_paused_reconfigure_defers_open_until_resume(tmp_path) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    input_a = DeviceFingerprint(name="Input A", endpoint_id="a")
    input_b = DeviceFingerprint(name="Input B", endpoint_id="b")
    selection_a = MicrophoneSelection(MicrophoneMode.FIXED, input_a)
    selection_b = MicrophoneSelection(MicrophoneMode.FIXED, input_b)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    log: list[str] = []

    def resolve(selection, *, include_preferred=True):
        assert include_preferred is True
        fingerprint = selection.preferred_device
        assert fingerprint is not None
        return _InputRouteResolution(
            (_CaptureCandidate(fingerprint, InputRoute.PREFERRED, fingerprint.name),),
            fingerprint.name,
            True,
        )

    class Capture:
        running = False

        def __init__(self, fingerprint):
            self.name = fingerprint.name
            self.reads = 0

        def start(self):
            self.running = True
            log.append(f"start:{self.name}")
            return SimpleNamespace(name=self.name, index=1)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.name == "Input A" and self.reads == 1:
                commands.put(WorkerCommand(WorkerCommandKind.PAUSE))
                commands.put(
                    WorkerCommand(
                        WorkerCommandKind.RECONFIGURE_INPUT,
                        InputRouteRequest("paused-switch", selection_b),
                    )
                )
            elif self.name == "Input B":
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False
            log.append(f"stop:{self.name}")

    clock = [0]

    def monotonic():
        clock[0] += 1
        if clock[0] == 3:
            log.append("resume-enqueued")
            commands.put(WorkerCommand(WorkerCommandKind.RESUME))
        return float(clock[0] * 3)

    _recorder_loop(
        AppConfig(records_root=str(tmp_path / "records"), microphone=selection_a),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=Capture,
        route_resolver=resolve,
        preview_factory=NoSpeechPreview,
        vad_factory=lambda: (NoSpeechVad(), None),
        spool_factory=NoSpeechSpool,
        monotonic=monotonic,
        max_iterations=12,
    )

    assert log.index("stop:Input A") < log.index("resume-enqueued")
    assert log.index("resume-enqueued") < log.index("start:Input B")
    assert any(
        isinstance(event, InputRouteUpdate)
        and event.request_id == "paused-switch"
        and event.active_input_name is None
        and not event.input_switching
        for event in events.queue
    )


def test_fallback_preferred_recovery_only_updates_availability(tmp_path) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    preferred = DeviceFingerprint(name="Preferred", endpoint_id="preferred")
    fallback = DeviceFingerprint(name="Fallback", endpoint_id="fallback")
    selection = MicrophoneSelection(MicrophoneMode.FIXED, preferred)
    starts: list[str] = []

    def resolve(_selection, *, include_preferred=True):
        return _InputRouteResolution(
            (_CaptureCandidate(fallback, InputRoute.FALLBACK, fallback.name),),
            preferred.name,
            not include_preferred,
            "preferred microphone is temporarily unavailable" if include_preferred else None,
        )

    class Capture:
        running = False

        def __init__(self, fingerprint):
            self.name = fingerprint.name
            self.reads = 0

        def start(self):
            self.running = True
            starts.append(self.name)
            return SimpleNamespace(name=self.name, index=1)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads >= 2:
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        AppConfig(records_root=str(tmp_path / "records"), microphone=selection),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=Capture,
        route_resolver=resolve,
        preview_factory=NoSpeechPreview,
        vad_factory=lambda: (NoSpeechVad(), None),
        spool_factory=NoSpeechSpool,
        monotonic=monotonic,
        max_iterations=10,
    )

    assert starts == ["Fallback"]
    assert any(
        isinstance(event, InputRouteUpdate)
        and event.input_route == InputRoute.FALLBACK
        and event.preferred_input_available
        and event.active_input_name == "Fallback"
        and "will not be selected automatically" in (event.reason or "")
        for event in events.queue
    )


def test_journal_workers_input_request_is_idempotent_and_restart_uses_latest(
    tmp_path,
    paths,
) -> None:
    context = FakeContext()
    original = MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(name="Input A", endpoint_id="a"),
    )
    updated = MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(name="Input B", endpoint_id="b"),
    )
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root), microphone=original),
        paths,
        context=context,
        restart_backoff_seconds=0,
        max_restart_backoff_seconds=0,
    )
    workers.start()

    assert workers.reconfigure_input(updated, request_id="same-request") == "same-request"
    assert workers.reconfigure_input(updated, request_id="same-request") == "same-request"
    commands = list(workers._recorder_commands.queue)
    assert [item.kind for item in commands].count(WorkerCommandKind.RECONFIGURE_INPUT) == 1

    old_recorder = workers._recorder
    old_recorder.die(1)
    workers.poll_events()

    assert workers._recorder is not old_recorder
    assert workers._recorder.args[0].microphone == updated
    workers.stop()


def test_recorder_duplicate_pending_route_request_is_not_terminally_acked(tmp_path, paths) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    selected = MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(name="Input B", endpoint_id="b"),
    )
    request = InputRouteRequest("switch-b", selected)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    commands.put(WorkerCommand(WorkerCommandKind.RECONFIGURE_INPUT, request))
    commands.put(WorkerCommand(WorkerCommandKind.RECONFIGURE_INPUT, request))

    class Capture:
        def __init__(self, fingerprint):
            self.name = fingerprint.name
            self.running = False

        def start(self):
            self.running = True
            return SimpleNamespace(name=self.name, index=1)

        def read(self, timeout):
            assert timeout == 0.1
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=NoSpeechPreview,
        vad_factory=lambda: (NoSpeechVad(), None),
        spool_factory=NoSpeechSpool,
        max_iterations=5,
    )

    updates = [
        event
        for event in events.queue
        if isinstance(event, InputRouteUpdate) and event.request_id == "switch-b"
    ]
    assert [update.input_switching for update in updates] == [True, True, False]


def captured(tmp_path: Path, segment_id: str = "segment-1") -> CapturedSegment:
    started = datetime(2026, 7, 12, tzinfo=UTC)
    audio = tmp_path / f"{segment_id}.flac"
    audio.write_bytes(b"audio")
    return CapturedSegment(
        segment_id=segment_id,
        audio_path=audio,
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=1),
        preview_text="預覽",
        duration_ms=1_000,
    )


class FakeProcess:
    def __init__(self, *, name, target, args):
        self.name = name
        self.target = target
        self.args = args
        self.exitcode = None
        self._alive = False

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, _timeout=None):
        self._alive = False
        if self.exitcode is None:
            self.exitcode = 0

    def terminate(self):
        self._alive = False
        self.exitcode = -15

    def die(self, code=1):
        self._alive = False
        self.exitcode = code


class FakeContext:
    def __init__(self):
        self.processes = []

    @staticmethod
    def Queue(maxsize):
        return queue.Queue(maxsize=maxsize)

    def Process(self, **kwargs):
        process = FakeProcess(**kwargs)
        self.processes.append(process)
        return process


def test_start_rolls_back_partial_spawn_and_can_retry(tmp_path, paths):
    class FailOnceContext(FakeContext):
        def __init__(self):
            super().__init__()
            self.failed = False

        def Process(self, **kwargs):
            process = super().Process(**kwargs)
            original_start = process.start

            def start():
                if process.name == "speech-journal-finalizer" and not self.failed:
                    self.failed = True
                    raise OSError("transient spawn failure")
                original_start()

            process.start = start
            return process

    context = FailOnceContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
    )

    with pytest.raises(OSError, match="transient spawn failure"):
        workers.start()

    assert not workers._started
    assert workers._preview is None
    assert workers._recorder is None
    assert workers._finalizer is None
    assert not any(process.is_alive() for process in context.processes[:3])

    workers.start()

    assert workers.running
    assert [process.name for process in context.processes[3:]] == [
        "speech-journal-preview",
        "speech-journal-recorder",
        "speech-journal-finalizer",
    ]
    assert [
        command.kind for command in list(workers._recorder_commands.queue)
    ] == [WorkerCommandKind.START]
    workers.stop()


def test_input_request_retries_backpressure_and_recorder_restart_until_ack(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(
            records_root=str(paths.records_root),
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
        ),
        paths,
        context=context,
        command_queue_size=1,
        restart_backoff_seconds=0,
        max_restart_backoff_seconds=0,
    )
    workers.start()
    selected = MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(name="Input B", endpoint_id="b"),
    )

    assert workers.reconfigure_input(selected, request_id="switch-b") == "switch-b"
    assert "switch-b" not in workers._sent_input_request_ids
    assert workers._recorder_commands.get_nowait().kind == WorkerCommandKind.START

    workers.poll_events()
    first_delivery = workers._recorder_commands.get_nowait()
    assert first_delivery.kind == WorkerCommandKind.RECONFIGURE_INPUT
    assert first_delivery.payload == InputRouteRequest("switch-b", selected)

    workers._recorder.die(1)
    workers.poll_events()
    assert workers._recorder_commands.get_nowait().kind == WorkerCommandKind.START
    workers.poll_events()
    replay = workers._recorder_commands.get_nowait()
    assert replay.kind == WorkerCommandKind.RECONFIGURE_INPUT
    assert replay.payload == InputRouteRequest("switch-b", selected)

    workers._events.put(
        InputRouteUpdate(
            request_id="switch-b",
            preferred_input_name="Input B",
            active_input_name="Input B",
            input_route=InputRoute.PREFERRED,
        )
    )
    workers.poll_events()

    assert "switch-b" in workers._acked_input_request_ids
    assert workers._recorder_commands.empty()
    assert workers.reconfigure_input(selected, request_id="switch-b") == "switch-b"
    assert workers._recorder_commands.empty()
    workers.stop()


def test_journal_workers_uses_bounded_finalizer_queue(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        command_queue_size=4,
        finalizer_queue_size=1,
    )
    workers.start()

    assert workers.submit(captured(tmp_path, "one"))
    assert not workers.submit(captured(tmp_path, "two"))
    events = workers.poll_events()

    assert any(
        isinstance(event, WorkerStatus)
        and event.state == WorkerState.DEGRADED
        and "queue is full" in event.message
        for event in events
    )
    workers.stop()


def test_finalizer_loop_emits_result_and_status(tmp_path, paths):
    segment = captured(tmp_path)
    inputs = queue.Queue()
    events = queue.Queue()
    inputs.put(segment)
    inputs.put(WorkerCommand(WorkerCommandKind.STOP))

    class FakeEngine:
        last_fallback_reason = None
        last_deadline_exceeded = False

        def transcribe(self, received):
            assert received == segment
            return FinalResult(
                segment_id=received.segment_id,
                raw_text="文字",
                normalized_text="文字",
                engine_profile="fake",
            )

        def update_hotwords(self, _hotwords):
            return None

        def close(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _finalizer_loop(config, paths, inputs, events, engine_factory=FakeEngine)
    emitted = list(events.queue)

    assert any(isinstance(event, FinalResult) for event in emitted)
    assert [
        event.state for event in emitted if isinstance(event, WorkerStatus)
    ] == [WorkerState.STARTING, WorkerState.READY, WorkerState.STOPPED]


def test_finalizer_status_recovers_after_a_normal_result(tmp_path, paths):
    first = captured(tmp_path, "first")
    second = captured(tmp_path, "second")
    inputs = queue.Queue()
    events = queue.Queue()
    inputs.put(first)
    inputs.put(second)
    inputs.put(WorkerCommand(WorkerCommandKind.STOP))

    class RecoveringEngine:
        last_fallback_reason = None
        last_deadline_exceeded = False
        last_normalization_error = None
        count = 0

        def transcribe(self, segment):
            self.count += 1
            return FinalResult(
                segment_id=segment.segment_id,
                raw_text="" if self.count == 1 else "恢復",
                normalized_text=segment.preview_text if self.count == 1 else "恢復",
                engine_profile="fake",
                success=self.count != 1,
                error="temporary" if self.count == 1 else None,
            )

        def update_hotwords(self, _hotwords):
            return None

        def close(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _finalizer_loop(config, paths, inputs, events, engine_factory=RecoveringEngine)
    statuses = [event for event in events.queue if isinstance(event, WorkerStatus)]

    assert any(event.state == WorkerState.DEGRADED for event in statuses)
    assert any(
        event.state == WorkerState.READY and "recovered" in event.message for event in statuses
    )


def test_recorder_loop_spools_completed_vad_segment(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class FakeCapture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake mic", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class FakePreview:
        def warmup(self):
            return None

        def accept(self, _samples, *, sample_rate):
            assert sample_rate == 16_000
            return PreviewHypothesis("預覽", False, True)

        def finish(self):
            return PreviewHypothesis("完成", True, True)

        def reset(self):
            return None

        def update_hotwords(self, _hotwords):
            return False

        def close(self):
            return None

    class FakeSpool:
        usage_ratio = 0.81

        def write(self, _samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=FakeCapture,
        preview_factory=FakePreview,
        vad_factory=lambda: (FakeVad(), None),
        spool_factory=FakeSpool,
        max_iterations=10,
    )

    emitted = list(events.queue)
    segments = [event for event in emitted if isinstance(event, CapturedSegment)]
    assert len(segments) == 1
    assert segments[0].preview_text == "完成"
    assert str(uuid.UUID(segments[0].segment_id)) == segments[0].segment_id
    assert any(
        isinstance(event, WorkerStatus)
        and event.metadata.get("spool_ratio") == 0.81
        and event.severity.value == "error"
        for event in emitted
    )


def test_recorder_stop_flushes_active_speech_to_spool(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class StopAfterChunkCapture:
        running = False

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            return AudioChunk(np.ones(1_600, np.float32), 16_000, started)

        def stop(self):
            self.running = False

    class ActivePreview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("尚未完成", False, True)

        def finish(self):
            return PreviewHypothesis("關機前完成", True, True)

        def reset(self):
            return None

        def close(self):
            return None

    class ActiveVad:
        is_speech_detected = True

        def __init__(self):
            self.samples = None

        def accept(self, samples):
            self.samples = samples
            return []

        def flush(self):
            if self.samples is None:
                return []
            return [SpeechAudio(self.samples, 0, len(self.samples))]

        def reset(self):
            return None

    class FakeSpool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=StopAfterChunkCapture,
        preview_factory=ActivePreview,
        vad_factory=lambda: (ActiveVad(), None),
        spool_factory=FakeSpool,
        max_iterations=5,
    )
    segments = [event for event in events.queue if isinstance(event, CapturedSegment)]

    assert len(segments) == 1
    assert segments[0].preview_text == "關機前完成"


def test_microphone_failure_flushes_active_speech_before_reconnect(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    order: list[str] = []

    class FailingCapture:
        running = False
        reads = 0
        starts = 0

        def start(self):
            self.running = True
            self.starts += 1
            order.append(f"start-{self.starts}")
            if self.starts > 1:
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            if self.reads == 2:
                raise RuntimeError("device removed")
            raise queue.Empty

        def stop(self):
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("中斷前", False, True)

        def finish(self):
            return PreviewHypothesis("中斷前", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True

        def __init__(self):
            self.samples = None
            self.flushes = 0

        def accept(self, samples):
            self.samples = samples
            return []

        def flush(self):
            self.flushes += 1
            order.append(f"flush-{self.flushes}")
            if self.flushes == 1:
                raise OSError("temporary VAD flush failure")
            if self.samples is None:
                return []
            return [SpeechAudio(self.samples, 0, len(self.samples))]

        def reset(self):
            self.samples = None

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            return tmp_path / f"{segment_id}.flac"

    now = [0.0]

    def monotonic():
        now[0] += 3.0
        return now[0]

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=FailingCapture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        monotonic=monotonic,
        max_iterations=8,
    )
    segments = [event for event in events.queue if isinstance(event, CapturedSegment)]

    assert len(segments) == 1
    assert order.index("flush-2") < order.index("start-2")
    assert segments[0].preview_text == "中斷前"


def test_pause_retries_failed_vad_flush_before_leaving_capture_running(tmp_path):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False

        def __init__(self):
            self.reads = 0
            self.stops = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            commands.put(WorkerCommand(WorkerCommandKind.PAUSE))
            return AudioChunk(np.ones(1_600, np.float32), 16_000, started)

        def stop(self):
            self.stops += 1
            self.running = False

    class Vad:
        is_speech_detected = False

        def __init__(self):
            self.flushes = 0

        def accept(self, _samples):
            return []

        def flush(self):
            self.flushes += 1
            if self.flushes == 1:
                raise OSError("temporary VAD flush failure")
            return []

        def reset(self):
            return None

    capture = Capture()
    _recorder_loop(
        AppConfig(records_root=str(tmp_path / "records")),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        capture_factory=lambda: capture,
        preview_factory=NoSpeechPreview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=NoSpeechSpool,
        max_iterations=4,
    )

    assert capture.reads == 1
    assert capture.stops == 1
    assert any(
        isinstance(event, WorkerStatus) and event.state == WorkerState.PAUSED
        for event in events.queue
    )


def test_sleep_gap_flushes_streaming_state_before_new_audio(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    order: list[str] = []

    class GappedCapture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            return AudioChunk(
                np.full(1_600, 2.0, np.float32),
                16_000,
                started + timedelta(seconds=5.2),
            )

        def stop(self):
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("語音", False, True)

        def finish(self):
            return PreviewHypothesis("語音", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True

        def __init__(self):
            self.samples = []

        def accept(self, samples):
            self.samples.append(samples.copy())
            if float(samples[0]) == 2.0:
                order.append("accept-second")
                commands.put(WorkerCommand(WorkerCommandKind.STOP))
            return []

        def flush(self):
            if not self.samples:
                return []
            values = np.concatenate(self.samples)
            return [SpeechAudio(values, 0, len(values))]

        def reset(self):
            self.samples = []

    class Spool:
        usage_ratio = 0.0

        def __init__(self):
            self.writes = 0

        def write(self, _samples, *, sample_rate, segment_id):
            del sample_rate
            self.writes += 1
            order.append(f"spool-{self.writes}")
            if self.writes == 1:
                raise OSError("temporary spool failure")
            path = tmp_path / f"{segment_id}.flac"
            path.write_bytes(b"audio")
            return path

    config = AppConfig(records_root=str(tmp_path / "records"))
    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=GappedCapture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        monotonic=monotonic,
        max_iterations=12,
    )
    emitted = list(events.queue)
    segments = [event for event in emitted if isinstance(event, CapturedSegment)]

    assert len(segments) == 2
    assert order.index("spool-2") < order.index("accept-second")
    assert segments[1].started_at_utc - segments[0].started_at_utc > timedelta(seconds=5)
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("sleep_gap_seconds", 0) > 5
        for event in emitted
    )


def test_spool_hard_limit_stops_recorder_in_error(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class FakeCapture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=1)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class FakePreview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("預覽", False, True)

        def finish(self):
            return PreviewHypothesis("預覽", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class FullSpool:
        usage_ratio = 1.0

        def write(self, *_args, **_kwargs):
            raise SpoolLimitExceeded("full")

    capture_backend = FakeCapture()
    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=lambda: capture_backend,
        preview_factory=FakePreview,
        vad_factory=lambda: (FakeVad(), None),
        spool_factory=FullSpool,
        max_iterations=5,
    )

    statuses = [event for event in events.queue if isinstance(event, WorkerStatus)]
    assert any(
        event.state == WorkerState.ERROR and event.metadata.get("spool_hard_limit")
        for event in statuses
    ), statuses
    assert capture_backend.running is False


def test_recorder_status_recovers_after_clean_audio(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class RecoveringCapture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=1)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(
                    np.zeros(1_600, np.float32),
                    16_000,
                    started,
                    dropped_frames=160,
                )
            if self.reads == 2:
                return AudioChunk(np.zeros(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class QuietPreview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("", False, False)

        def finish(self):
            return PreviewHypothesis("", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class QuietVad:
        is_speech_detected = False

        def accept(self, _samples):
            return []

        def flush(self):
            return []

        def reset(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=RecoveringCapture,
        preview_factory=QuietPreview,
        vad_factory=lambda: (QuietVad(), None),
        spool_factory=lambda: SimpleNamespace(usage_ratio=0.0),
        max_iterations=10,
    )
    statuses = [event for event in events.queue if isinstance(event, WorkerStatus)]

    assert any(event.state == WorkerState.DEGRADED for event in statuses)
    assert any(
        event.state == WorkerState.RECORDING and "recovered" in event.message
        for event in statuses
    )


def test_preview_loop_emits_raw_partial_and_endpoint_result(tmp_path, paths):
    started = datetime(2026, 7, 12, tzinfo=UTC)
    segment = captured(tmp_path, "preview-segment")
    inputs = queue.Queue()
    events = queue.Queue()
    inputs.put(
        PreviewAudioChunk(
            segment_id=segment.segment_id,
            samples=np.ones(1_600, np.float32),
            sample_rate=16_000,
            segment_started_at_utc=started,
        )
    )
    inputs.put(PreviewFinalize(segment))
    inputs.put(WorkerCommand(WorkerCommandKind.STOP))

    class Preview:
        def warmup(self):
            return None

        def accept(self, _samples, *, sample_rate):
            assert sample_rate == 16_000
            return PreviewHypothesis("簡體預覽", False, True, "简体预览")

        def finish(self):
            return PreviewHypothesis("簡體完成", True, True, "简体完成")

        def reset(self):
            return None

        def update_hotwords(self, _hotwords):
            return False

        def close(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _preview_loop(config, paths, inputs, events, engine_factory=Preview)
    emitted = list(events.queue)
    partial = next(
        event
        for event in emitted
        if hasattr(event, "raw_text")
        and not isinstance(event, PreviewEndpointResult)
    )
    endpoint = next(event for event in emitted if isinstance(event, PreviewEndpointResult))

    assert partial.text == "簡體預覽"
    assert partial.raw_text == "简体预览"
    assert endpoint.normalized_text == "簡體完成"
    assert endpoint.raw_text == "简体完成"


def test_preview_loop_emits_first_text_immediately_and_retains_throttled_change(
    tmp_path: Path, paths) -> None:
    started = datetime(2026, 7, 12, tzinfo=UTC)
    segment = captured(tmp_path, "preview-throttle")
    inputs = queue.Queue()
    events = queue.Queue()
    for _ in range(3):
        inputs.put(
            PreviewAudioChunk(
                segment_id=segment.segment_id,
                samples=np.ones(1_600, np.float32),
                sample_rate=16_000,
                segment_started_at_utc=started,
            )
        )
    inputs.put(PreviewFinalize(segment))
    inputs.put(WorkerCommand(WorkerCommandKind.STOP))
    clock = {"now": 0.0}

    class Preview:
        count = 0

        def warmup(self):
            return None

        def accept(self, _samples, *, sample_rate):
            assert sample_rate == 16_000
            timeline = [(0.0, "甲", True), (0.1, "甲乙", True), (0.4, "甲乙", False)]
            now, text, changed = timeline[self.count]
            self.count += 1
            clock["now"] = now
            return PreviewHypothesis(text, False, changed)

        def finish(self):
            return PreviewHypothesis("甲乙", True, False)

        def reset(self):
            return None

        def update_hotwords(self, _hotwords):
            return False

        def close(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _preview_loop(
        config,
        paths,
        inputs,
        events,
        engine_factory=Preview,
        monotonic=lambda: clock["now"],
    )

    partials = [event for event in events.queue if isinstance(event, PartialUpdate)]
    assert [event.text for event in partials[:2]] == ["甲", "甲乙"]
    assert partials[0].emitted_at_utc >= partials[0].started_at_utc


def test_inline_preview_uses_the_same_first_and_pending_update_policy(
    tmp_path: Path, paths
) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    clock = {"now": 0.0}

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads <= 3:
                return AudioChunk(
                    np.ones(1_600, np.float32),
                    16_000,
                    started + timedelta(milliseconds=100 * (self.reads - 1)),
                )
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Preview:
        count = 0

        def warmup(self):
            return None

        def accept(self, _samples, *, sample_rate):
            assert sample_rate == 16_000
            timeline = [(0.0, "甲", True), (0.1, "甲乙", True), (0.4, "甲乙", False)]
            now, text, changed = timeline[self.count]
            self.count += 1
            clock["now"] = now
            return PreviewHypothesis(text, False, changed)

        def finish(self):
            return PreviewHypothesis("甲乙", True, False)

        def reset(self):
            return None

        def update_hotwords(self, _hotwords):
            return False

        def close(self):
            return None

    class Vad:
        is_speech_detected = True
        samples = []

        def accept(self, samples):
            self.samples.append(samples.copy())
            return []

        def flush(self):
            values = np.concatenate(self.samples)
            return [SpeechAudio(values, 0, len(values))]

        def reset(self):
            self.samples = []

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            return tmp_path / f"{segment_id}.flac"

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        monotonic=lambda: clock["now"],
        max_iterations=10,
    )

    partials = [event for event in events.queue if isinstance(event, PartialUpdate)]
    assert [event.text for event in partials] == ["甲", "甲乙"]


def test_recorder_sends_exact_preview_preroll_without_repeating_current_chunk(
    tmp_path: Path, paths) -> None:
    commands = queue.Queue()
    events = queue.Queue()
    previews = queue.Queue(maxsize=8)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads <= 4:
                return AudioChunk(
                    np.full(1_600, self.reads / 10, np.float32),
                    16_000,
                    started + timedelta(milliseconds=100 * (self.reads - 1)),
                )
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Vad:
        is_speech_detected = False
        chunks = []

        def accept(self, samples):
            self.chunks.append(samples.copy())
            if len(self.chunks) == 4:
                self.is_speech_detected = True
            return []

        def flush(self):
            values = np.concatenate(self.chunks)
            return [SpeechAudio(values, 0, len(values))]

        def reset(self):
            self.is_speech_detected = False
            self.chunks = []

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            return tmp_path / f"{segment_id}.flac"

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        previews,
        capture_factory=Capture,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=10,
    )

    audio_messages = [
        item for item in previews.queue if isinstance(item, PreviewAudioChunk)
    ]
    assert [float(item.samples[0]) for item in audio_messages] == pytest.approx(
        [0.2, 0.3, 0.4]
    )
    assert sum(float(item.samples[0]) == pytest.approx(0.4) for item in audio_messages) == 1
    levels = [event for event in events.queue if isinstance(event, AudioLevelUpdate)]
    assert len(levels) == 4
    assert levels[-1].speech_active and levels[-1].segment_id


def test_production_recorder_enqueues_preview_without_decoding_inline(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    previews = queue.Queue(maxsize=4)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        previews,
        capture_factory=Capture,
        preview_factory=lambda: (_ for _ in ()).throw(AssertionError("inline ASR")),
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=5,
    )

    preview_messages = list(previews.queue)
    emitted = list(events.queue)
    assert any(isinstance(event, CapturedSegment) for event in emitted), "\n".join(
        repr(event) for event in emitted
    )
    segment = next(event for event in emitted if isinstance(event, CapturedSegment))
    assert isinstance(preview_messages[0], PreviewAudioChunk)
    assert isinstance(preview_messages[1], PreviewFinalize)
    assert segment.preview_pending
    assert not any(hasattr(event, "text") for event in events.queue)


def test_production_recorder_stop_flushes_vad_and_preview_in_order(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    previews = queue.Queue(maxsize=4)
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            return AudioChunk(np.ones(1_600, np.float32), 16_000, started)

        def stop(self):
            self.running = False

    class Vad:
        is_speech_detected = True
        samples = None

        def accept(self, samples):
            self.samples = samples
            return []

        def flush(self):
            return [SpeechAudio(self.samples, 0, len(self.samples))]

        def reset(self):
            return None

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        previews,
        capture_factory=Capture,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=4,
    )
    messages = list(previews.queue)
    segments = [event for event in events.queue if isinstance(event, CapturedSegment)]

    assert [type(message) for message in messages] == [
        PreviewAudioChunk,
        PreviewFinalize,
    ]
    assert len(segments) == 1 and segments[0].preview_pending


def test_preview_backpressure_never_drops_vad_or_flac(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    previews = queue.Queue(maxsize=1)
    previews.put(object())
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)
    writes = []

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Spool:
        usage_ratio = 0.0

        def write(self, samples, *, sample_rate, segment_id):
            writes.append((len(samples), sample_rate, segment_id))
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        previews,
        capture_factory=Capture,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=5,
    )
    emitted = list(events.queue)
    segment = next(event for event in emitted if isinstance(event, CapturedSegment))

    assert writes and writes[0][0] == 1_600
    assert not segment.preview_pending
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("preview_backpressure")
        for event in emitted
    )


def test_generic_spool_failure_retries_before_publishing_capture(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0
        stop_injected = False

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            raise queue.Empty

        def stop(self):
            self.running = False
            if self.reads and not self.stop_injected:
                self.stop_injected = True
                commands.put(WorkerCommand(WorkerCommandKind.STOP))

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("可讀預覽", False, True, "可读预览")

        def finish(self):
            return PreviewHypothesis("可讀預覽", True, False, "可读预览")

        def reset(self):
            return None

        def close(self):
            return None

    class BrokenSpool:
        usage_ratio = 0.0
        writes = 0

        def write(self, *_args, segment_id, **_kwargs):
            self.writes += 1
            if self.writes == 1:
                raise OSError("simulated fsync failure")
            path = paths.spool_dir / f"{segment_id}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fLaC-recovered")
            return path

    ticks = iter((0.0, 0.0, 3.0, 3.0, 6.0, 6.0, 9.0, 9.0))

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=BrokenSpool,
        monotonic=lambda: next(ticks, 12.0),
        max_iterations=5,
    )
    emitted = list(events.queue)
    segment = next(event for event in emitted if isinstance(event, CapturedSegment))

    assert segment.started_at_utc == started
    assert segment.ended_at_utc == started + timedelta(milliseconds=100)
    assert segment.audio_path == paths.spool_dir / f"{segment.segment_id}.flac"
    assert segment.audio_path.is_file()
    assert segment.preview_text == "可讀預覽"
    assert segment.preview_raw_text == "可读预览"
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("spool_write_failed")
        for event in emitted
    )
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("spool_write_recovered")
        for event in emitted
    )


def test_stop_flush_drains_transient_spool_failure_before_worker_exit(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("關閉預覽", False, True)

        def finish(self):
            return PreviewHypothesis("關閉預覽", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True
        buffered = np.ones(1_600, np.float32)

        def accept(self, _samples):
            return []

        def flush(self):
            values, self.buffered = self.buffered, np.empty(0, np.float32)
            return [SpeechAudio(values, 0, len(values))] if len(values) else []

        def reset(self):
            return None

    class TransientSpool:
        usage_ratio = 0.0
        writes = 0

        def write(self, _samples, *, sample_rate, segment_id):
            self.writes += 1
            if self.writes == 1:
                raise OSError("transient close-time write failure")
            path = paths.spool_dir / f"{segment_id}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fLaC-close-recovery")
            return path

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=TransientSpool,
        max_iterations=8,
    )

    captured_events = [event for event in events.queue if isinstance(event, CapturedSegment)]
    assert len(captured_events) == 1, [
        (type(event).__name__, getattr(event, "message", ""), getattr(event, "metadata", {}))
        for event in events.queue
    ]
    assert captured_events[0].audio_path.is_file()


def test_retry_validates_all_recovered_partials_and_rewrites_truncated_one(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            raise queue.Empty

        def stop(self):
            self.running = False

    class Vad:
        is_speech_detected = True

        def accept(self, samples):
            return [
                SpeechAudio(samples[:800], 0, 800, forced_endpoint=True),
                SpeechAudio(samples[800:], 800, 1_600),
            ]

        def flush(self):
            return []

        def reset(self):
            return None

    class RecoveringSpool:
        usage_ratio = 0.0

        def __init__(self):
            self.initial: list[tuple[str, int, int]] = []
            self.recovered = False
            self.rewrites: list[str] = []

        def write(self, samples, *, sample_rate, segment_id):
            if len(self.initial) < 2:
                self.initial.append((segment_id, len(samples), sample_rate))
                raise OSError("simulated partial write")
            self.rewrites.append(segment_id)
            path = paths.spool_dir / f"{segment_id}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fLaC-full-rewrite")
            return path

        def recover_partials(self):
            if self.recovered:
                return []
            self.recovered = True
            recovered = []
            for index, (segment_id, frames, sample_rate) in enumerate(self.initial):
                path = paths.spool_dir / f"{segment_id}.flac"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fLaC-partial")
                recovered.append(
                    RecoveredFlac(
                        segment_id,
                        path,
                        sample_rate,
                        frames if index == 0 else frames - 10,
                    )
                )
            return recovered

    spool = RecoveringSpool()
    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=lambda: spool,
        monotonic=monotonic,
        max_iterations=6,
    )

    captured_events = [event for event in events.queue if isinstance(event, CapturedSegment)]
    assert len(captured_events) == 2, [
        (type(event).__name__, getattr(event, "message", ""), getattr(event, "metadata", {}))
        for event in events.queue
    ]
    assert all(event.audio_path.is_file() for event in captured_events)
    assert spool.rewrites == [spool.initial[1][0]]
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("partial_length_mismatch")
        for event in events.queue
    )
    assert not list(paths.spool_dir.glob(".*.partial.flac"))


def test_failed_truncated_rewrite_keeps_recoverable_prefix_for_restart(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            raise queue.Empty

        def stop(self):
            self.running = False

    class FailingRewriteSpool:
        usage_ratio = 0.0

        def __init__(self):
            self.segment_id = ""
            self.recovered = False

        def write(self, _samples, *, sample_rate, segment_id):
            self.segment_id = segment_id
            raise OSError("disk remains full")

        def recover_partials(self):
            if self.recovered or not self.segment_id:
                return []
            self.recovered = True
            path = paths.spool_dir / f"{self.segment_id}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"decodable-prefix")
            return [RecoveredFlac(self.segment_id, path, 16_000, 800)]

    clock = [0.0]

    def monotonic():
        clock[0] += 3.0
        return clock[0]

    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=FailingRewriteSpool,
        monotonic=monotonic,
        max_iterations=4,
    )

    assert not any(isinstance(event, CapturedSegment) for event in events.queue)
    backups = list(paths.spool_dir.glob(".*.partial.flac"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"decodable-prefix"


def test_permanent_spool_failure_never_publishes_missing_final_path(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, 3, 4, 5, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, _timeout):
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(1_600, np.float32), 16_000, started)
            raise queue.Empty

        def stop(self):
            self.running = False

    class BrokenSpool:
        usage_ratio = 0.0

        def write(self, *_args, **_kwargs):
            raise OSError("disk remains full")

    ticks = iter((0.0, 0.0, 3.0, 6.0, 9.0, 12.0, 15.0))
    _recorder_loop(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=BrokenSpool,
        monotonic=lambda: next(ticks, 18.0),
        max_iterations=4,
    )

    assert not any(isinstance(event, CapturedSegment) for event in events.queue)


def test_spool_warning_status_recovers_below_threshold(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads <= 2:
                return AudioChunk(
                    np.ones(1_600, np.float32),
                    16_000,
                    started + timedelta(milliseconds=100 * (self.reads - 1)),
                )
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("文字", False, True)

        def finish(self):
            return PreviewHypothesis("文字", True, False)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True
        cursor = 0

        def accept(self, samples):
            start = self.cursor
            self.cursor += len(samples)
            return [SpeechAudio(samples, start, self.cursor)]

        def flush(self):
            return []

        def reset(self):
            return None

    class Spool:
        writes = 0

        @property
        def usage_ratio(self):
            return (0.0, 0.81, 0.5)[self.writes]

        def write(self, _samples, *, sample_rate, segment_id):
            self.writes += 1
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=7,
    )
    statuses = [event for event in events.queue if isinstance(event, WorkerStatus)]

    assert sum("audio spool is" in status.message for status in statuses) == 1
    assert any(
        status.state == WorkerState.RECORDING
        and "spool usage recovered" in status.message
        for status in statuses
    )


def test_forced_split_carries_overlap_chain_metadata(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    started = datetime(2026, 7, 12, tzinfo=UTC)

    class Capture:
        running = False
        reads = 0

        def start(self):
            self.running = True
            return SimpleNamespace(name="fake", index=44)

        def read(self, timeout):
            assert timeout == 0.1
            self.reads += 1
            if self.reads == 1:
                return AudioChunk(np.ones(2_200, np.float32), 16_000, started)
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
            raise queue.Empty

        def stop(self):
            self.running = False

    class Preview:
        def warmup(self):
            return None

        def accept(self, *_args, **_kwargs):
            return PreviewHypothesis("預覽", False, True)

        def finish(self):
            return PreviewHypothesis("完成", True, True)

        def reset(self):
            return None

        def close(self):
            return None

    class Vad:
        is_speech_detected = True

        def accept(self, samples):
            return [
                SpeechAudio(samples[:1_600], 0, 1_600, forced_endpoint=True),
                SpeechAudio(samples[1_200:], 1_200, 2_200),
            ]

        def flush(self):
            return []

        def reset(self):
            return None

    class Spool:
        usage_ratio = 0.0

        def write(self, _samples, *, sample_rate, segment_id):
            return tmp_path / f"{segment_id}.flac"

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=Capture,
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=Spool,
        max_iterations=5,
    )
    segments = [event for event in events.queue if isinstance(event, CapturedSegment)]

    assert len(segments) == 2
    assert segments[1].previous_segment_id == segments[0].segment_id
    assert segments[1].leading_overlap_ms == 25


def test_preview_result_is_joined_into_captured_segment_before_publish(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        finalizer_queue_size=4,
    )
    workers.start()
    segment = captured(tmp_path, "joined-preview")
    workers._events.put(replace(segment, preview_pending=True, preview_text=""))
    workers._events.put(
        PreviewEndpointResult(
            segment_id=segment.segment_id,
            raw_text="简体结果",
            normalized_text="簡體結果",
        )
    )

    events = workers.poll_events()
    published = next(event for event in events if isinstance(event, CapturedSegment))
    assert published.preview_text == "簡體結果"
    assert published.preview_raw_text == "简体结果"
    assert not published.preview_pending
    assert workers.pending_finalizations == 1
    queued_before_ack = workers._finalizer_inputs.qsize()
    assert workers.submit(published)
    assert workers._finalizer_inputs.qsize() == queued_before_ack
    workers.stop()


def test_preview_timeout_releases_durable_capture_before_early_final(tmp_path, paths):
    now = [10.0]
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        monotonic=lambda: now[0],
    )
    workers.start()
    segment = captured(tmp_path, "preview-timeout")
    workers._events.put(replace(segment, preview_pending=True, preview_text=""))
    assert not any(isinstance(event, CapturedSegment) for event in workers.poll_events())
    workers._events.put(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="最終",
            normalized_text="最終",
            engine_profile="fake",
        )
    )
    assert workers.poll_events() == []

    now[0] += 4.0
    events = workers.poll_events()

    assert isinstance(events[0], CapturedSegment)
    assert isinstance(events[1], FinalResult)
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("preview_timeout")
        for event in events
    )
    assert workers.pending_finalizations == 0
    workers.stop()


def test_supervisor_restarts_preview_worker(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        restart_backoff_seconds=0,
        max_restart_backoff_seconds=0,
    )
    workers.start()
    old_preview = workers._preview
    old_preview.die(1)

    events = workers.poll_events()

    assert workers._preview is not old_preview
    assert workers._preview.is_alive()
    assert any(
        isinstance(event, WorkerStatus)
        and event.worker == WorkerKind.PREVIEW
        and event.metadata.get("unexpected_exit")
        for event in events
    )
    workers.stop()


def test_child_guard_covers_all_three_processes_and_closes(tmp_path, paths):
    class Guard:
        def __init__(self):
            self.assigned = []
            self.closed = False

        def assign(self, process):
            self.assigned.append(process.name)

        def close(self):
            self.closed = True

    guard = Guard()
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        child_guard_factory=lambda: guard,
    )

    workers.start()
    assert guard.assigned == [
        "speech-journal-preview",
        "speech-journal-recorder",
        "speech-journal-finalizer",
    ]
    assert all(process.args[-1] == os.getpid() for process in context.processes)
    workers.stop()

    assert guard.closed


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_job_object_kills_assigned_child_on_close():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    job = _WindowsKillOnCloseJob()
    try:
        job.assign(SimpleNamespace(sentinel=int(child._handle)))
        job.close()
        assert child.wait(timeout=5) is not None
    finally:
        job.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_supervisor_restarts_dead_finalizer_and_requeues_pending(tmp_path, paths):
    context = FakeContext()
    now = [10.0]
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        command_queue_size=4,
        finalizer_queue_size=4,
        restart_backoff_seconds=1.0,
        monotonic=lambda: now[0],
    )
    workers.start()
    segment = captured(tmp_path)
    assert workers.submit(segment)
    old_finalizer = workers._finalizer
    old_finalizer.die(0)

    first = workers.poll_events()
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("unexpected_exit")
        for event in first
    )

    now[0] = 11.0
    second = workers.poll_events()
    assert workers._finalizer is not old_finalizer
    assert workers._finalizer.is_alive()
    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("restart_count") == 1
        for event in second
    )
    assert segment.segment_id in workers._queued_segment_ids
    workers.stop()


def test_staged_shutdown_keeps_finalizer_alive_until_pending_is_drained(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        command_queue_size=4,
        finalizer_queue_size=4,
    )
    workers.start()
    segment = captured(tmp_path)
    assert workers.submit(segment)
    assert workers.pending_finalizations == 1

    workers.stop_recorder()
    assert not workers._recorder.is_alive()
    assert workers._finalizer.is_alive()
    workers.poll_events()
    assert workers._recorder is context.processes[1]

    workers._events.put(
        FinalResult(
            segment_id=segment.segment_id,
            raw_text="完成",
            normalized_text="完成",
            engine_profile="fake",
        )
    )
    assert any(isinstance(event, FinalResult) for event in workers.poll_events())
    assert workers.pending_finalizations == 0

    workers.stop_finalizer()
    assert not workers._finalizer.is_alive()


def test_stop_worker_drains_full_event_queue_without_losing_events(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        event_queue_size=1,
    )
    workers.start()
    status = WorkerStatus(
        worker=WorkerKind.RECORDER,
        state=WorkerState.RECORDING,
        message="queued before shutdown",
    )
    workers._events.put_nowait(status)

    workers.stop_recorder()
    emitted = workers.poll_events()

    assert status in emitted
    assert not workers._recorder.is_alive()
    workers.stop_finalizer()


def test_stop_worker_retries_stop_command_after_command_backpressure(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        event_queue_size=1,
        command_queue_size=1,
    )
    workers.start()

    class BackpressuredRecorder:
        exitcode = None

        def __init__(self, commands):
            self.commands = commands
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(command, WorkerCommand) and command.kind == WorkerCommandKind.STOP:
                self.alive = False
                self.exitcode = 0

        def terminate(self):
            self.alive = False
            self.exitcode = -15

    recorder = BackpressuredRecorder(workers._recorder_commands)
    workers._recorder = recorder
    status = WorkerStatus(
        worker=WorkerKind.RECORDER,
        state=WorkerState.RECORDING,
        message="event queue occupied",
    )
    workers._events.put_nowait(status)

    workers.stop_recorder(timeout=1.0)

    assert not recorder.is_alive()
    assert status in workers.poll_events()
    workers.stop_finalizer()


def test_recorder_shutdown_timeout_never_terminates_undurable_audio(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
    )
    workers.start()

    class RecorderWaitingForStorage:
        exitcode = None

        def __init__(self) -> None:
            self.alive = True
            self.terminated = False

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            return None

        def terminate(self):
            self.terminated = True
            self.alive = False

    recorder = RecorderWaitingForStorage()
    workers._recorder = recorder

    with pytest.raises(RecorderShutdownPending, match="durable storage"):
        workers.stop_recorder(timeout=0)

    assert recorder.is_alive()
    assert not recorder.terminated
    assert any(
        isinstance(event, WorkerStatus)
        and event.metadata.get("shutdown_durability_pending")
        for event in workers.poll_events()
    )

    recorder.alive = False
    workers.stop_recorder(timeout=0)
    workers.stop_finalizer()


def test_supervisor_continues_capped_retries_after_fatal_threshold(tmp_path, paths):
    context = FakeContext()
    workers = JournalWorkers(
        AppConfig(records_root=str(paths.records_root)),
        paths,
        context=context,
        command_queue_size=4,
        finalizer_queue_size=4,
        max_restarts=1,
        restart_backoff_seconds=0,
        max_restart_backoff_seconds=0,
    )
    workers.start()
    workers._finalizer.die(1)
    workers.poll_events()
    assert workers._finalizer.is_alive()

    workers._finalizer.die(1)
    events = workers.poll_events()

    assert any(
        isinstance(event, WorkerStatus) and event.metadata.get("fatal") for event in events
    )
    assert workers._finalizer.is_alive()
    assert workers._restart_counts[WorkerKind.FINALIZER] == 1
    workers.stop()


def test_realtime_model_probe_exercises_preview_vad_and_opencc(tmp_path):
    calls = []

    class Preview:
        def warmup(self):
            calls.append("preview-warmup")

        def accept(self, samples, *, sample_rate):
            calls.append(("preview-accept", len(samples), sample_rate))
            return PreviewHypothesis("", False, False)

        def finish(self):
            calls.append("preview-finish")

        def normalize_text(self, text):
            assert text == "后台软件"
            calls.append("opencc")
            return "後臺軟件"

        def close(self):
            calls.append("preview-close")

    class Vad:
        def self_test(self):
            calls.append("vad-load")

        def accept(self, samples):
            calls.append(("vad-accept", len(samples)))

        def flush(self):
            calls.append("vad-flush")

    config = AppConfig(records_root=str(tmp_path / "records"))
    probe = probe_realtime_models(
        config,
        tmp_path / "models",
        preview_factory=Preview,
        vad_factory=Vad,
        numpy_module=np,
    )

    assert probe.preview_loaded and probe.vad_loaded
    assert probe.normalized_example == "後臺軟件"
    assert "preview-warmup" in calls
    assert "vad-load" in calls
    assert "opencc" in calls


def test_recorder_registers_recovered_partial_with_controller(tmp_path, paths):
    commands = queue.Queue()
    events = queue.Queue()
    commands.put(WorkerCommand(WorkerCommandKind.STOP))
    segment_id = str(uuid.uuid4())
    audio_path = tmp_path / f"{segment_id}.flac"
    audio_path.write_bytes(b"recovered")
    spool = SimpleNamespace(
        recovered_partials=[RecoveredFlac(segment_id, audio_path, 16_000, 1_600)],
        recovery_warnings=[],
        usage_ratio=0.0,
    )

    class Preview:
        def warmup(self):
            return None

        def close(self):
            return None

    class Vad:
        def reset(self):
            return None

    config = AppConfig(records_root=str(tmp_path / "records"))
    _recorder_loop(
        config,
        paths,
        commands,
        events,
        capture_factory=lambda: SimpleNamespace(running=False),
        preview_factory=Preview,
        vad_factory=lambda: (Vad(), None),
        spool_factory=lambda: spool,
        max_iterations=2,
    )

    recovered_events = [event for event in events.queue if isinstance(event, CapturedSegment)]
    assert len(recovered_events) == 1
    assert recovered_events[0].segment_id == segment_id
    assert recovered_events[0].duration_ms == 100
