from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
benchmark = importlib.import_module("tools.benchmark_preview_latency")
PreviewLatencyResult = benchmark.PreviewLatencyResult
benchmark_samples = benchmark.benchmark_samples
passes_threshold = benchmark.passes_threshold
prepare_audio = benchmark.prepare_audio
summarize_results = benchmark.summarize_results


class FakePreviewEngine:
    def __init__(self, outputs: list[list[str]]) -> None:
        self.outputs = outputs
        self.reset_count = 0
        self.finish_count = 0
        self.accepted_sizes: list[int] = []
        self._file_index = 0
        self._active: list[str] = []

    def reset(self) -> None:
        self._active = list(self.outputs[self._file_index])
        self.reset_count += 1

    def accept(self, samples, *, sample_rate=None):
        assert sample_rate == 16_000
        self.accepted_sizes.append(len(samples))
        text = self._active.pop(0) if self._active else ""
        return SimpleNamespace(text=text)

    def finish(self):
        text = self._active.pop(0) if self._active else ""
        self.finish_count += 1
        self._file_index += 1
        return SimpleNamespace(text=text)


def _clock(*values: float):
    ticks = iter(values)
    return lambda: next(ticks)


def test_first_nonempty_hypothesis_uses_100ms_audio_chunks_and_resets_per_file():
    engine = FakePreviewEngine(
        [["", "   ", "第一個字", "第一檔完成"], ["下一檔", "第二檔完成"]]
    )
    samples = np.ones(6_400, dtype=np.float32)

    first = benchmark_samples(
        "one.wav",
        samples,
        engine,
        monotonic=_clock(10.0, 10.01, 10.03, 10.06),
    )
    second = benchmark_samples(
        "two.flac",
        samples,
        engine,
        monotonic=_clock(20.0, 20.015),
    )

    assert first.first_text == "第一個字"
    assert first.first_audio_time_ms == 300
    assert first.wall_compute_ms == pytest.approx(60)
    assert second.first_text == "下一檔"
    assert second.first_audio_time_ms == 100
    assert engine.reset_count == 2
    assert engine.finish_count == 2
    assert engine.accepted_sizes == [1_600, 1_600, 1_600, 1_600]


def test_accept_error_keeps_original_error_and_best_effort_recreates_stream():
    class BrokenEngine:
        def __init__(self) -> None:
            self.reset_count = 0
            self.finish_count = 0

        def reset(self) -> None:
            self.reset_count += 1

        def accept(self, _samples, *, sample_rate=None):
            assert sample_rate == 16_000
            raise RuntimeError("accept failed")

        def finish(self):
            self.finish_count += 1
            if self.finish_count == 1:
                raise RuntimeError("finish failed")
            return SimpleNamespace(text="stale text")

    engine = BrokenEngine()

    with pytest.raises(RuntimeError, match="accept failed"):
        benchmark_samples(
            "broken.wav",
            np.ones(1_600, dtype=np.float32),
            engine,
            monotonic=_clock(1.0),
        )

    assert engine.reset_count == 2
    assert engine.finish_count == 2


def test_prepare_audio_downmixes_stereo_without_loading_a_file():
    stereo = np.column_stack(
        [np.ones(1_600, dtype=np.float32), np.zeros(1_600, dtype=np.float32)]
    )

    mono = prepare_audio(stereo, 16_000)

    assert mono.shape == (1_600,)
    assert mono.dtype == np.float32
    assert np.allclose(mono, 0.5)


def test_summary_reports_interpolated_median_p95_and_missing_detection():
    results = [
        PreviewLatencyResult("a.wav", 16_000, 500, "甲", 100, 10),
        PreviewLatencyResult("b.wav", 16_000, 500, "乙", 200, 20),
        PreviewLatencyResult("c.flac", 48_000, 500, "丙", 400, 30),
        PreviewLatencyResult("silent.wav", 16_000, 500, None, None, None),
    ]

    summary = summarize_results(results)

    assert summary["detected_count"] == 3
    assert summary["missing_count"] == 1
    assert summary["first_audio_time_ms"] == {
        "count": 3,
        "median": 200,
        "p95": 380,
    }
    assert summary["wall_compute_ms"]["median"] == 20
    assert not passes_threshold(summary, 1_200)

    complete = summarize_results(results[:3])
    assert passes_threshold(complete, 380)
    assert not passes_threshold(complete, 379.9)
