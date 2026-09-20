from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from auto_speech_journal.audio import (
    EnergyVadSegmenter,
    SpeechAudio,
    StreamingResampler,
    WasapiMicrophone,
)
from auto_speech_journal.config import (
    AppConfig,
    DeviceFingerprint,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.types import (
    InputRouteRequest,
    WorkerCommand,
    WorkerCommandKind,
    WorkerStatus,
)
from auto_speech_journal.workers import _recorder_loop


class BufferedMicrophone(WasapiMicrophone):
    """Use the real queue/resampler/close logic without opening an audio device."""

    def __init__(self, fingerprint, *, sample_rate=16_000, after_read=None):
        super().__init__(fingerprint)
        self.sample_rate = sample_rate
        self.after_read = after_read
        self.source = [
            np.full(sample_rate // 10, 0.1, np.float32),
            np.full(sample_rate // 10, 0.2, np.float32),
            np.full(111, 0.3, np.float32),
        ]

    def start(self):
        owner = self

        class Stream:
            active = True

            def stop(self):
                if self.active:
                    # A final callback may finish while PortAudio stop is waiting.
                    tail = owner.source[-1]
                    owner._callback(tail, len(tail), None, None)
                    self.active = False

            def close(self):
                self.active = False

        self._device = SimpleNamespace(
            name=self.fingerprint.name, index=1, default_sample_rate=self.sample_rate
        )
        self._resampler = StreamingResampler(self.sample_rate)
        self._stream = Stream()
        for samples in self.source[:2]:
            self._callback(samples, len(samples), None, None)
        return self._device

    def read(self, timeout=None):
        chunk = super().read(timeout)
        callback, self.after_read = self.after_read, None
        if callback is not None:
            callback()
        return chunk


@pytest.mark.parametrize("sample_rate", [16_000, 48_000])
def test_stop_and_drain_preserves_queue_last_callback_and_resampler_tail(sample_rate):
    microphone = BufferedMicrophone(DeviceFingerprint(name="Input A"), sample_rate=sample_rate)
    microphone.start()
    first = microphone.read(timeout=0)
    remaining = microphone.stop_and_drain()
    expected = StreamingResampler(sample_rate).process(
        np.concatenate(microphone.source), final=True
    )

    actual = np.concatenate([first.samples, *(chunk.samples for chunk in remaining)])
    np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)
    previous = first
    for chunk in remaining:
        expected_start = previous.started_at_utc + timedelta(
            seconds=len(previous.samples) / previous.sample_rate
        )
        assert chunk.started_at_utc == expected_start
        previous = chunk
    assert not microphone.running
    assert microphone.stop_and_drain() == []


@pytest.mark.parametrize("failure", ["stop", "close"])
def test_stop_and_drain_retries_device_failure_without_losing_accepted_audio(failure):
    microphone = BufferedMicrophone(DeviceFingerprint(name="Input A"), sample_rate=48_000)
    microphone.start()
    first = microphone.read(timeout=0)
    stream = microphone._stream
    original = getattr(stream, failure)
    attempts = 0

    def fail_once():
        nonlocal attempts
        attempts += 1
        original()
        if attempts == 1:
            raise OSError("device shutdown failed once")

    setattr(stream, failure, fail_once)
    with pytest.raises(OSError, match="failed once"):
        microphone.stop_and_drain()
    assert microphone._stream is stream
    assert microphone._resampler is not None
    assert not microphone._queue.empty()

    remaining = microphone.stop_and_drain()
    expected = StreamingResampler(48_000).process(np.concatenate(microphone.source), final=True)
    np.testing.assert_allclose(
        np.concatenate([first.samples, *(chunk.samples for chunk in remaining)]),
        expected,
        atol=1e-6,
        rtol=0,
    )
    assert attempts == 2
    assert microphone.stop_and_drain() == []


def test_output_clock_preserves_a_capture_gap():
    microphone = BufferedMicrophone(DeviceFingerprint(name="Input A"))
    microphone.start()
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    microphone._queue.queue[0].started_at_utc = origin
    microphone._queue.queue[1].started_at_utc = origin + timedelta(seconds=2)
    first = microphone.read(timeout=0)
    second = microphone.read(timeout=0)

    assert first.started_at_utc == origin
    assert second.started_at_utc == origin + timedelta(seconds=2)
    microphone.stop()


@pytest.mark.parametrize(
    "action", [WorkerCommandKind.STOP, WorkerCommandKind.PAUSE, WorkerCommandKind.RECONFIGURE_INPUT]
)
def test_recorder_drains_accepted_audio_before_stop_pause_or_switch(tmp_path, action):
    commands = queue.Queue()
    events = queue.Queue()
    previews = queue.Queue()
    saved = []
    captures = []
    input_a = DeviceFingerprint(name="Input A")
    input_b = DeviceFingerprint(name="Input B")
    selection_a = MicrophoneSelection(MicrophoneMode.FIXED, input_a)
    commands.put(WorkerCommand(WorkerCommandKind.START))

    def request_action():
        request = (
            InputRouteRequest("switch-to-b", MicrophoneSelection(MicrophoneMode.FIXED, input_b))
            if action == WorkerCommandKind.RECONFIGURE_INPUT
            else None
        )
        commands.put(WorkerCommand(action, request))
        if action == WorkerCommandKind.PAUSE:
            commands.put(WorkerCommand(WorkerCommandKind.STOP))

    def capture_factory(fingerprint):
        capture = BufferedMicrophone(fingerprint, after_read=request_action)
        captures.append(capture)
        if fingerprint.name == "Input B":
            # A successful switch is enough; the second input contributes no audio.
            capture.source = [np.empty(0, np.float32)] * 3
            commands.put(WorkerCommand(WorkerCommandKind.STOP))
        return capture

    class Spool:
        usage_ratio = 0.0

        def can_reserve(self, _bytes):
            return True

        def write(self, samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            saved.append(samples.copy())
            path = tmp_path / f"{segment_id}.flac"
            path.write_bytes(b"isolated test spool")
            return path

    config = AppConfig(microphone=selection_a, pre_roll_ms=0, segment_overlap_ms=0)
    _recorder_loop(
        config,
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        previews,
        capture_factory=capture_factory,
        vad_factory=lambda: (EnergyVadSegmenter(pre_roll_ms=0, overlap_ms=0), None),
        spool_factory=Spool,
        max_iterations=15,
    )

    assert saved
    np.testing.assert_array_equal(np.concatenate(saved), np.concatenate(captures[0].source))
    assert all(not capture.running for capture in captures)
    if action == WorkerCommandKind.RECONFIGURE_INPUT:
        assert [capture.fingerprint.name for capture in captures] == ["Input A", "Input B"]


@pytest.mark.parametrize(
    "failure_stage", [
        "before-accept", "after-accept", "endpoint-accept", "endpoint-state",
        "partial-endpoint-accept",
    ]
)
@pytest.mark.parametrize("spool_failures", [0, 2], ids=["healthy-spool", "repeated-spool-failure"])
def test_recorder_recovers_failed_vad_chunk_exactly_once(
    tmp_path, failure_stage, spool_failures
):
    commands = queue.Queue()
    events = queue.Queue()
    saved = []
    fingerprint = DeviceFingerprint(name="Input A")
    capture = BufferedMicrophone(
        fingerprint,
        after_read=lambda: commands.put(WorkerCommand(WorkerCommandKind.STOP)),
    )

    class FailingVad(EnergyVadSegmenter):
        calls = 0
        failures = 0
        endpoint_samples = 0
        fail_state = False

        @property
        def is_speech_detected(self):
            if self.fail_state:
                self.fail_state = False
                self.failures += 1
                raise RuntimeError("injected VAD accept failure")
            return super().is_speech_detected

        def accept(self, samples):
            self.calls += 1
            if self.calls == 1 and failure_stage == "partial-endpoint-accept":
                super().accept(samples)
                return [SpeechAudio(samples[:800].copy(), 0, 800)]
            if self.calls == 2:
                if failure_stage != "before-accept":
                    completed = super().accept(samples)
                    self.endpoint_samples = sum(len(speech.samples) for speech in completed)
                    if failure_stage == "endpoint-state":
                        self.fail_state = True
                        return completed
                self.failures += 1
                raise RuntimeError("injected VAD accept failure")
            return super().accept(samples)

    class Spool:
        usage_ratio = 0.0
        failures = 0

        def can_reserve(self, _bytes):
            return True

        def write(self, samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            if self.failures < spool_failures:
                self.failures += 1
                raise OSError("injected spool failure")
            saved.append(samples.copy())
            path = tmp_path / f"{segment_id}.flac"
            path.write_bytes(b"isolated test spool")
            return path

    vad = FailingVad(
        pre_roll_ms=0,
        overlap_ms=0,
        max_segment_ms=200 if "endpoint" in failure_stage else 28_000,
    )
    spool = Spool()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    _recorder_loop(
        AppConfig(
            microphone=MicrophoneSelection(MicrophoneMode.FIXED, fingerprint),
            pre_roll_ms=0,
            segment_overlap_ms=0,
        ),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        events,
        queue.Queue(),
        capture_factory=lambda _fingerprint: capture,
        vad_factory=lambda: (vad, None),
        spool_factory=lambda: spool,
        max_iterations=30,
    )

    assert vad.failures == 1
    if "endpoint" in failure_stage:
        assert vad.endpoint_samples == 3200
    assert spool.failures == spool_failures
    statuses = [event for event in events.queue if isinstance(event, WorkerStatus)]
    assert any("injected VAD accept failure" in event.message for event in statuses)
    if spool_failures:
        assert any(event.metadata.get("spool_write_recovered") for event in statuses)
    assert saved
    if failure_stage == "partial-endpoint-accept":
        assert len(saved[0]) == 800
    actual = np.concatenate(saved)
    assert len(actual) == 3311
    np.testing.assert_array_equal(actual, np.concatenate(capture.source))
    assert not capture.running


@pytest.mark.parametrize("speech_active", [False, True], ids=["idle-bounded", "active-retained"])
def test_vad_recovery_bounds_idle_history_but_preserves_unfinished_speech(tmp_path, speech_active):
    commands = queue.Queue()
    saved = []
    capture = BufferedMicrophone(
        DeviceFingerprint(name="Input A"),
        after_read=lambda: commands.put(WorkerCommand(WorkerCommandKind.STOP)),
    )
    capture.source = [
        np.full(48_000, 0.1, np.float32),
        np.full(48_000, 0.2, np.float32),
        np.full(111, 0.3, np.float32),
    ]

    class LostStateVad:
        is_speech_detected = speech_active
        calls = 0
        failures = 0

        def accept(self, _samples):
            self.calls += 1
            if self.calls == 3:
                self.failures += 1
                raise RuntimeError("lost buffered VAD state")
            return []

        def flush(self):
            return []

        def reset(self):
            return None

    class Spool:
        usage_ratio = 0.0

        def can_reserve(self, _bytes):
            return True

        def write(self, samples, *, sample_rate, segment_id):
            assert sample_rate == 16_000
            saved.append(samples.copy())
            path = tmp_path / f"{segment_id}.flac"
            path.write_bytes(b"isolated test spool")
            return path

    vad = LostStateVad()
    commands.put(WorkerCommand(WorkerCommandKind.START))
    _recorder_loop(
        AppConfig(
            microphone=MicrophoneSelection(MicrophoneMode.FIXED, capture.fingerprint),
            max_segment_ms=1000,
            endpoint_silence_ms=250,
            pre_roll_ms=0,
            segment_overlap_ms=0,
        ),
        AppPaths(tmp_path / "runtime", tmp_path / "records"),
        commands,
        queue.Queue(),
        queue.Queue(),
        capture_factory=lambda _fingerprint: capture,
        vad_factory=lambda: (vad, None),
        spool_factory=Spool,
        max_iterations=30,
    )

    assert vad.failures == 1
    expected = np.concatenate(capture.source)
    if not speech_active:
        # Confirmed idle keeps 1000 ms segment + 250 ms endpoint history,
        # plus the failing final callback whose speech classification is unknown.
        expected = expected[-20_111:]
    assert all(len(samples) <= 16_000 for samples in saved)
    np.testing.assert_array_equal(np.concatenate(saved), expected)
