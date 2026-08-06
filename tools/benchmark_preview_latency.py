"""Replay audio files through the local streaming preview model.

Run from a source checkout with::

    $env:PYTHONPATH = "src"
    uv run --no-sync python tools/benchmark_preview_latency.py `
      --audio samples/one.wav --audio samples/two.flac `
      --max-first-p95-ms 1200

The benchmark feeds audio as fast as the recognizer can consume it.  The
``first_audio_time_ms`` metric is the amount of source audio submitted when the
first non-empty hypothesis appears; ``wall_compute_ms`` is elapsed recognizer
wall time, excluding file loading and model warm-up.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from auto_speech_journal.audio import TARGET_SAMPLE_RATE, StreamingResampler
from auto_speech_journal.model_download import resolve_model_paths
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.preview_engine import SherpaPreviewEngine

CHUNK_MS = 100


class PreviewEngineLike(Protocol):
    def reset(self) -> None: ...

    def accept(self, samples: Any, *, sample_rate: int | None = None) -> Any: ...

    def finish(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreviewLatencyResult:
    audio: str
    source_sample_rate: int
    duration_ms: float
    first_text: str | None
    first_audio_time_ms: float | None
    wall_compute_ms: float | None


def prepare_audio(samples: Any, sample_rate: int) -> np.ndarray:
    """Downmix an audio array and resample it to the preview engine's rate."""

    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 2:
        values = np.mean(values, axis=1, dtype=np.float32)
    elif values.ndim != 1:
        raise ValueError("audio must be a mono vector or frames-by-channels matrix")
    mono = np.ascontiguousarray(values, dtype=np.float32).reshape(-1)
    if not len(mono):
        raise ValueError("audio contains no samples")
    if not np.all(np.isfinite(mono)):
        raise ValueError("audio contains NaN or infinite samples")
    if sample_rate != TARGET_SAMPLE_RATE:
        mono = StreamingResampler(sample_rate, TARGET_SAMPLE_RATE).process(
            mono,
            final=True,
        )
    return np.ascontiguousarray(mono, dtype=np.float32).reshape(-1)


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Load a WAV/FLAC file and return 16 kHz mono samples plus its source rate."""

    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError("soundfile is required to load benchmark audio") from exc

    source, sample_rate = sf.read(
        str(path),
        dtype="float32",
        always_2d=True,
    )
    return prepare_audio(source, int(sample_rate)), int(sample_rate)


def _hypothesis_text(hypothesis: Any) -> str:
    if hypothesis is None:
        return ""
    value = hypothesis if isinstance(hypothesis, str) else getattr(hypothesis, "text", "")
    return str(value).strip()


def _best_effort_recreate_stream(engine: PreviewEngineLike) -> None:
    """Finish the current stream, falling back to reset+finish after a decode error."""

    try:
        engine.finish()
        return
    except Exception:
        pass
    with suppress(Exception):
        engine.reset()
    with suppress(Exception):
        engine.finish()


def benchmark_samples(
    audio_name: str,
    samples: Any,
    engine: PreviewEngineLike,
    *,
    source_sample_rate: int = TARGET_SAMPLE_RATE,
    monotonic: Callable[[], float] = time.perf_counter,
) -> PreviewLatencyResult:
    """Measure first-hypothesis latency for one already prepared audio array."""

    values = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if not len(values):
        raise ValueError("audio contains no samples")

    chunk_samples = TARGET_SAMPLE_RATE * CHUNK_MS // 1_000
    first_text: str | None = None
    first_audio_time_ms: float | None = None
    wall_compute_ms: float | None = None
    stream_finished = False

    try:
        engine.reset()
        started = monotonic()
        submitted = 0
        for offset in range(0, len(values), chunk_samples):
            chunk = values[offset : offset + chunk_samples]
            hypothesis = engine.accept(chunk, sample_rate=TARGET_SAMPLE_RATE)
            submitted += len(chunk)
            elapsed_ms = (monotonic() - started) * 1_000
            text = _hypothesis_text(hypothesis)
            if text:
                first_text = text
                first_audio_time_ms = submitted * 1_000 / TARGET_SAMPLE_RATE
                wall_compute_ms = elapsed_ms
                break

        if first_text is None:
            hypothesis = engine.finish()
            stream_finished = True
            elapsed_ms = (monotonic() - started) * 1_000
            text = _hypothesis_text(hypothesis)
            if text:
                first_text = text
                first_audio_time_ms = len(values) * 1_000 / TARGET_SAMPLE_RATE
                wall_compute_ms = elapsed_ms

        return PreviewLatencyResult(
            audio=audio_name,
            source_sample_rate=source_sample_rate,
            duration_ms=round(len(values) * 1_000 / TARGET_SAMPLE_RATE, 3),
            first_text=first_text,
            first_audio_time_ms=(
                round(first_audio_time_ms, 3) if first_audio_time_ms is not None else None
            ),
            wall_compute_ms=(
                round(wall_compute_ms, 3) if wall_compute_ms is not None else None
            ),
        )
    finally:
        # Sherpa's in-place reset can retain buffered features/results.  A
        # successful finish creates a new stream, so always do it after an
        # early first-hypothesis exit and also attempt it after decode errors.
        if not stream_finished:
            _best_effort_recreate_stream(engine)


def benchmark_files(
    audio_paths: Sequence[Path],
    engine: PreviewEngineLike,
    *,
    loader: Callable[[Path], tuple[np.ndarray, int]] = load_audio,
    monotonic: Callable[[], float] = time.perf_counter,
) -> list[PreviewLatencyResult]:
    results: list[PreviewLatencyResult] = []
    for path in audio_paths:
        samples, source_sample_rate = loader(path)
        results.append(
            benchmark_samples(
                str(path),
                samples,
                engine,
                source_sample_rate=source_sample_rate,
                monotonic=monotonic,
            )
        )
    return results


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Return a linearly interpolated percentile without requiring NumPy output types."""

    if not values:
        return None
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * percent / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _metric_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": round(statistics.median(values), 3) if values else None,
        "p95": (
            round(value, 3) if (value := percentile(values, 95)) is not None else None
        ),
    }


