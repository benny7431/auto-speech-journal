from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from auto_speech_journal.audio import FlacSpool, SherpaSileroVadSegmenter


class NativeSegments:
    def __init__(self, source, schedule, *, on_flush=False):
        self.source = source
        self.schedule = list(schedule)
        self.pending = []
        self.cursor = 0
        self.on_flush = on_flush

    def accept_waveform(self, samples):
        self.cursor += len(samples)
        if not self.on_flush:
            self._publish()

    def _publish(self):
        while self.schedule and self.schedule[0][0] <= self.cursor:
            _, start, end = self.schedule.pop(0)
            self.pending.append(SimpleNamespace(start=start, samples=self.source[start:end]))

    def flush(self):
        self._publish()

    def empty(self):
        return not self.pending

    @property
    def front(self):
        return self.pending[0]

    def pop(self):
        self.pending.pop(0)


@pytest.mark.parametrize("on_flush", [False, True])
def test_long_native_segment_survives_history_eviction_and_flac(tmp_path, on_flush):
    source = np.arange(7600, dtype=np.float32) / 32768
    vad = SherpaSileroVadSegmenter(tmp_path / "unused.onnx", sample_rate=100)
    vad._vad = NativeSegments(source, [(7600, 2858, 7590)], on_flush=on_flush)
    segments = []
    for offset in range(0, len(source), 10):
        segments.extend(vad.accept(source[offset : offset + 10]))
    segments.extend(vad.flush())

    assert len(segments) == 1
    segment = segments[0]
    assert segment.start_sample <= 2858
    assert segment.end_sample == 7590
    np.testing.assert_array_equal(segment.samples, source[segment.start_sample : 7590])
    spool = FlacSpool(tmp_path / "spool", limit_bytes=1_000_000)
    path = spool.write(segment.samples, sample_rate=100, segment_id="long-segment")
    saved, rate = sf.read(path, dtype="float32")
    assert rate == 100
    np.testing.assert_array_equal(saved, source[segment.start_sample : 7590])
    assert vad.flush() == []


def test_native_segments_keep_preroll_and_only_requested_boundary_overlap(tmp_path):
    source = np.arange(4000, dtype=np.float32) / 32768
    vad = SherpaSileroVadSegmenter(tmp_path / "unused.onnx", sample_rate=100)
    vad._vad = NativeSegments(source, [(3100, 200, 3000), (4000, 3000, 3950)])
    segments = []
    for offset in range(0, len(source), 10):
        segments.extend(vad.accept(source[offset : offset + 10]))
    segments.extend(vad.flush())

    assert [(s.start_sample, s.end_sample) for s in segments] == [(170, 3000), (2900, 3950)]
    assert segments[0].forced_endpoint
    assert not segments[1].forced_endpoint
    for segment in segments:
        np.testing.assert_array_equal(
            segment.samples, source[segment.start_sample : segment.end_sample]
        )
    assert segments[0].end_sample - segments[1].start_sample == 100
    assert vad.flush() == []
