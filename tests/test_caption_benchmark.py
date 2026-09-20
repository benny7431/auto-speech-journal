from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
benchmark = importlib.import_module("tools.benchmark_caption_pipeline")


class FakeEngine:
    def __init__(self):
        self.count = 0
        self.accepted = 0

    def accept(self, samples, *, sample_rate):
        assert sample_rate == 16000
        self.accepted += len(samples)
        self.count += 1
        return SimpleNamespace(text="甲" if self.count == 1 else "乙", is_endpoint=self.count == 1)

    def finish(self):
        return SimpleNamespace(text="乙丙", is_endpoint=True)


def test_preview_consumes_full_audio_and_preserves_endpoint_and_flush_text():
    engine = FakeEngine()
    result = benchmark.replay_preview(np.zeros(3500, np.float32), engine, {})

    assert engine.accepted == result["submitted_samples"] == 3500
    assert result["final_preview_text"] == "甲 乙丙"
    assert result["first_text_audio_ms"] == 100
    assert result["events"][-1]["audio_ms"] == 218.75
    assert result["text_edits"] is None
    assert result["first_nonempty_after_onset_engine_ms"] is None
    assert result["final_asr_latency_ms"] is None
    assert result["ui_latency_ms"] is None


def test_pre_onset_text_is_not_counted_as_speech_latency():
    result = benchmark.replay_preview(
        np.zeros(3200, np.float32), FakeEngine(), {"speech_start_ms": 150})

    assert result["pre_onset_nonempty_events"] == 1
    assert result["first_nonempty_after_onset_audio_ms"] == 50
    assert result["first_nonempty_after_onset_engine_ms"] is None


def test_reference_requires_manual_verification_and_matching_pcm():
    assert benchmark.trusted_annotation({"reference_text": "model output"}, "hash", 500) == {}
    with pytest.raises(ValueError, match="SHA-256"):
        benchmark.trusted_annotation({"manual_verified": True}, "hash", 500)
    annotation = {"manual_verified": True, "pcm_sha256": "hash", "reference_text": "人聲",
                  "speech_start_ms": 100, "speech_end_ms": 400}
    assert benchmark.trusted_annotation(annotation, "hash", 500) == annotation


@pytest.mark.parametrize("start,end", [(500, 400), (-1, 400), (0, 501), (float("nan"), 400)])
def test_invalid_manual_boundaries_are_rejected(start, end):
    with pytest.raises(ValueError):
        benchmark.trusted_annotation(
            {"manual_verified": True, "pcm_sha256": "hash", "speech_start_ms": start,
             "speech_end_ms": end}, "hash", 500)


def test_character_edits_report_substitution_deletion_insertion_and_empty_reference():
    result = benchmark.text_edits("AB C", "aX")
    assert result["substitutions"] == 1
    assert result["deletions"] == 1
    assert result["insertions"] == 0
    assert result["cer"] == pytest.approx(2 / 3)
    empty = benchmark.text_edits("", "幻覺")
    assert empty["insertions"] == 2
    assert empty["cer"] is None


def test_range_union_avoids_double_counting_and_keeps_internal_holes():
    assert benchmark.merged_ranges([[0, 10], [5, 20], [25, 40]], 30) == [[0, 20], [25, 30]]
    assert benchmark.missing_ranges([[0, 30]], [[5, 10], [8, 20], [25, 30]], 30) == [
        [0, 5], [20, 25]]


def test_coverage_separates_native_loss_from_vad_exclusion_and_roundtrips_flac(tmp_path):
    samples = np.linspace(-0.9, 0.9, 3200, dtype=np.float32)

    class FakeVad:
        def accept(self, chunk):
            return []

        def flush(self):
            self.observations.append({"native_range": [500, 2500],
                                      "native_matches_source": True})
            return [SimpleNamespace(start_sample=1000, end_sample=2500,
                                    samples=self.source[1000:2500])]

    result = benchmark.replay_coverage(samples, FakeVad(), tmp_path)
    assert result["submitted_samples"] == 3200
    assert result["native_missing_from_wrapper_ranges"] == [[500, 1000]]
    assert result["native_missing_from_wrapper_samples"] == 500
    assert not result["preservation_passed"]
    assert result["vad_excluded_ranges"] == [[0, 500], [2500, 3200]]
    assert result["verified_flac_ranges"] == [[1000, 2500]]
    assert result["lost_vocal_samples"] is None
    row = result["wrapper_and_flac"][0]
    assert row["flac_frames_match"] and row["flac_within_pcm16_step"]
    assert row["source_matches"] and row["native_missing_ranges"] == [[500, 1000]]


def test_corrupted_wrapper_samples_are_not_counted_as_verified_flac(tmp_path):
    source = np.ones(100, np.float32)
    row = benchmark.verify_flac(
        [SimpleNamespace(start_sample=0, end_sample=100, samples=np.zeros(100, np.float32))],
        source, tmp_path)[0]
    assert not row["source_matches"]
    assert row["flac_frames_match"]