def summarize_results(results: Sequence[PreviewLatencyResult]) -> dict[str, Any]:
    audio_times = [
        result.first_audio_time_ms
        for result in results
        if result.first_audio_time_ms is not None
    ]
    wall_times = [
        result.wall_compute_ms
        for result in results
        if result.wall_compute_ms is not None
    ]
    return {
        "file_count": len(results),
        "detected_count": len(audio_times),
        "missing_count": len(results) - len(audio_times),
        "first_audio_time_ms": _metric_summary(audio_times),
        "wall_compute_ms": _metric_summary(wall_times),
    }


def passes_threshold(summary: dict[str, Any], maximum_ms: float) -> bool:
    p95 = summary["first_audio_time_ms"]["p95"]
    return (
        maximum_ms >= 0
        and summary["missing_count"] == 0
        and p95 is not None
        and float(p95) <= maximum_ms
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audio",
        action="append",
        required=True,
        type=Path,
        help="WAV or FLAC speech sample; repeat for multiple files",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=AppPaths.defaults().models_dir,
        help="local model root (default: AutoSpeechJournal runtime models)",
    )
    parser.add_argument(
        "--max-first-p95-ms",
        type=float,
        help="fail if first_audio_time_ms P95 exceeds this value or any file has no text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_first_p95_ms is not None and args.max_first_p95_ms < 0:
        _parser().error("--max-first-p95-ms must be non-negative")

    engine: SherpaPreviewEngine | None = None
    try:
        models = resolve_model_paths(args.models_dir.expanduser().resolve())
        engine = SherpaPreviewEngine(model_dir=models.preview_dir)
        engine.warmup()
        audio_paths = [path.expanduser().resolve() for path in args.audio]
        results = benchmark_files(audio_paths, engine)
        summary = summarize_results(results)
        threshold_passed = (
            True
            if args.max_first_p95_ms is None
            else passes_threshold(summary, args.max_first_p95_ms)
        )
        payload = {
            "status": "passed" if threshold_passed else "failed",
            "chunk_ms": CHUNK_MS,
            "target_sample_rate": TARGET_SAMPLE_RATE,
            "threshold": (
                None
                if args.max_first_p95_ms is None
                else {
                    "max_first_p95_ms": args.max_first_p95_ms,
                    "passed": threshold_passed,
                }
            ),
            "files": [asdict(result) for result in results],
            "summary": summary,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if threshold_passed else 1
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    finally:
        if engine is not None:
            engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
