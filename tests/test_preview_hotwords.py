from __future__ import annotations

import itertools
import queue
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from auto_speech_journal.audio import AudioChunk, SpeechAudio
from auto_speech_journal.config import AppConfig
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.preview_engine import PreviewEngineError, SherpaPreviewEngine
from auto_speech_journal.types import (
    PartialUpdate,
    PreviewAudioChunk,
    Severity,
    WorkerCommand,
    WorkerCommandKind,
    WorkerState,
    WorkerStatus,
)
from auto_speech_journal.workers import _preview_loop, _recorder_loop


class TextStream:
    def __init__(self) -> None:
        self.text = ""

    def accept_waveform(self, _rate: int, samples: list[str]) -> None:
        self.text += "".join(samples)


class TextRecognizer:
    def __init__(self) -> None:
        self.streams: list[TextStream] = []
        self.hotwords: list[str] = []
        self.fail = False

    def create_stream(self, hotwords: str = "") -> TextStream:
        if self.fail or hotwords == "失敗":
            raise RuntimeError("synthetic stream failure")
        stream = TextStream()
        self.streams.append(stream)
        self.hotwords.append(hotwords)
        return stream

    def is_ready(self, _stream: TextStream) -> bool:
        return False

    def is_endpoint(self, _stream: TextStream) -> bool:
        return False

    def get_result(self, stream: TextStream) -> str:
        return stream.text

    def reset(self, stream: TextStream) -> None:
        stream.text = ""


def make_engine(recognizer: TextRecognizer, *, supported: bool = False) -> SherpaPreviewEngine:
    return SherpaPreviewEngine(
        recognizer_factory=lambda: recognizer,
        normalizer_factory=lambda: lambda text: text.replace("词", "詞"),
        supports_hotwords=supported,
    )


@pytest.mark.parametrize("words", [[], ["新詞"], ["新詞", "另一詞"]])
def test_unsupported_hotwords_keep_in_progress_audio(words: list[str]) -> None:
    recognizer = TextRecognizer()
    engine = make_engine(recognizer)
    assert engine.accept(["前文"]).text == "前文"

    assert engine.update_hotwords(words) is False

    assert not engine.accept([]).changed
    assert engine.accept(["後文"]).text == "前文後文"
    assert len(recognizer.streams) == 1
    assert recognizer.hotwords == [""]


def test_unchanged_normalized_hotwords_keep_in_progress_audio() -> None:
    recognizer = TextRecognizer()
    engine = make_engine(recognizer, supported=True)
    engine.update_hotwords(["詞"])
    assert engine.accept(["前文"]).text == "前文"

    assert engine.update_hotwords([" 词 ", "詞", ""]) is False

    assert not engine.accept([]).changed
    assert engine.accept(["後文"]).text == "前文後文"
    assert engine.hotwords_applied
    assert recognizer.hotwords == ["詞"]


def test_supported_hotword_change_reports_stream_change_and_can_clear_words() -> None:
    recognizer = TextRecognizer()
    engine = make_engine(recognizer, supported=True)
    assert engine.update_hotwords(["詞"]) is False  # No active stream before warmup.
    engine.accept(["前文"])

    assert engine.update_hotwords(["新詞"]) is True
    assert engine.hotwords_applied
    assert engine.accept(["後文"]).text == "後文"
    assert engine.update_hotwords([]) is True
    assert engine.hotwords_applied
    assert recognizer.hotwords == ["詞", "新詞", ""]


def test_failed_hotword_update_keeps_stream_and_can_retry() -> None:
    recognizer = TextRecognizer()
    engine = make_engine(recognizer, supported=True)
    engine.update_hotwords(["詞"])
    engine.accept(["前文"])
    recognizer.fail = True

    with pytest.raises(RuntimeError, match="synthetic stream failure"):
        engine.update_hotwords(["新詞"])

    assert engine.hotwords_applied
    assert not engine.accept([]).changed
    assert engine.accept(["後文"]).text == "前文後文"
    recognizer.fail = False
    assert engine.update_hotwords(["新詞"]) is True
    assert recognizer.hotwords == ["詞", "新詞"]