def test_flac_pcm16_clipping_is_explicitly_allowed(tmp_path):
    source = np.array([-2, -1, -0.12345, 0, 0.12345, 1, 2], np.float32)
    row = benchmark.verify_flac(
        [SimpleNamespace(start_sample=0, end_sample=len(source), samples=source)],
        source, tmp_path)[0]
    assert row["source_matches"] and row["flac_within_pcm16_step"]


def test_memory_probe_returns_nullable_integer_fields():
    result = benchmark.memory_bytes()
    assert set(result) == {"rss_bytes", "process_peak_rss_bytes"}
    assert all(value is None or value > 0 for value in result.values())


def test_cli_refuses_to_overwrite_existing_audio_or_report(tmp_path):
    existing = tmp_path / "input.wav"
    existing.write_bytes(b"preserve")
    with pytest.raises(SystemExit) as exc:
        benchmark.main(["--mode", "preview", "--audio", str(existing),
                        "--models-dir", str(tmp_path), "--output", str(existing)])
    assert exc.value.code == 2
    assert existing.read_bytes() == b"preserve"


def test_source_metadata_hashes_actual_imported_files_without_private_paths():
    metadata = benchmark.source_metadata()
    assert set(metadata) == {"audio.py", "preview_engine.py", "finalizer_engine.py",
                             "benchmark_caption_pipeline.py"}
    assert all(len(digest) == 64 for digest in metadata.values())
    assert metadata["benchmark_caption_pipeline.py"] == benchmark.sha256(Path(benchmark.__file__))


@pytest.mark.parametrize("overlap", [0, 100])
def test_final_uses_temporary_flac_and_reports_compute_not_live_latency(tmp_path, overlap):
    import soundfile as sf

    from auto_speech_journal.types import CapturedSegment, FinalResult

    source = np.linspace(-0.1, 0.1, 3200, dtype=np.float32)
    segments = [SimpleNamespace(start_sample=0, end_sample=1600, samples=source[:1600]),
                SimpleNamespace(start_sample=1600 - overlap, end_sample=3200,
                                samples=source[1600 - overlap:])]
    rows = benchmark.verify_flac(segments, source, tmp_path)
    coverage = {"wrapper_and_flac": rows, "submitted_samples": 3200,
                "native_observations": [{"decision_sample": 1600}, {"decision_sample": 3200}]}

    class FakeFinalizer:
        active_device = "cpu"
        active_compute_type = "int8"
        last_fallback_reason = None
        last_deadline_exceeded = False
        warmups = 0

        def warmup(self):
            self.warmups += 1

        def transcribe(self, segment):
            assert isinstance(segment, CapturedSegment)
            values, rate = sf.read(segment.audio_path)
            index = int(segment.segment_id.split("-")[1])
            assert len(values) == len(segments[index].samples) and rate == 16000
            assert segment.preview_text == ""
            assert segment.duration_ms == round(len(values) / 16)
            text = "甲" if index == 0 else "乙"
            return FinalResult(segment.segment_id, text, text, "fake:cpu", latency_ms=3)

    engine = FakeFinalizer()
    result = benchmark.replay_final(coverage, tmp_path, engine, {"reference_text": "甲乙"})
    assert engine.warmups == 1
    assert result["final_success_count"] == 2 and result["final_failure_count"] == 0
    assert result["final_overlap_samples"] == overlap
    assert result["final_events"][1]["vad_decision_audio_ms"] == 200
    assert result["final_events"][0]["source_range"] == [0, 1600]
    assert result["final_events"][0]["engine_reported_compute_ms"] == 3
    assert result["final_transcribe_wall_ms"] >= 0
    assert result["final_transcribe_process_cpu_ms"] >= 0
    assert result["final_asr_latency_ms"] is None and result["ui_latency_ms"] is None
    if overlap:
        assert result["final_text_edits"] is None
    else:
        assert result["final_text_edits"]["cer"] == 0


def test_final_empty_vad_selection_does_not_load_or_call_engine(tmp_path):
    result = benchmark.replay_final(
        {"wrapper_and_flac": [], "native_observations": [], "submitted_samples": 1600},
        tmp_path, object(), {})
    assert result["final_events"] == []
    assert result["final_engine_load_ms"] is None
    assert result["final_text_edits"] is None


def test_final_failure_is_visible_and_not_replaced_with_reference(tmp_path):
    from auto_speech_journal.types import FinalResult

    engine = SimpleNamespace(
        warmup=lambda: None, active_device="cpu", active_compute_type="int8",
        last_fallback_reason="CUDA unavailable", last_deadline_exceeded=True,
        transcribe=lambda segment: FinalResult(segment.segment_id, "", "", "fake:late",
                                              success=False, error="decode failed", latency_ms=50))
    result = benchmark.replay_final(
        {"wrapper_and_flac": [{"range": [0, 1600]}], "submitted_samples": 1600,
         "native_observations": [{"decision_sample": 1600}]},
        tmp_path, engine, {"reference_text": "甲"})
    assert result["final_failure_count"] == 1 and result["final_success_count"] == 0
    assert result["final_events"][0]["error"] == "decode failed"
    assert result["final_events"][0]["deadline_exceeded"]
    assert result["final_text_segment_join"] == ""
    assert result["final_text_edits"]["deletions"] == 1
