"""Replay local WAV/FLAC without recording, UI, settings, or persistent journal data.

Run with PYTHONPATH=src: python tools/benchmark_caption_pipeline.py --mode preview
--audio sample.wav --models-dir MODEL_ROOT --threads 1 --output result.json
Repeat in separate processes for 2 and 4 threads. Add --realtime for paced engine
latency; otherwise wall times describe accelerated replay, not live latency.
Coverage mode runs VAD and production FlacSpool in an automatically removed temp
directory. It measures preservation, never whether VAD labels are correct.
Final mode adds sequential FasterWhisperFinalizer calls on those FLAC files.
It is offline: final computation time is not end-of-speech-to-screen latency.

Optional annotation JSON: {"pcm_sha256": "hash from an unannotated run",
"manual_verified": true, "speech_start_ms": 500, "speech_end_ms": 4000,
"reference_text": "manually checked complete transcript"}. Each field is optional.
Unverified annotations are ignored; verified annotations must match prepared PCM.
No published lyrics or model-generated transcript is implicitly trusted.
Output includes recognized text: keep reports for private audio private.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import inspect
import json
import math
import os
import platform
import tempfile
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from auto_speech_journal.audio import FlacSpool, SherpaSileroVadSegmenter
from auto_speech_journal.config import AppConfig
from auto_speech_journal.finalizer_engine import FasterWhisperFinalizer
from auto_speech_journal.model_download import FINAL_SPEC, resolve_model_paths
from auto_speech_journal.preview_engine import SherpaPreviewEngine
from auto_speech_journal.types import CapturedSegment

try:
    from tools.benchmark_preview_latency import load_audio
except ModuleNotFoundError:  # Direct script invocation puts tools/ on sys.path.
    from benchmark_preview_latency import load_audio

RATE = 16_000
BLOCK = 1_600
PREVIEW_DIRECTORY = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_metadata() -> dict[str, str]:
    # Hash imported implementations: a non-editable install may otherwise hide
    # that PYTHONPATH was omitted. Reports need no private absolute paths.
    paths = [Path(inspect.getfile(cls)) for cls in (
        SherpaSileroVadSegmenter, SherpaPreviewEngine, FasterWhisperFinalizer)]
    return {path.name: sha256(path) for path in [*paths, Path(__file__)]}


def memory_bytes() -> dict[str, int | None]:
    """Windows working set and process-lifetime peak; unavailable elsewhere."""
    result: dict[str, int | None] = {"rss_bytes": None, "process_peak_rss_bytes": None}
    if os.name != "nt":
        return result
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in ("peak", "rss", "ppp", "pp", "pnp", "np", "page", "peakpage")
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        result.update(rss_bytes=counters.rss, process_peak_rss_bytes=counters.peak)
    return result


def trusted_annotation(data: dict[str, Any], pcm_hash: str, duration_ms: float) -> dict:
    if data.get("manual_verified") is not True:
        return {}
    if data.get("pcm_sha256") != pcm_hash:
        raise ValueError("verified annotation does not match prepared PCM SHA-256")
    for key in ("speech_start_ms", "speech_end_ms"):
        value = data.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, float | int)
            or not math.isfinite(value) or not 0 <= value <= duration_ms
        ):
            raise ValueError(f"invalid {key}")
    start, end = data.get("speech_start_ms"), data.get("speech_end_ms")
    if start is not None and end is not None and start > end:
        raise ValueError("speech start is after speech end")
    if "reference_text" in data and not isinstance(data["reference_text"], str):
        raise ValueError("reference_text must be a string")
    return data


def text_edits(reference: str, hypothesis: str) -> dict[str, Any]:
    """Character edits; NFKC/casefold, ignore punctuation/space, no script conversion."""
    def normalize(text: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                       if c.isalnum())

    ref, hyp = normalize(reference), normalize(hypothesis)
    # Tuples are (cost, substitutions, deletions, insertions); prefer diagonal ties.
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i, left in enumerate(ref, 1):
        current = [(i, 0, i, 0)]
        for j, right in enumerate(hyp, 1):
            cost, sub, delete, insert = previous[j - 1]
            diagonal = (cost + (left != right), sub + (left != right), delete, insert)
            cost, sub, delete, insert = previous[j]
            deletion = (cost + 1, sub, delete + 1, insert)
            cost, sub, delete, insert = current[-1]
            insertion = (cost + 1, sub, delete, insert + 1)
            current.append(min((diagonal, deletion, insertion), key=lambda item: item[0]))
        previous = current
    cost, sub, delete, insert = previous[-1]
    return {"reference_characters": len(ref), "substitutions": sub, "deletions": delete,
            "insertions": insert, "cer": cost / len(ref) if ref else None,
            "normalization": "NFKC, casefold, alphanumeric characters; no OpenCC"}


def replay_preview(samples: np.ndarray, engine: Any, annotation: dict, *,
                   realtime: bool = False) -> dict:
    events, committed = [], []
    previous, submitted = "", 0
    start, cpu_start = time.perf_counter(), time.process_time()
    for offset in range(0, len(samples), BLOCK):
        chunk = samples[offset:offset + BLOCK]
        submitted += len(chunk)
        if realtime:
            time.sleep(max(0, start + submitted / RATE - time.perf_counter()))
        before = time.perf_counter()
        value = engine.accept(chunk, sample_rate=RATE)
        now = time.perf_counter()
        if value.text != previous or value.is_endpoint:
            events.append({"audio_ms": submitted * 1000 / RATE,
                           "wall_ms": (now - start) * 1000,
                           "call_ms": (now - before) * 1000,
                           "text": value.text, "endpoint": value.is_endpoint,
                           "kind": "accept"})
        previous = value.text
        if value.is_endpoint:
            committed.append(value.text)
            previous = ""
    before = time.perf_counter()
    final = engine.finish()
    finished = time.perf_counter()
    events.append({"audio_ms": submitted * 1000 / RATE,
                   "wall_ms": (finished - start) * 1000,
                   "call_ms": (finished - before) * 1000,
                   "text": final.text, "endpoint": True, "kind": "finish"})
    transcript = " ".join([*committed, final.text]).strip()
    onset = annotation.get("speech_start_ms")
    first = next((e for e in events if e["text"]), None)
    after = next((e for e in events if e["text"] and onset is not None
                  and e["audio_ms"] >= onset), None)
    elapsed = finished - start
    return {
        "submitted_samples": submitted, "events": events, "final_preview_text": transcript,
        "first_text_audio_ms": first["audio_ms"] if first else None,
        "first_nonempty_after_onset_audio_ms": after["audio_ms"] - onset if after else None,
        "first_nonempty_after_onset_engine_ms": (
            after["wall_ms"] - onset if after and realtime else None),
        "pre_onset_nonempty_events": (
            sum(bool(e["text"]) and e["audio_ms"] < onset for e in events)
            if onset is not None else None),
        "text_edits": text_edits(annotation["reference_text"], transcript)
        if "reference_text" in annotation else None,
        "nonempty_events": sum(bool(e["text"]) for e in events),
        "preview_flush_ms": (finished - before) * 1000,
        "final_asr_latency_ms": None, "ui_latency_ms": None,
        "wall_seconds": elapsed, "cpu_seconds": time.process_time() - cpu_start,
        "replay_rtf": elapsed / (len(samples) / RATE),
        "timing_mode": "paced_engine" if realtime else "accelerated_engine",
        "limits": "No capture, IPC, finalizer or UI. First nonempty is not first correct text. "
        "Insertions are not automatically hallucinations. Flush is not final ASR latency.",
    }


def merged_ranges(ranges: list[list[int]], length: int) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted((max(0, a), min(length, b)) for a, b in ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def missing_ranges(wanted: list[list[int]], present: list[list[int]], length: int) -> list:
    result = []
    for start, end in merged_ranges(wanted, length):
        cursor = start
        for left, right in merged_ranges(present, length):
            if right <= cursor or left >= end:
                continue
            if left > cursor:
                result.append([cursor, left])
            cursor = max(cursor, min(end, right))
        if cursor < end:
            result.append([cursor, end])
    return result


class ObservedVad(SherpaSileroVadSegmenter):
    def _history_slice(self, start: int, end: int, fallback: Any) -> Any:
        native = np.asarray(fallback, dtype=np.float32).reshape(-1)
        result = super()._history_slice(start, end, fallback)
        native_start = end - len(native)
        self.observations.append({
            "native_range": [native_start, end], "decision_sample": self._cursor,
            "native_matches_source": bool(np.array_equal(native, self.source[native_start:end])),
        })
        return result


def verify_flac(segments: list, source: np.ndarray, root: Path) -> list[dict]:
    import soundfile as sf

    spool = FlacSpool(root, limit_bytes=max(1024 * 1024, len(source) * 16))
    reports = []
    for index, segment in enumerate(segments):
        path = spool.write(segment.samples, sample_rate=RATE, segment_id=f"segment-{index}")
        restored, rate = sf.read(path, dtype="float32")
        values = np.asarray(segment.samples, dtype=np.float32)
        # PCM16 is quantized/clipped; exact float equality is not its contract.
        expected = np.clip(values, -1, 1 - 1 / 32768)
        frames_match = len(restored) == len(values) and rate == RATE
        error = float(np.max(np.abs(restored - expected))) if frames_match and len(values) else None
        reports.append({"range": [segment.start_sample, segment.end_sample],
                        "source_matches": bool(np.array_equal(
                            values, source[segment.start_sample:segment.end_sample])),
                        "range_matches_length": segment.end_sample - segment.start_sample
                        == len(values), "flac_frames_match": frames_match,
                        "flac_max_abs_quantization_error": error,
                        "flac_within_pcm16_step": error is not None and error <= 1 / 32768})
    return reports


def replay_coverage(samples: np.ndarray, vad: Any, root: Path) -> dict:
    vad.source, vad.observations = samples, []
    segments, submitted = [], 0
    for offset in range(0, len(samples), BLOCK):
        chunk = samples[offset:offset + BLOCK]
        segments.extend(vad.accept(chunk))
        submitted += len(chunk)
    segments.extend(vad.flush())
    flac = verify_flac(segments, samples, root)
    if len(flac) != len(vad.observations):
        raise ValueError("native observation count differs from wrapper segment count")
    for row, observation in zip(flac, vad.observations, strict=True):
        row["native_missing_ranges"] = missing_ranges(
            [observation["native_range"]], [row["range"]], len(samples))
    native = [item["native_range"] for item in vad.observations]
    wrapper = [item["range"] for item in flac]
    lost = missing_ranges(native, wrapper, len(samples))
    verified_flac = [item["range"] for item in flac if item["source_matches"]
                     and item["range_matches_length"] and item["flac_frames_match"]
                     and item["flac_within_pcm16_step"]]
    return {"submitted_samples": submitted, "native_observations": vad.observations,
            "preservation_passed": submitted == len(samples) and not lost
            and all(item["native_matches_source"] for item in vad.observations)
            and len(verified_flac) == len(flac)
            and not any(item["native_missing_ranges"] for item in flac),
            "wrapper_and_flac": flac, "native_ranges": merged_ranges(native, len(samples)),
            "wrapper_ranges": merged_ranges(wrapper, len(samples)),
            "native_missing_from_wrapper_ranges": lost,
            "native_missing_from_wrapper_samples": sum(b - a for a, b in lost),
            "verified_flac_ranges": merged_ranges(verified_flac, len(samples)),
            "native_missing_from_verified_flac_ranges": missing_ranges(
                native, verified_flac, len(samples)),
            "vad_excluded_ranges": missing_ranges([[0, len(samples)]], native, len(samples)),
            "lost_vocal_samples": None, "final_asr_latency_ms": None, "ui_latency_ms": None,
            "coverage_limits": "VAD selections are not human vocal annotations. Excluded ranges "
            "are VAD decisions, not proof of silence. Temporary production FLAC only; no SQLite, "
            "capture, finalizer, or crash recovery verification."}


def replay_final(coverage: dict, root: Path, engine: Any, annotation: dict) -> dict:
    """Consume coverage mode's temporary production FLAC; no preview fallback text."""
    rows, observations = coverage["wrapper_and_flac"], coverage["native_observations"]
    events = []
    load_ms = load_cpu_ms = None
    memory_after_load = None
    if rows:
        before, cpu = time.perf_counter(), time.process_time()
        engine.warmup()
        load_ms = (time.perf_counter() - before) * 1000
        load_cpu_ms = (time.process_time() - cpu) * 1000
        memory_after_load = memory_bytes()
    phase_start = time.perf_counter()
    origin = datetime(2000, 1, 1, tzinfo=UTC)  # Synthetic UTC anchor; never capture time.
    for index, (row, observed) in enumerate(zip(rows, observations, strict=True)):
        start, end = row["range"]
        segment = CapturedSegment(
            segment_id=f"segment-{index}", audio_path=root / f"segment-{index}.flac",
            started_at_utc=origin + timedelta(seconds=start / RATE),
            ended_at_utc=origin + timedelta(seconds=end / RATE),
            sample_rate=RATE, duration_ms=round((end - start) * 1000 / RATE),
        )
        before, cpu = time.perf_counter(), time.process_time()
        result = engine.transcribe(segment)
        finished, cpu_finished = time.perf_counter(), time.process_time()
        events.append({
            "source_range": [start, end],
            "vad_decision_audio_ms": observed["decision_sample"] * 1000 / RATE,
            "transcribe_wall_ms": (finished - before) * 1000,
            "transcribe_process_cpu_ms": (cpu_finished - cpu) * 1000,
            "engine_reported_compute_ms": result.latency_ms,
            "ready_wall_since_final_phase_ms": (finished - phase_start) * 1000,
            "text": result.normalized_text, "raw_text": result.raw_text,
            "success": result.success, "error": result.error,
            "engine_profile": result.engine_profile,
            "active_device": engine.active_device,
            "compute_type": engine.active_compute_type,
            "fallback_reason": engine.last_fallback_reason,
            "deadline_exceeded": engine.last_deadline_exceeded,
            "memory": memory_bytes(),
        })
    ranges = [row["range"] for row in rows]
    union_length = sum(b - a for a, b in merged_ranges(ranges, coverage["submitted_samples"]))
    overlap = sum(b - a for a, b in ranges) - union_length
    text = " ".join(event["text"] for event in events if event["success"]).strip()
    return {
        "final_events": events, "final_engine_load_ms": load_ms,
        "final_engine_load_cpu_ms": load_cpu_ms, "final_memory_after_load": memory_after_load,
        "final_transcribe_wall_ms": sum(e["transcribe_wall_ms"] for e in events),
        "final_transcribe_process_cpu_ms": sum(e["transcribe_process_cpu_ms"] for e in events),
        "final_success_count": sum(e["success"] for e in events),
        "final_failure_count": sum(not e["success"] for e in events),
        "final_text_segment_join": text, "final_overlap_samples": overlap,
        "final_text_edits": text_edits(annotation["reference_text"], text)
        if "reference_text" in annotation and overlap == 0 else None,
        "final_asr_latency_ms": None, "ui_latency_ms": None,
        "final_timing_mode": "offline_sequential_after_vad",
        "final_limits": "Same process resources only; no GPU memory or screen timing. "
        "Synthetic UTC anchor. No production queue/controller overlap reconciliation; "
        "joined-text edits withheld when audio overlaps. Empty preview and no hotwords. "
        "VAD decision file time and offline transcribe duration do not establish live latency.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preview", "coverage", "final"), required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--audio", type=Path)
    inputs.add_argument("--silence-seconds", type=float)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--annotation", type=Path)
    parser.add_argument("--threads", type=int, choices=(1, 2, 4), default=1,
                        help="preview threads only; final uses the production engine settings")
    parser.add_argument("--final-device", choices=("cpu", "cuda"), default="cpu",
                        help="requested final device; production CUDA fallback is reported")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-segment-ms", type=int, default=28_000)
    parser.add_argument("--endpoint-silence-ms", type=int, default=2_000)
    parser.add_argument("--pre-roll-ms", type=int, default=300)
    parser.add_argument("--overlap-ms", type=int, default=1_000)
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new report path")
    if args.silence_seconds is not None and (
        not math.isfinite(args.silence_seconds) or not 0 < args.silence_seconds <= 3600
    ):
        parser.error("silence-seconds must be in (0, 3600]")
    if (args.endpoint_silence_ms <= 0 or args.max_segment_ms <= args.endpoint_silence_ms
            or not 0 <= args.pre_roll_ms < args.max_segment_ms
            or not 0 <= args.overlap_ms < args.max_segment_ms):
        parser.error("invalid VAD duration settings")
    if args.mode != "preview" and args.realtime:
        parser.error("realtime only applies to preview mode")
    samples, source_rate = load_audio(args.audio) if args.audio else (
        np.zeros(round(args.silence_seconds * RATE), dtype=np.float32), RATE)
    if not len(samples):
        parser.error("input must contain at least one prepared sample")
    pcm_hash = hashlib.sha256(samples.tobytes()).hexdigest()
    annotation = trusted_annotation(
        json.loads(args.annotation.read_text(encoding="utf-8-sig")) if args.annotation else {},
        pcm_hash, len(samples) * 1000 / RATE)
    if args.audio is None:
        annotation = {"reference_text": ""}
    models = args.models_dir
    started, cpu_started = time.perf_counter(), time.process_time()
    report = {"schema_version": 1, "mode": args.mode, "source_sample_rate": source_rate,
              "python": platform.python_version(), "platform": platform.platform(),
              "packages": {name: version(name) for name in (
                  "sherpa-onnx", "numpy", "soundfile", "faster-whisper", "ctranslate2", "opencc",
                  "soxr")},
              "source_modules": source_metadata(),
              "prepared_sample_rate": RATE, "source_samples": len(samples),
              "pcm_sha256": pcm_hash, "annotation_trusted": bool(annotation),
              "annotation_source": "generated_silence" if args.audio is None
              else "manual" if annotation else None,
              "duration_ms": len(samples) * 1000 / RATE, "chunk_ms": 100}
    if args.mode == "preview":
        folder = models / PREVIEW_DIRECTORY
        files = [folder / name for name in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")]
        report["model_sha256"] = {p.name: sha256(p) for p in files}
        engine = SherpaPreviewEngine(folder, num_threads=args.threads,
                                     endpoint_silence_ms=args.endpoint_silence_ms)
        try:
            before = time.perf_counter()
            engine.warmup()
            report["load_ms"] = (time.perf_counter() - before) * 1000
            report["memory_after_load"] = memory_bytes()
            report.update(replay_preview(samples, engine, annotation, realtime=args.realtime))
            report["threads"] = args.threads
            report["preview_settings"] = {"provider": "cpu", "precision": "int8",
                                          "endpoint_silence_ms": args.endpoint_silence_ms,
                                          "max_utterance_seconds": 300}
        finally:
            engine.close()
    else:
        from auto_speech_journal.native_runtime import register_onnxruntime_dll_directory

        register_onnxruntime_dll_directory()
        model = models / "silero-vad" / "silero_vad.onnx"
        report["model_sha256"] = {model.name: sha256(model)}
        settings = {"max_segment_ms": args.max_segment_ms,
                    "endpoint_silence_ms": args.endpoint_silence_ms,
                    "pre_roll_ms": args.pre_roll_ms, "overlap_ms": args.overlap_ms}
        report["vad_settings"] = settings
        vad = ObservedVad(model, **settings)
        vad.self_test()
        with tempfile.TemporaryDirectory(prefix="caption-benchmark-") as temporary:
            report.update(replay_coverage(samples, vad, Path(temporary)))
            if args.mode == "final":
                final_folder = resolve_model_paths(models).final_dir
                report["final_model_sha256"] = {
                    name: sha256(final_folder / name) for name in FINAL_SPEC.required_files}
                config = AppConfig()
                engine = FasterWhisperFinalizer(
                    final_folder, language=config.language, prefer_cuda=args.final_device == "cuda",
                    cuda_compute_type=config.model.final_compute_type,
                    cpu_compute_type=config.model.cpu_compute_type,
                    deadline_ms=config.final_deadline_ms,
                )
                report["final_settings"] = {
                    "requested_device": args.final_device, "language": config.language,
                    "cpu_compute_type": config.model.cpu_compute_type,
                    "cuda_compute_type": config.model.final_compute_type,
                    "deadline_ms": config.final_deadline_ms, "beam_size": engine.beam_size,
                    "preview_fallback_text": "", "hotwords": [],
                }
                try:
                    report.update(replay_final(report, Path(temporary), engine, annotation))
                finally:
                    engine.close()
    report["total_cpu_seconds_including_load"] = time.process_time() - cpu_started
    report["total_wall_seconds_including_load"] = time.perf_counter() - started
    report["memory_at_end"] = memory_bytes()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