def test_rejected_hotword_signatures_keep_the_existing_stream() -> None:
    class PlainRecognizer(TextRecognizer):
        def create_stream(self) -> TextStream:
            return super().create_stream()

    recognizer = PlainRecognizer()
    engine = make_engine(recognizer, supported=True)
    engine.accept(["前文"])

    with pytest.raises(PreviewEngineError, match="did not apply hotwords"):
        engine.update_hotwords(["詞"])

    assert engine.accept(["後文"]).text == "前文後文"
    assert engine.hotwords_applied


@pytest.mark.parametrize("pipeline", ["queued", "inline"])
@pytest.mark.parametrize("scenario", ["unsupported", "unchanged", "changed", "failed"])
def test_hotword_commands_preserve_partial_context(
    tmp_path: Path, pipeline: str, scenario: str,
) -> None:
    recognizer = TextRecognizer()

    class ChunkPreview(SherpaPreviewEngine):
        def accept(self, samples, *, sample_rate=None):
            # Keep model output deterministic while exercising real engine stream state.
            text = "前词" if samples[0] == 1 else "後文"
            return super().accept([text], sample_rate=sample_rate)

    engine = ChunkPreview(
        recognizer_factory=lambda: recognizer,
        normalizer_factory=lambda: lambda text: text.replace("词", "詞"),
        supports_hotwords=scenario != "unsupported",
    )
    engine.update_hotwords(["詞"])
    requested = {"unchanged": "詞", "failed": "失敗"}.get(scenario, "新詞")
    update = WorkerCommand(WorkerCommandKind.UPDATE_HOTWORDS, [requested])
    events = queue.Queue()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
    config = AppConfig(records_root=str(paths.records_root))
    if pipeline == "queued":
        inputs = queue.Queue()
        for value in (1, 2):
            inputs.put(PreviewAudioChunk(
                segment_id="segment", samples=np.full(1600, value, np.float32),
                sample_rate=16000, segment_started_at_utc=started,
            ))
            if value == 1:
                inputs.put(update)
        inputs.put(WorkerCommand(WorkerCommandKind.STOP))
        clock = itertools.count()
        _preview_loop(
            config, paths, inputs, events, engine_factory=lambda: engine,
            monotonic=lambda: float(next(clock)),
        )
    else:
        commands = queue.Queue()
        commands.put(WorkerCommand(WorkerCommandKind.START))
        clock = {"now": 0.0}

        class Capture:
            running = False
            reads = 0

            def start(self):
                self.running = True
                return SimpleNamespace(name="synthetic", index=44)

            def read(self, timeout):
                self.reads += 1
                if self.reads > 2:
                    commands.put(WorkerCommand(WorkerCommandKind.STOP))
                    raise queue.Empty
                clock["now"] += 0.4
                if self.reads == 1:
                    commands.put(update)
                return AudioChunk(
                    np.full(1600, self.reads, np.float32), 16000,
                    started + timedelta(milliseconds=100 * (self.reads - 1)),
                )

            def stop(self):
                self.running = False

        class Vad:
            is_speech_detected = True

            def __init__(self):
                self.samples = []

            def accept(self, samples):
                self.samples.append(samples.copy())
                return []

            def flush(self):
                if not self.samples:
                    return []
                values = np.concatenate(self.samples)
                return [SpeechAudio(values, 0, len(values))]

            def reset(self):
                self.samples.clear()

        class Spool:
            usage_ratio = 0.0

            def write(self, _samples, *, sample_rate, segment_id):
                return tmp_path / f"{segment_id}.flac"

        _recorder_loop(
            config, paths, commands, events,
            capture_factory=Capture, preview_factory=lambda: engine,
            vad_factory=lambda: (Vad(), None), spool_factory=Spool,
            monotonic=lambda: clock["now"], max_iterations=12,
        )
    partials = [event for event in events.queue if isinstance(event, PartialUpdate)]
    assert [event.text for event in partials] == ["前詞", "前詞後文"]
    assert [event.raw_text for event in partials] == ["前词", "前词後文"]
    messages = [event.message for event in events.queue if isinstance(event, WorkerStatus)]
    assert any("hotwords are unsupported" in message for message in messages) == (
        scenario == "unsupported"
    )
    assert any("hotword update failed" in message for message in messages) == (scenario == "failed")
    if scenario == "failed":
        failure = next(
            event for event in events.queue
            if isinstance(event, WorkerStatus) and "hotword update failed" in event.message
        )
        assert failure.severity == Severity.WARNING
        if pipeline == "queued":
            assert "streaming preview recovered" in messages
        else:
            assert failure.state == WorkerState.RECORDING
