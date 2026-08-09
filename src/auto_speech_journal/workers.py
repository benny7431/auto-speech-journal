from __future__ import annotations

import importlib
import math
import multiprocessing as mp
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .audio import (
    AudioChunk,
    FlacSpool,
    SherpaSileroVadSegmenter,
    SpeechAudio,
    VadSegmenter,
    create_vad_segmenter,
)
from .config import AppConfig, MicrophoneMode, MicrophoneSelection
from .finalizer_engine import FasterWhisperFinalizer
from .input_routing import (
    CaptureCandidate as _CaptureCandidate,
)
from .input_routing import (
    InputRouteResolution as _InputRouteResolution,
)
from .input_routing import (
    RouteCoordinator,
    RouteState,
    device_key,
)
from .model_download import resolve_model_paths
from .paths import AppPaths
from .preview_engine import PreviewHypothesis, SherpaPreviewEngine
from .preview_stream import (
    PreviewCoordinator,
    PreviewState,
    as_preview_chunk,
    hypothesis_texts,
)
from .spool_retry import SpoolCoordinator, SpoolState
from .types import (
    AudioLevelUpdate,
    CapturedSegment,
    FinalResult,
    InputRouteRequest,
    InputRouteUpdate,
    PartialUpdate,
    PreviewAudioChunk,
    PreviewEndpointResult,
    PreviewFinalize,
    Severity,
    WorkerCommand,
    WorkerCommandKind,
    WorkerKind,
    WorkerState,
    WorkerStatus,
)

JournalEvent = (
    AudioLevelUpdate
    | PartialUpdate
    | CapturedSegment
    | FinalResult
    | WorkerStatus
    | InputRouteUpdate
)
RawWorkerEvent = JournalEvent | PreviewEndpointResult


@dataclass(frozen=True, slots=True)
class RealtimeModelProbe:
    preview_loaded: bool
    vad_loaded: bool
    normalized_example: str


@dataclass(slots=True)
class _LoopState:
    """Whether the recorder wants to record, and the work it owes before it may exit.

    `stop_requested` starts a drain rather than ending the loop: the loop keeps turning
    until every pending flush and spool write has landed, which is what
    `RecorderShutdownPending` reports back to the supervisor.
    """

    desired_recording: bool = False
    running: bool = True
    iterations: int = 0
    stop_requested: bool = False
    stop_capture_pending: bool = False
    stream_flush_pending: bool = False
    deferred_gap_chunk: AudioChunk | None = None
    capture_degraded: bool = False


@dataclass(slots=True)
class _SegmentState:
    """The segment currently being accumulated, plus the stream clock it is timed against.

    `stream_origin` anchors sample offsets to wall time; clearing it (see
    `clear_stream_clock`) marks the audio stream as discontinuous, so the next chunk
    starts a fresh timeline instead of being dated from the old origin.
    """

    stream_origin: datetime | None = None
    last_chunk_end_utc: datetime | None = None
    current_id: str | None = None
    current_started_at: datetime | None = None
    current_preview: str = ""
    current_preview_raw: str = ""
    preview_prefix: str = ""
    preview_raw_prefix: str = ""
    previous_forced_segment_id: str | None = None
    previous_forced_end_sample: int | None = None

    def clear_stream_clock(self) -> None:
        self.stream_origin = None
        self.last_chunk_end_utc = None


class WorkerBackpressure(RuntimeError):
    pass


class RecorderShutdownPending(RuntimeError):
    """Graceful exit is waiting for captured audio to become durable."""

    pass


def _audio_levels_dbfs(samples: Any) -> tuple[float, float]:
    """Return finite, UI-safe RMS and peak levels in dBFS."""

    np = importlib.import_module("numpy")
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not len(values):
        return -120.0, -120.0
    finite = np.where(np.isfinite(values), values, 0.0)
    peak = float(np.max(np.abs(finite)))
    rms = float(np.sqrt(np.mean(np.square(finite, dtype=np.float64))))

    def to_dbfs(value: float) -> float:
        if value <= 1e-6:
            return -120.0
        return max(-120.0, min(0.0, 20.0 * math.log10(value)))

    peak_dbfs = to_dbfs(peak)
    return min(to_dbfs(rms), peak_dbfs), peak_dbfs


class _NoopChildGuard:
    def assign(self, _process: Any) -> None:
        return None

    def close(self) -> None:
        return None


class _WindowsKillOnCloseJob:
    """Own a non-inheritable Job Object that kills assigned children on close."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows")
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, process: Any) -> None:
        if self._handle is None:
            raise RuntimeError("child Job Object is closed")
        import ctypes
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(process.sentinel))
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.CloseHandle(handle)


def _create_child_guard() -> Any:
    if os.name == "nt":
        return _WindowsKillOnCloseJob()
    return _NoopChildGuard()


def _watch_parent(parent_pid: int) -> None:
    """Exit immediately when the exact parent process object becomes signaled."""

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if not handle:
            os._exit(70)
        try:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel32.CloseHandle(handle)
        os._exit(70)

    while os.getppid() == parent_pid:
        time.sleep(0.5)
    os._exit(70)


def _start_parent_watchdog(parent_pid: int | None) -> None:
    if parent_pid is None or parent_pid <= 0:
        return
    threading.Thread(
        target=_watch_parent,
        args=(parent_pid,),
        name="speech-journal-parent-watchdog",
        daemon=True,
    ).start()


def _queue_size(channel: Any) -> int:
    try:
        return max(0, int(channel.qsize()))
    except (AttributeError, NotImplementedError, OSError):
        return 0


def _emit(channel: Any, event: JournalEvent, *, replaceable: bool = False) -> bool:
    if replaceable:
        try:
            channel.put_nowait(event)
            return True
        except queue.Full:
            return False
    while True:
        try:
            channel.put(event, timeout=0.5)
            return True
        except queue.Full:
            continue


def _status(
    channel: Any,
    worker: WorkerKind,
    state: WorkerState,
    message: str = "",
    *,
    severity: Severity = Severity.INFO,
    queue_size: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    _emit(
        channel,
        WorkerStatus(
            worker=worker,
            state=state,
            message=message,
            severity=severity,
            queue_size=queue_size,
            metadata=metadata or {},
        ),
    )


def _segment_id(_started_at: datetime) -> str:
    return str(uuid.uuid4())


def _build_preview(config: AppConfig, paths: AppPaths) -> SherpaPreviewEngine:
    models = resolve_model_paths(paths.models_dir)
    return SherpaPreviewEngine(
        models.preview_dir,
        sample_rate=config.audio_sample_rate,
        endpoint_silence_ms=config.endpoint_silence_ms,
        provider="cpu",
    )


def _build_vad(config: AppConfig, paths: AppPaths) -> tuple[VadSegmenter, str | None]:
    models = resolve_model_paths(paths.models_dir)
    return create_vad_segmenter(
        models.vad_model,
        sample_rate=config.audio_sample_rate,
        endpoint_silence_ms=config.endpoint_silence_ms,
        max_segment_ms=config.max_segment_ms,
        pre_roll_ms=config.pre_roll_ms,
        overlap_ms=config.segment_overlap_ms,
    )


def _build_spool(config: AppConfig, paths: AppPaths) -> FlacSpool:
    spool = FlacSpool(paths.spool_dir, limit_bytes=config.spool_limit_bytes)
    spool.recover_partials()
    return spool


def _build_finalizer(config: AppConfig, paths: AppPaths) -> FasterWhisperFinalizer:
    models = resolve_model_paths(paths.models_dir)
    return FasterWhisperFinalizer(
        models.final_dir,
        language=config.language,
        cuda_compute_type=config.model.final_compute_type,
        cpu_compute_type=config.model.cpu_compute_type,
        deadline_ms=config.final_deadline_ms,
    )


def probe_realtime_models(
    config: AppConfig,
    models_dir: Path,
    *,
    preview_factory: Callable[[], Any] | None = None,
    vad_factory: Callable[[], Any] | None = None,
    numpy_module: Any | None = None,
) -> RealtimeModelProbe:
    models = resolve_model_paths(models_dir)
    preview = (
        preview_factory()
        if preview_factory is not None
        else SherpaPreviewEngine(
            models.preview_dir,
            sample_rate=config.audio_sample_rate,
            endpoint_silence_ms=config.endpoint_silence_ms,
            provider="cpu",
        )
    )
    vad = (
        vad_factory()
        if vad_factory is not None
        else SherpaSileroVadSegmenter(
            models.vad_model,
            sample_rate=config.audio_sample_rate,
            endpoint_silence_ms=config.endpoint_silence_ms,
            max_segment_ms=config.max_segment_ms,
            pre_roll_ms=config.pre_roll_ms,
            overlap_ms=config.segment_overlap_ms,
        )
    )
    np = numpy_module or importlib.import_module("numpy")
    silence = np.zeros(round(config.audio_sample_rate * 0.1), dtype=np.float32)
    try:
        preview.warmup()
        preview.accept(silence, sample_rate=config.audio_sample_rate)
        preview.finish()
        normalized = preview.normalize_text("后台软件")
        if normalized != "後臺軟件":
            raise RuntimeError(f"OpenCC s2tw probe returned unexpected text: {normalized}")
        vad.self_test()
        vad.accept(silence)
        vad.flush()
        return RealtimeModelProbe(
            preview_loaded=True,
            vad_loaded=True,
            normalized_example=normalized,
        )
    finally:
        with suppress(Exception):
            preview.close()


def _preview_loop(
    config: AppConfig,
    paths: AppPaths,
    input_queue: Any,
    event_queue: Any,
    *,
    engine_factory: Callable[[], Any] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Decode bounded preview IPC independently from capture/VAD/FLAC."""

    _status(event_queue, WorkerKind.PREVIEW, WorkerState.STARTING, "initializing preview")
    try:
        engine = (engine_factory or (lambda: _build_preview(config, paths)))()
        if hasattr(engine, "warmup"):
            engine.warmup()
    except Exception as exc:
        _status(
            event_queue,
            WorkerKind.PREVIEW,
            WorkerState.ERROR,
            f"preview initialization failed: {exc}",
            severity=Severity.ERROR,
        )
        raise RuntimeError(f"preview initialization failed: {exc}") from exc

    _status(event_queue, WorkerKind.PREVIEW, WorkerState.READY, "preview ready")
    active_segment_id: str | None = None
    active_started_at: datetime | None = None
    raw_prefix = ""
    normalized_prefix = ""
    last_raw = ""
    last_normalized = ""
    last_emitted_raw = ""
    last_emitted_normalized = ""
    last_emit = 0.0
    has_emitted = False
    degraded_active = False

    def reset_state() -> None:
        nonlocal active_segment_id, active_started_at, raw_prefix, normalized_prefix
        nonlocal last_raw, last_normalized, last_emitted_raw, last_emitted_normalized
        nonlocal last_emit, has_emitted
        active_segment_id = None
        active_started_at = None
        raw_prefix = ""
        normalized_prefix = ""
        last_raw = ""
        last_normalized = ""
        last_emitted_raw = ""
        last_emitted_normalized = ""
        last_emit = 0.0
        has_emitted = False
        with suppress(Exception):
            engine.reset()

    def begin(chunk: PreviewAudioChunk) -> None:
        nonlocal active_segment_id, active_started_at
        if active_segment_id == chunk.segment_id:
            return
        reset_state()
        active_segment_id = chunk.segment_id
        active_started_at = chunk.segment_started_at_utc

    def report_recovered() -> None:
        nonlocal degraded_active
        if degraded_active:
            degraded_active = False
            _status(
                event_queue,
                WorkerKind.PREVIEW,
                WorkerState.READY,
                "streaming preview recovered",
                queue_size=_queue_size(input_queue),
            )

    try:
        while True:
            item = input_queue.get()
            if isinstance(item, WorkerCommand):
                if item.kind == WorkerCommandKind.STOP:
                    break
                if item.kind == WorkerCommandKind.UPDATE_HOTWORDS:
                    applied = bool(engine.update_hotwords(list(item.payload or [])))
                    if item.payload and not applied:
                        _status(
                            event_queue,
                            WorkerKind.PREVIEW,
                            WorkerState.READY,
                            "preview hotwords are unsupported; final transcription will use them",
                        )
                continue

            if isinstance(item, PreviewAudioChunk):
                begin(item)
                try:
                    hypothesis: PreviewHypothesis = engine.accept(
                        item.samples,
                        sample_rate=item.sample_rate,
                    )
                    raw_tail, normalized_tail = hypothesis_texts(hypothesis)
                    current_raw = (raw_prefix + raw_tail).strip()
                    current_normalized = (normalized_prefix + normalized_tail).strip()
                    if hypothesis.is_endpoint:
                        raw_prefix = current_raw
                        normalized_prefix = current_normalized
                    changed_since_emit = (
                        current_raw != last_emitted_raw
                        or current_normalized != last_emitted_normalized
                    )
                    due = monotonic() - last_emit >= config.preview_interval_ms / 1000
                    should_emit = current_normalized and (
                        (changed_since_emit and (not has_emitted or due))
                        or hypothesis.is_endpoint
                    )
                    if should_emit and _emit(
                        event_queue,
                        PartialUpdate(
                            segment_id=item.segment_id,
                            text=current_normalized,
                            raw_text=current_raw,
                            started_at_utc=active_started_at
                            or item.segment_started_at_utc,
                        ),
                        replaceable=True,
                    ):
                        last_emit = monotonic()
                        has_emitted = True
                        last_emitted_raw = current_raw
                        last_emitted_normalized = current_normalized
                    last_raw = current_raw
                    last_normalized = current_normalized
                    report_recovered()
                except Exception as exc:
                    degraded_active = True
                    _status(
                        event_queue,
                        WorkerKind.PREVIEW,
                        WorkerState.DEGRADED,
                        f"streaming preview failed for {item.segment_id}: {exc}",
                        severity=Severity.WARNING,
                        queue_size=_queue_size(input_queue),
                        metadata={"segment_id": item.segment_id},
                    )
                    reset_state()
                continue

            if not isinstance(item, PreviewFinalize):
                continue
            segment = item.segment
            raw_text = last_raw
            normalized_text = last_normalized
            success = True
            error: str | None = None
            try:
                if active_segment_id not in (None, segment.segment_id):
                    reset_state()
                if active_segment_id == segment.segment_id:
                    tail = engine.finish()
                    raw_tail, normalized_tail = hypothesis_texts(tail)
                    raw_text = (raw_prefix + raw_tail).strip() or last_raw
                    normalized_text = (
                        (normalized_prefix + normalized_tail).strip() or last_normalized
                    )
                report_recovered()
            except Exception as exc:
                success = False
                error = str(exc)
                degraded_active = True
                _status(
                    event_queue,
                    WorkerKind.PREVIEW,
                    WorkerState.DEGRADED,
                    f"preview endpoint flush failed for {segment.segment_id}: {exc}",
                    severity=Severity.WARNING,
                    queue_size=_queue_size(input_queue),
                    metadata={"segment_id": segment.segment_id},
                )
            _emit(
                event_queue,
                PreviewEndpointResult(
                    segment_id=segment.segment_id,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    success=success,
                    error=error,
                ),
            )
            reset_state()
    finally:
        with suppress(Exception):
            engine.close()
        _status(event_queue, WorkerKind.PREVIEW, WorkerState.STOPPED, "preview stopped")


def _recorder_loop(
    config: AppConfig,
    paths: AppPaths,
    command_queue: Any,
    event_queue: Any,
    preview_queue: Any | None = None,
    *,
    capture_factory: Callable[..., Any] | None = None,
    route_resolver: Callable[..., _InputRouteResolution] | None = None,
    preview_factory: Callable[[], Any] | None = None,
    vad_factory: Callable[[], tuple[Any, str | None]] | None = None,
    spool_factory: Callable[[], Any] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    max_iterations: int | None = None,
) -> None:
    _status(event_queue, WorkerKind.RECORDER, WorkerState.STARTING, "initializing recorder")
    try:
        inline_preview = (
            (preview_factory or (lambda: _build_preview(config, paths)))()
            if preview_queue is None
            else None
        )
        vad, degraded_reason = (vad_factory or (lambda: _build_vad(config, paths)))()
        spool = (spool_factory or (lambda: _build_spool(config, paths)))()
        if inline_preview is not None and hasattr(inline_preview, "warmup"):
            inline_preview.warmup()
    except Exception as exc:
        _status(
            event_queue,
            WorkerKind.RECORDER,
            WorkerState.ERROR,
            f"recorder initialization failed: {exc}",
            severity=Severity.ERROR,
        )
        raise RuntimeError(f"recorder initialization failed: {exc}") from exc

    route_state = RouteState.for_selection(config.microphone)

    _status(event_queue, WorkerKind.RECORDER, WorkerState.READY, "recorder ready")
    if degraded_reason:
        _status(
            event_queue,
            WorkerKind.RECORDER,
            WorkerState.DEGRADED,
            degraded_reason,
            severity=Severity.WARNING,
        )
    for warning in getattr(spool, "recovery_warnings", ()):
        _status(
            event_queue,
            WorkerKind.RECORDER,
            WorkerState.DEGRADED,
            warning,
            severity=Severity.WARNING,
        )
    for recovered in getattr(spool, "recovered_partials", ()):
        try:
            duration = recovered.frame_count / recovered.sample_rate
            ended_at = datetime.fromtimestamp(recovered.path.stat().st_mtime, tz=UTC)
            _emit(
                event_queue,
                CapturedSegment(
                    segment_id=recovered.segment_id,
                    audio_path=recovered.path,
                    started_at_utc=ended_at - timedelta(seconds=duration),
                    ended_at_utc=ended_at,
                    sample_rate=recovered.sample_rate,
                    duration_ms=round(duration * 1000),
                ),
            )
        except Exception as exc:
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.ERROR,
                f"failed to register recovered FLAC {recovered.path}: {exc}",
                severity=Severity.ERROR,
            )

    loop_state = _LoopState()
    segment_state = _SegmentState()
    preview_state = PreviewState()
    spool_state = SpoolState()
    retry_delay = 2.0
    reserve_bytes = round(config.max_segment_ms * config.audio_sample_rate / 1000) * 2

    def reset_segment_state() -> None:
        segment_state.current_id = None
        segment_state.current_started_at = None
        preview_state.reset_for_segment()

    def ensure_segment(started_at: datetime) -> None:
        if segment_state.current_id is None:
            segment_state.current_id = _segment_id(started_at)
            segment_state.current_started_at = started_at

    previews = PreviewCoordinator(
        preview_state,
        config,
        inline_preview=inline_preview,
        preview_queue=preview_queue,
        status=lambda state, message, **kwargs: _status(
            event_queue, WorkerKind.PREVIEW, state, message, **kwargs
        ),
        emit_partial=lambda partial: _emit(event_queue, partial, replaceable=True),
        queue_size=lambda: _queue_size(preview_queue),
    )

    def publish_persisted_segment(segment: CapturedSegment, audio_path: Path) -> None:
        persisted = replace(segment, audio_path=Path(audio_path), preview_pending=False)
        if preview_queue is not None and previews.offer(PreviewFinalize(persisted)):
            persisted = replace(persisted, preview_pending=True)
        _emit(event_queue, persisted)

    def emit_speech_segment(
        speech: SpeechAudio,
        flushed_preview: str = "",
        flushed_preview_raw: str = "",
    ) -> None:
        if segment_state.stream_origin is None:
            return
        started_at = segment_state.stream_origin + timedelta(
            seconds=speech.start_sample / config.audio_sample_rate
        )
        ended_at = segment_state.stream_origin + timedelta(
            seconds=speech.end_sample / config.audio_sample_rate
        )
        ensure_segment(started_at)
        identifier = segment_state.current_id or _segment_id(started_at)
        text = (flushed_preview or preview_state.current_text).strip()
        raw_text = (flushed_preview_raw or preview_state.current_raw_text or text).strip()
        overlap_samples = (
            max(0, segment_state.previous_forced_end_sample - speech.start_sample)
            if segment_state.previous_forced_segment_id is not None
            and segment_state.previous_forced_end_sample is not None
            else 0
        )

        def captured_event(audio_path: Path) -> CapturedSegment:
            return CapturedSegment(
                segment_id=identifier,
                audio_path=audio_path,
                started_at_utc=started_at,
                ended_at_utc=max(ended_at, started_at),
                preview_text=text,
                preview_raw_text=raw_text,
                sample_rate=config.audio_sample_rate,
                duration_ms=round(len(speech.samples) * 1000 / config.audio_sample_rate),
                leading_overlap_ms=round(
                    overlap_samples * 1000 / config.audio_sample_rate
                ),
                previous_segment_id=segment_state.previous_forced_segment_id,
            )

        stored = False
        try:
            stored = spool_writer.write_segment(
                captured_event,
                speech.samples,
                segment_id=identifier,
                forced_endpoint=speech.forced_endpoint,
                end_sample=speech.end_sample,
                expected_path=paths.spool_dir / f"{identifier}.flac",
                monotonic=monotonic,
                on_capture_stop=stop_capture_after_spool_failure,
            )
        finally:
            if stored and speech.forced_endpoint:
                segment_state.previous_forced_segment_id = identifier
                segment_state.previous_forced_end_sample = speech.end_sample
            elif stored:
                segment_state.previous_forced_segment_id = None
                segment_state.previous_forced_end_sample = None
            reset_segment_state()
            if inline_preview is not None:
                with suppress(Exception):
                    inline_preview.reset()

    def flush_current() -> bool:
        flushed, flushed_raw = previews.finish_for_flush()
        try:
            segments = vad.flush()
        except Exception as exc:
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.ERROR,
                f"VAD flush failed: {exc}",
                severity=Severity.ERROR,
            )
            return False
        for speech in segments:
            emit_speech_segment(speech, flushed, flushed_raw)
            flushed = ""
            flushed_raw = ""
        vad.reset()
        if inline_preview is not None:
            inline_preview.reset()
        reset_segment_state()
        previews.clear_preroll()
        segment_state.previous_forced_segment_id = None
        segment_state.previous_forced_end_sample = None
        return not spool_state.pending_writes

    def stop_capture(*, paused: bool) -> bool:
        flushed = flush_current()
        if route_state.capture is not None and getattr(route_state.capture, "running", False):
            try:
                route_state.capture.stop()
            except Exception as exc:
                _status(
                    event_queue,
                    WorkerKind.RECORDER,
                    WorkerState.DEGRADED,
                    f"failed to stop microphone: {exc}",
                    severity=Severity.WARNING,
                )
                return False
        if not flushed:
            return False
        route_state.capture = None
        segment_state.clear_stream_clock()
        _status(
            event_queue,
            WorkerKind.RECORDER,
            WorkerState.PAUSED if paused else WorkerState.READY,
            "recording paused" if paused else "recording stopped",
        )
        return True

    def reset_stream_state() -> None:
        vad.reset()
        if inline_preview is not None:
            inline_preview.reset()
        segment_state.clear_stream_clock()
        loop_state.capture_degraded = False
        reset_segment_state()
        previews.clear_preroll()

    def stop_capture_after_spool_failure() -> None:
        """Stop taking audio we cannot store, without tearing down the route."""
        if route_state.capture is not None:
            with suppress(Exception):
                route_state.capture.stop()

    def reset_stream_after_drain() -> None:
        """Restart the stream once the write backlog clears.

        Deliberately narrower than `reset_stream_state`: the capture never stopped
        being the active device, so neither the degraded flag nor the preview pre-roll
        is cleared here.
        """
        vad.reset()
        if inline_preview is not None:
            inline_preview.reset()
        segment_state.clear_stream_clock()
        reset_segment_state()

    spool_writer = SpoolCoordinator(
        spool_state,
        spool,
        config,
        status=lambda state, message, **kwargs: _status(
            event_queue, WorkerKind.RECORDER, state, message, **kwargs
        ),
        publish_persisted=publish_persisted_segment,
        reset_stream_after_drain=reset_stream_after_drain,
        is_stopping=lambda: loop_state.stop_requested,
        recording_state=lambda: (
            WorkerState.RECORDING
            if loop_state.desired_recording and getattr(route_state.capture, "running", False)
            else WorkerState.READY
        ),
        reserve_bytes=reserve_bytes,
        retry_delay=retry_delay,
    )

    router = RouteCoordinator(
        route_state,
        config,
        emit=lambda event: _emit(event_queue, event),
        status=lambda state, message, **kwargs: _status(
            event_queue, WorkerKind.RECORDER, state, message, **kwargs
        ),
        capture_factory=capture_factory,
        route_resolver=route_resolver,
        flush_current=flush_current,
        reset_stream_state=reset_stream_state,
        is_recording=lambda: loop_state.desired_recording,
        has_pending_writes=lambda: bool(spool_state.pending_writes),
        retry_delay=retry_delay,
    )

    def drain_commands() -> None:
        """Apply every queued command. A STOP begins the shutdown drain."""
        while True:
            try:
                command = command_queue.get_nowait()
            except queue.Empty:
                return
            if not isinstance(command, WorkerCommand):
                continue
            if command.kind in (WorkerCommandKind.START, WorkerCommandKind.RESUME):
                loop_state.desired_recording = True
                route_state.next_start_attempt = 0.0
            elif command.kind == WorkerCommandKind.PAUSE:
                loop_state.desired_recording = False
                loop_state.stop_capture_pending = not stop_capture(paused=True)
            elif command.kind == WorkerCommandKind.UPDATE_HOTWORDS:
                hotwords = list(command.payload or [])
                applied = (
                    bool(inline_preview.update_hotwords(hotwords))
                    if inline_preview is not None
                    else True
                )
                if hotwords and inline_preview is not None and not applied:
                    _status(
                        event_queue,
                        WorkerKind.PREVIEW,
                        (
                            WorkerState.RECORDING
                            if loop_state.desired_recording
                            else WorkerState.READY
                        ),
                        "preview hotwords are unsupported; final transcription will use them",
                    )
            elif command.kind in {
                WorkerCommandKind.RECONFIGURE_INPUT,
                WorkerCommandKind.RETRY_PREFERRED_INPUT,
            }:
                request = command.payload
                if not isinstance(request, InputRouteRequest):
                    _status(
                        event_queue,
                        WorkerKind.RECORDER,
                        WorkerState.DEGRADED,
                        "ignored malformed microphone route request",
                        severity=Severity.WARNING,
                    )
                    continue
                if request.request_id in route_state.seen_request_ids:
                    router.publish_route(
                        request_id=request.request_id,
                        switching=(
                            route_state.pending_route_request is not None
                            and route_state.pending_route_request.request_id == request.request_id
                        ),
                    )
                    continue
                if route_state.pending_route_request is not None:
                    router.publish_route(
                        request_id=route_state.pending_route_request.request_id,
                        reason="microphone change was superseded by a newer request",
                    )
                route_state.seen_request_ids.add(request.request_id)
                route_state.selection = request.selection
                route_state.pending_route_request = request
                route_state.pending_include_preferred = True
                route_state.next_switch_attempt = 0.0
                router.publish_route(request_id=request.request_id, switching=True)
            elif command.kind == WorkerCommandKind.STOP:
                loop_state.desired_recording = False
                loop_state.stop_requested = True
                spool_state.next_retry = 0.0
                return

    def retry_pending_flushes() -> bool:
        """Re-attempt a pause-stop or stream flush that could not complete earlier."""
        if loop_state.stop_capture_pending:
            if stop_capture(paused=True):
                loop_state.stop_capture_pending = False
            else:
                time.sleep(0.05)
                return True
        if loop_state.stream_flush_pending:
            if not flush_current():
                time.sleep(0.05)
                return True
            loop_state.stream_flush_pending = False
            segment_state.clear_stream_clock()
        return False

    def poll_device_watchdog(now: float) -> bool:
        """Every 2s, notice the catalog moving under us and queue a switch."""
        if now < route_state.next_route_check:
            return False
        route_state.next_route_check = now + 2.0
        include_preferred = route_state.include_preferred
        try:
            tracked = router.resolve_route(include_preferred=include_preferred)
        except Exception as exc:
            tracked = _InputRouteResolution(
                candidates=(),
                preferred_input_name=router.preferred_name(),
                preferred_input_available=False,
                reason=f"microphone catalog refresh failed: {exc}",
            )
        if tracked.preferred_input_available != route_state.preferred_input_available:
            route_state.preferred_input_available = tracked.preferred_input_available
            route_state.input_route_reason = router.route_reason(
                tracked, route_state.active_route
            )
            router.publish_route()
        if not (
            loop_state.desired_recording
            and route_state.capture is not None
            and getattr(route_state.capture, "running", False)
        ):
            return False
        tracked_candidate = tracked.candidates[0] if tracked.candidates else None
        target_changed = (
            tracked_candidate is None
            or route_state.active_fingerprint is None
            or device_key(tracked_candidate.fingerprint)
            != device_key(route_state.active_fingerprint)
            or tracked_candidate.route != route_state.active_route
        )
        if not target_changed:
            return False
        # Route the change through the same pending-request path a user switch uses,
        # so it is applied at a safe boundary rather than mid-segment.
        request = InputRouteRequest(
            request_id=f"auto-{uuid.uuid4()}",
            selection=route_state.selection,
        )
        route_state.seen_request_ids.add(request.request_id)
        route_state.pending_route_request = request
        route_state.pending_include_preferred = include_preferred
        router.publish_route(request_id=request.request_id, switching=True)
        return True

    def ensure_capture_started(now: float) -> bool:
        """Open a capture when recording is wanted but no stream is live."""
        if not loop_state.desired_recording or getattr(route_state.capture, "running", False):
            return False
        if now < route_state.next_start_attempt:
            time.sleep(min(0.05, route_state.next_start_attempt - now))
            return True
        if segment_state.stream_origin is not None:
            if not flush_current():
                route_state.next_start_attempt = now + retry_delay
                return True
            segment_state.clear_stream_clock()
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.DEGRADED,
                "microphone stream became inactive; buffered speech was flushed",
                severity=Severity.WARNING,
            )
        spool_ratio = spool_writer.usage_ratio
        recovered_below_hysteresis = spool_ratio < config.spool_warning_ratio
        # Unlike the mid-stream gate, this one holds the block until usage falls back
        # under the warning ratio, so recording does not flap on and off.
        if not spool_writer.has_headroom() or (
            spool_state.capacity_blocked and not recovered_below_hysteresis
        ):
            if not spool_state.capacity_blocked:
                spool_writer.report_hard_limit(
                    "audio spool lacks headroom for a complete segment; recording stopped"
                )
            spool_state.capacity_blocked = True
            route_state.next_start_attempt = now + 1.0
            return True
        if spool_state.capacity_blocked:
            spool_state.capacity_blocked = False
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.READY,
                "audio spool recovered; resuming recording",
                metadata={"spool_ratio": spool_ratio},
            )
        return not router.start_current_route(now)

    def guard_mid_stream_headroom() -> bool:
        """Stop between segments if the spool ran out while we were recording.

        No hysteresis here, unlike the pre-start gate: audio is already flowing, so
        the stream stops immediately rather than waiting for usage to fall back.
        """
        if not (segment_state.current_id is None and not spool_writer.has_headroom()):
            return False
        with suppress(Exception):
            route_state.capture.stop()
        segment_state.clear_stream_clock()
        spool_writer.report_hard_limit(
            "audio spool lacks headroom for the next segment; recording stopped"
        )
        route_state.next_start_attempt = monotonic() + 1.0
        return True

    def read_chunk() -> AudioChunk | None:
        """Take the next chunk of audio, or None when the iteration should end."""
        try:
            if loop_state.deferred_gap_chunk is not None:
                chunk = loop_state.deferred_gap_chunk
                loop_state.deferred_gap_chunk = None
                return chunk
            return route_state.capture.read(timeout=0.1)
        except queue.Empty:
            return None
        except Exception as exc:
            loop_state.stream_flush_pending = not flush_current()
            with suppress(Exception):
                route_state.capture.stop()
            if not loop_state.stream_flush_pending:
                segment_state.clear_stream_clock()
            route_state.next_start_attempt = monotonic() + retry_delay
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.DEGRADED,
                f"microphone stream failed; reconnecting: {exc}",
                severity=Severity.WARNING,
            )
            return None

    def track_stream_clock(chunk: AudioChunk) -> bool:
        """Anchor the chunk to the stream clock, restarting it across a sleep gap.

        A wall-clock jump means the machine slept or the device stalled, so sample
        offsets from the old origin would date the next segment wrongly.
        """
        chunk_duration = len(chunk.samples) / chunk.sample_rate
        if segment_state.last_chunk_end_utc is not None:
            wall_gap = (chunk.started_at_utc - segment_state.last_chunk_end_utc).total_seconds()
            if wall_gap > max(1.0, 3 * chunk_duration):
                if not flush_current():
                    loop_state.stream_flush_pending = True
                    loop_state.deferred_gap_chunk = chunk
                    with suppress(Exception):
                        route_state.capture.stop()
                    route_state.next_start_attempt = monotonic() + retry_delay
                    _status(
                        event_queue,
                        WorkerKind.RECORDER,
                        WorkerState.DEGRADED,
                        "audio clock gap detected; waiting for durable speech recovery",
                        severity=Severity.WARNING,
                        metadata={"sleep_gap_seconds": wall_gap},
                    )
                    return True
                segment_state.stream_origin = None
                _status(
                    event_queue,
                    WorkerKind.RECORDER,
                    WorkerState.RECORDING,
                    "audio clock gap detected; streaming state was restarted",
                    metadata={"sleep_gap_seconds": wall_gap},
                )
        if segment_state.stream_origin is None:
            segment_state.stream_origin = chunk.started_at_utc
        segment_state.last_chunk_end_utc = chunk.started_at_utc + timedelta(
            seconds=chunk_duration
        )
        return False

    def report_capture_health(chunk: AudioChunk) -> None:
        if chunk.dropped_frames or chunk.status:
            loop_state.capture_degraded = True
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.DEGRADED,
                "audio capture discontinuity detected",
                severity=Severity.WARNING,
                metadata={
                    "dropped_frames": chunk.dropped_frames,
                    "portaudio_status": chunk.status,
                },
            )
        elif loop_state.capture_degraded:
            loop_state.capture_degraded = False
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.RECORDING,
                "audio capture recovered",
            )

    def accept_into_vad(chunk: AudioChunk) -> tuple[list[SpeechAudio], bool] | None:
        """Run VAD over the chunk. None means the pipeline failed and we auto-paused."""
        try:
            was_speech_active = bool(getattr(vad, "is_speech_detected", False))
            if segment_state.current_id is None and not was_speech_active:
                previews.remember_preroll(chunk)
            completed = vad.accept(chunk.samples)
            return completed, bool(getattr(vad, "is_speech_detected", False))
        except Exception as exc:
            _status(
                event_queue,
                WorkerKind.RECORDER,
                WorkerState.ERROR,
                f"VAD pipeline failed: {exc}",
                severity=Severity.ERROR,
            )
            loop_state.desired_recording = False
            loop_state.stop_capture_pending = not stop_capture(paused=True)
            return None

    def open_segment_if_speech_started(
        chunk: AudioChunk,
        completed: list[SpeechAudio],
        speech_active: bool,
    ) -> bool:
        """Start a segment, dating it from the pre-roll so it begins at the first word."""
        if not (segment_state.current_id is None and bool(completed or speech_active)):
            return False
        if preview_state.preroll:
            speech_started_at = preview_state.preroll[0].started_at_utc
        elif completed:
            speech_started_at = segment_state.stream_origin + timedelta(
                seconds=completed[0].start_sample / config.audio_sample_rate
            )
        else:
            speech_started_at = chunk.started_at_utc
        ensure_segment(speech_started_at)
        return True

    router.publish_route()

    while loop_state.running:
        loop_state.iterations += 1
        if max_iterations is not None and loop_state.iterations > max_iterations:
            break

        drain_commands()
        if not loop_state.running:
            break
        now = monotonic()
        spool_writer.poll_warning_recovery(now)
        if spool_state.pending_writes:
            spool_writer.drain_pending_write(now)
            continue
        if loop_state.stop_requested and not stop_capture(paused=False):
            time.sleep(0.05)
            continue
        if loop_state.stop_requested:
            loop_state.running = False
            break
        if retry_pending_flushes():
            continue
        if route_state.pending_route_request is not None and router.apply_pending_route(now):
            continue
        if poll_device_watchdog(now):
            continue
        if ensure_capture_started(now):
            continue

        if not loop_state.desired_recording or not getattr(route_state.capture, "running", False):
            time.sleep(0.05)
            continue
        if guard_mid_stream_headroom():
            continue

        chunk = read_chunk()
        if chunk is None:
            continue
        if track_stream_clock(chunk):
            continue
        report_capture_health(chunk)

        accepted = accept_into_vad(chunk)
        if accepted is None:
            continue
        completed, speech_active = accepted
        starting_segment = open_segment_if_speech_started(chunk, completed, speech_active)

        rms_dbfs, peak_dbfs = _audio_levels_dbfs(chunk.samples)
        _emit(
            event_queue,
            AudioLevelUpdate(
                rms_dbfs=rms_dbfs,
                peak_dbfs=peak_dbfs,
                speech_active=speech_active,
                segment_id=segment_state.current_id,
                measured_at_utc=segment_state.last_chunk_end_utc or chunk.started_at_utc,
            ),
            replaceable=True,
        )

        if segment_state.current_id is not None and segment_state.current_started_at is not None:
            # A starting segment replays its pre-roll so the preview begins at the
            # first word; otherwise only the chunk just read is previewed.
            previews.feed(
                previews.take_preroll()
                if starting_segment and preview_state.preroll
                else [as_preview_chunk(chunk)],
                segment_id=segment_state.current_id,
                segment_started_at=segment_state.current_started_at,
                monotonic=monotonic,
            )

        for index, speech in enumerate(completed):
            flushed, flushed_raw = previews.finish_for_segment()
            if inline_preview is None and index:
                # Out-of-process preview cannot be split across several segments
                # completing in one chunk, so drop the preview for the extras.
                preview_state.dropped = True
            emit_speech_segment(speech, flushed, flushed_raw)

    try:
        if getattr(route_state.capture, "running", False):
            flush_current()
            route_state.capture.stop()
    finally:
        if inline_preview is not None:
            with suppress(Exception):
                inline_preview.close()
        _status(event_queue, WorkerKind.RECORDER, WorkerState.STOPPED, "recorder stopped")


def _recorder_process_main(
    config: AppConfig,
    paths: AppPaths,
    command_queue: Any,
    event_queue: Any,
    preview_queue: Any,
    parent_pid: int | None = None,
) -> None:
    _start_parent_watchdog(parent_pid)
    _recorder_loop(
        config,
        paths,
        command_queue,
        event_queue,
        preview_queue,
    )


def _preview_process_main(
    config: AppConfig,
    paths: AppPaths,
    input_queue: Any,
    event_queue: Any,
    parent_pid: int | None = None,
) -> None:
    _start_parent_watchdog(parent_pid)
    _preview_loop(config, paths, input_queue, event_queue)


def _finalizer_loop(
    config: AppConfig,
    paths: AppPaths,
    input_queue: Any,
    event_queue: Any,
    *,
    engine_factory: Callable[[], Any] | None = None,
) -> None:
    _status(event_queue, WorkerKind.FINALIZER, WorkerState.STARTING, "initializing finalizer")
    try:
        engine = (engine_factory or (lambda: _build_finalizer(config, paths)))()
        if hasattr(engine, "warmup"):
            engine.warmup()
    except Exception as exc:
        _status(
            event_queue,
            WorkerKind.FINALIZER,
            WorkerState.ERROR,
            f"finalizer initialization failed: {exc}",
            severity=Severity.ERROR,
        )
        raise RuntimeError(f"finalizer initialization failed: {exc}") from exc
    _status(event_queue, WorkerKind.FINALIZER, WorkerState.READY, "finalizer ready")
    degraded_active = False
    try:
        while True:
            item = input_queue.get()
            if isinstance(item, WorkerCommand):
                if item.kind == WorkerCommandKind.STOP:
                    break
                if item.kind == WorkerCommandKind.UPDATE_HOTWORDS:
                    engine.update_hotwords(list(item.payload or []))
                continue
            if not isinstance(item, CapturedSegment):
                continue
            result: FinalResult = engine.transcribe(item)
            _emit(event_queue, result)
            degraded_this_result = False
            if not result.success:
                degraded_this_result = True
                _status(
                    event_queue,
                    WorkerKind.FINALIZER,
                    WorkerState.DEGRADED,
                    result.error or "final transcription failed",
                    severity=Severity.ERROR,
                    queue_size=_queue_size(input_queue),
                    metadata={"segment_id": result.segment_id},
                )
            elif getattr(engine, "last_fallback_reason", None):
                degraded_this_result = True
                _status(
                    event_queue,
                    WorkerKind.FINALIZER,
                    WorkerState.DEGRADED,
                    str(engine.last_fallback_reason),
                    severity=Severity.WARNING,
                    queue_size=_queue_size(input_queue),
                    metadata={"active_device": getattr(engine, "active_device", "")},
                )
            if getattr(engine, "last_normalization_error", None):
                degraded_this_result = True
                _status(
                    event_queue,
                    WorkerKind.FINALIZER,
                    WorkerState.DEGRADED,
                    f"Traditional Chinese conversion failed: {engine.last_normalization_error}",
                    severity=Severity.WARNING,
                    queue_size=_queue_size(input_queue),
                    metadata={"segment_id": result.segment_id},
                )
            if getattr(engine, "last_deadline_exceeded", False):
                degraded_this_result = True
                _status(
                    event_queue,
                    WorkerKind.FINALIZER,
                    WorkerState.DEGRADED,
                    f"finalization exceeded {config.final_deadline_ms} ms deadline",
                    severity=Severity.WARNING,
                    queue_size=_queue_size(input_queue),
                    metadata={"segment_id": result.segment_id, "latency_ms": result.latency_ms},
                )
            if degraded_this_result:
                degraded_active = True
            elif degraded_active:
                degraded_active = False
                _status(
                    event_queue,
                    WorkerKind.FINALIZER,
                    WorkerState.READY,
                    "final transcription recovered",
                    queue_size=_queue_size(input_queue),
                )
    finally:
        with suppress(Exception):
            engine.close()
        _status(event_queue, WorkerKind.FINALIZER, WorkerState.STOPPED, "finalizer stopped")


def _finalizer_process_main(
    config: AppConfig,
    paths: AppPaths,
    input_queue: Any,
    event_queue: Any,
    parent_pid: int | None = None,
) -> None:
    _start_parent_watchdog(parent_pid)
    _finalizer_loop(config, paths, input_queue, event_queue)


class JournalWorkers:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        event_queue_size: int = 128,
        *,
        command_queue_size: int = 32,
        preview_queue_size: int = 256,
        finalizer_queue_size: int = 32,
        context: Any | None = None,
        child_guard_factory: Callable[[], Any] | None = None,
        max_restarts: int = 3,
        restart_backoff_seconds: float = 0.5,
        max_restart_backoff_seconds: float = 30.0,
        restart_stable_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(
            event_queue_size,
            command_queue_size,
            preview_queue_size,
            finalizer_queue_size,
        ) <= 0:
            raise ValueError("worker queue sizes must be positive")
        if (
            max_restarts < 0
            or restart_backoff_seconds < 0
            or max_restart_backoff_seconds < 0
            or restart_stable_seconds < 0
        ):
            raise ValueError("restart policy values cannot be negative")
        self.config = config
        self.paths = paths
        provided_context = context is not None
        self._context = context or mp.get_context("spawn")
        self._event_queue_size = event_queue_size
        self._command_queue_size = command_queue_size
        self._preview_queue_size = preview_queue_size
        self._finalizer_queue_size = finalizer_queue_size
        self._max_restarts = max_restarts
        self._restart_backoff_seconds = restart_backoff_seconds
        self._max_restart_backoff_seconds = max_restart_backoff_seconds
        self._restart_stable_seconds = restart_stable_seconds
        self._monotonic = monotonic
        self._events = self._context.Queue(maxsize=event_queue_size)
        self._recorder_commands = self._context.Queue(maxsize=command_queue_size)
        self._preview_inputs = self._context.Queue(maxsize=preview_queue_size)
        self._finalizer_inputs = self._context.Queue(maxsize=finalizer_queue_size)
        self._recorder: Any | None = None
        self._preview: Any | None = None
        self._finalizer: Any | None = None
        self._local_events: list[JournalEvent] = []
        self._deferred_raw_events: list[RawWorkerEvent] = []
        self._started = False
        self._stopping = False
        self._desired_recording = False
        self._input_requests: dict[
            str, tuple[WorkerCommandKind, MicrophoneSelection]
        ] = {}
        self._sent_input_request_ids: set[str] = set()
        self._acked_input_request_ids: set[str] = set()
        self._hotwords: list[str] = []
        self._preview_hotwords_pending = False
        self._finalizer_hotwords_pending = False
        self._pending_segments: dict[str, CapturedSegment] = {}
        self._queued_segment_ids: set[str] = set()
        self._awaiting_previews: dict[str, tuple[CapturedSegment, float]] = {}
        self._preview_results: dict[str, PreviewEndpointResult] = {}
        self._early_finals: dict[str, FinalResult] = {}
        self._controller_ack_pending: set[str] = set()
        self._ack_expire_next_poll: set[str] = set()
        self._preview_wait_seconds = max(
            0.5,
            min(3.0, config.preview_interval_ms / 1000 * 1.5),
        )
        self._restart_counts = {
            WorkerKind.RECORDER: 0,
            WorkerKind.PREVIEW: 0,
            WorkerKind.FINALIZER: 0,
        }
        self._restart_due: dict[WorkerKind, float] = {}
        self._reported_dead: set[WorkerKind] = set()
        self._fatal_reported: set[WorkerKind] = set()
        self._ready_since: dict[WorkerKind, float] = {}
        self._intentionally_stopped: set[WorkerKind] = set()
        self._child_guard_factory = (
            child_guard_factory
            if child_guard_factory is not None
            else (_NoopChildGuard if provided_context else _create_child_guard)
        )
        self._child_guard: Any | None = None

    @property
    def running(self) -> bool:
        return bool(
            self._recorder is not None
            and self._recorder.is_alive()
            and self._preview is not None
            and self._preview.is_alive()
            and self._finalizer is not None
            and self._finalizer.is_alive()
        )

    @property
    def pending_finalizations(self) -> int:
        return len(set(self._pending_segments) | set(self._awaiting_previews))

    def start(self) -> None:
        if self._started:
            if self.running:
                return
            if not self._stopping:
                return
            raise RuntimeError("stopped workers cannot be restarted")
        self.config.validate()
        self.paths.ensure_runtime_dirs()
        self._started = True
        self._desired_recording = True
        try:
            self._child_guard = self._child_guard_factory()
        except Exception as exc:
            self._child_guard = _NoopChildGuard()
            self._local_events.append(
                WorkerStatus(
                    worker=WorkerKind.RECORDER,
                    state=WorkerState.DEGRADED,
                    message=(
                        "Windows child Job Object unavailable; "
                        f"PID watchdog remains active: {exc}"
                    ),
                    severity=Severity.WARNING,
                    metadata={"parent_guard_degraded": True},
                )
            )
        try:
            self._spawn_worker(WorkerKind.PREVIEW)
            # Do not let the recorder open a microphone until all three child
            # processes exist. A later spawn failure can then roll back without
            # abandoning newly captured audio in an obsolete event queue.
            self._spawn_worker(WorkerKind.RECORDER, start_recording=False)
            self._spawn_worker(WorkerKind.FINALIZER)
            if self._desired_recording:
                self._put_command(
                    self._recorder_commands,
                    WorkerCommand(WorkerCommandKind.START),
                    worker=WorkerKind.RECORDER,
                )
            self._refill_input_requests()
        except Exception:
            self._rollback_failed_start()
            raise

    def _spawn_worker(self, kind: WorkerKind, *, start_recording: bool = True) -> None:
        parent_pid = os.getpid()
        if kind == WorkerKind.RECORDER:
            process = self._context.Process(
                name="speech-journal-recorder",
                target=_recorder_process_main,
                args=(
                    self.config,
                    self.paths,
                    self._recorder_commands,
                    self._events,
                    self._preview_inputs,
                    parent_pid,
                ),
            )
            self._recorder = process
        elif kind == WorkerKind.PREVIEW:
            process = self._context.Process(
                name="speech-journal-preview",
                target=_preview_process_main,
                args=(
                    self.config,
                    self.paths,
                    self._preview_inputs,
                    self._events,
                    parent_pid,
                ),
            )
            self._preview = process
        else:
            process = self._context.Process(
                name="speech-journal-finalizer",
                target=_finalizer_process_main,
                args=(
                    self.config,
                    self.paths,
                    self._finalizer_inputs,
                    self._events,
                    parent_pid,
                ),
            )
            self._finalizer = process
        process.start()
        if self._child_guard is not None:
            try:
                self._child_guard.assign(process)
            except Exception as exc:
                self._local_events.append(
                    WorkerStatus(
                        worker=kind,
                        state=WorkerState.DEGRADED,
                        message=(
                            "could not assign child to kill-on-close Job Object; "
                            f"PID watchdog remains active: {exc}"
                        ),
                        severity=Severity.WARNING,
                        metadata={"parent_guard_degraded": True},
                    )
                )
        if kind == WorkerKind.RECORDER:
            if self._desired_recording and start_recording:
                self._put_command(
                    self._recorder_commands,
                    WorkerCommand(WorkerCommandKind.START),
                    worker=kind,
                )
        elif kind == WorkerKind.PREVIEW:
            self._preview_hotwords_pending = bool(self._hotwords)
            self._refill_preview_commands()
        else:
            self._finalizer_hotwords_pending = bool(self._hotwords)
            self._refill_finalizer_queue()

    def _rollback_failed_start(self) -> None:
        guard, self._child_guard = self._child_guard, None
        if guard is not None:
            with suppress(Exception):
                guard.close()
        for process in (self._preview, self._recorder, self._finalizer):
            if process is None:
                continue
            try:
                alive = bool(process.is_alive())
            except (AssertionError, OSError, ValueError):
                alive = False
            if alive:
                with suppress(Exception):
                    process.terminate()
            with suppress(AssertionError, OSError, ValueError):
                process.join(2.0)

        self._preview = None
        self._recorder = None
        self._finalizer = None
        self._started = False
        self._stopping = False
        self._desired_recording = False
        self._intentionally_stopped.clear()
        self._reported_dead.clear()
        self._fatal_reported.clear()
        self._restart_due.clear()
        self._ready_since.clear()
        for kind in self._restart_counts:
            self._restart_counts[kind] = 0
        self._reset_process_queues()

    def _reset_process_queues(self) -> None:
        for channel in (
            self._events,
            self._recorder_commands,
            self._preview_inputs,
            self._finalizer_inputs,
        ):
            self._close_queue(channel)
        self._events = self._context.Queue(maxsize=self._event_queue_size)
        self._recorder_commands = self._context.Queue(maxsize=self._command_queue_size)
        self._preview_inputs = self._context.Queue(maxsize=self._preview_queue_size)
        self._finalizer_inputs = self._context.Queue(maxsize=self._finalizer_queue_size)
        self._deferred_raw_events.clear()
        self._sent_input_request_ids.clear()
        self._queued_segment_ids.clear()
        self._preview_hotwords_pending = bool(self._hotwords)
        self._finalizer_hotwords_pending = bool(self._hotwords)

    @staticmethod
    def _close_queue(channel: Any) -> None:
        with suppress(AttributeError, OSError, ValueError):
            channel.close()

    def _restart_worker(self, kind: WorkerKind) -> None:
        count_cap = max(1, self._max_restarts)
        self._restart_counts[kind] = min(self._restart_counts[kind] + 1, count_cap)
        if kind == WorkerKind.RECORDER:
            self._close_queue(self._recorder_commands)
            self._recorder_commands = self._context.Queue(maxsize=self._command_queue_size)
            # A successful put only proves delivery to the previous recorder's
            # queue. Every unacknowledged request must be sent again to the new
            # child; request ids make that replay idempotent.
            self._sent_input_request_ids.clear()
        elif kind == WorkerKind.PREVIEW:
            # Keep the recorder's existing bounded queue reference. A new
            # preview consumer can safely drain messages left by the old one.
            self._preview_hotwords_pending = bool(self._hotwords)
        else:
            self._close_queue(self._finalizer_inputs)
            self._finalizer_inputs = self._context.Queue(maxsize=self._finalizer_queue_size)
            self._queued_segment_ids.clear()
            self._finalizer_hotwords_pending = bool(self._hotwords)
        self._reported_dead.discard(kind)
        self._restart_due.pop(kind, None)
        self._ready_since.pop(kind, None)
        self._local_events.append(
            WorkerStatus(
                worker=kind,
                state=WorkerState.STARTING,
                message=f"restarting {kind.value} worker",
                severity=Severity.WARNING,
                metadata={"restart_count": self._restart_counts[kind]},
            )
        )
        self._spawn_worker(kind)

    def _refill_preview_commands(self) -> None:
        if not self._preview_hotwords_pending:
            return
        try:
            self._preview_inputs.put_nowait(
                WorkerCommand(WorkerCommandKind.UPDATE_HOTWORDS, self._hotwords)
            )
        except queue.Full:
            return
        self._preview_hotwords_pending = False

    def _refill_finalizer_queue(self) -> None:
        if self._finalizer_hotwords_pending:
            try:
                self._finalizer_inputs.put_nowait(
                    WorkerCommand(WorkerCommandKind.UPDATE_HOTWORDS, self._hotwords)
                )
                self._finalizer_hotwords_pending = False
            except queue.Full:
                return
        for segment_id, segment in self._pending_segments.items():
            if segment_id in self._queued_segment_ids:
                continue
            try:
                self._finalizer_inputs.put_nowait(segment)
            except queue.Full:
                break
            self._queued_segment_ids.add(segment_id)

    def _refill_input_requests(self) -> None:
        if (
            not self._started
            or self._stopping
            or self._recorder is None
            or not self._recorder.is_alive()
        ):
            return
        for identifier, (kind, selection) in self._input_requests.items():
            if (
                identifier in self._acked_input_request_ids
                or identifier in self._sent_input_request_ids
            ):
                continue
            try:
                self._recorder_commands.put_nowait(
                    WorkerCommand(kind, InputRouteRequest(identifier, selection))
                )
            except queue.Full:
                return
            self._sent_input_request_ids.add(identifier)

    def _put_command(self, channel: Any, command: WorkerCommand, *, worker: WorkerKind) -> None:
        try:
            channel.put_nowait(command)
        except queue.Full as exc:
            raise WorkerBackpressure(f"{worker.value} command queue is full") from exc

    def pause(self) -> None:
        self._require_started()
        self._desired_recording = False
        if self._recorder is not None and self._recorder.is_alive():
            self._put_command(
                self._recorder_commands,
                WorkerCommand(WorkerCommandKind.PAUSE),
                worker=WorkerKind.RECORDER,
            )

    def resume(self) -> None:
        self._require_started()
        self._desired_recording = True
        if self._recorder is not None and self._recorder.is_alive():
            self._put_command(
                self._recorder_commands,
                WorkerCommand(WorkerCommandKind.RESUME),
                worker=WorkerKind.RECORDER,
            )

    def reconfigure_input(
        self,
        selection: MicrophoneSelection,
        *,
        request_id: str | None = None,
    ) -> str:
        """Save the latest child config, then enqueue one idempotent live switch."""

        selection.validate()
        updated = replace(self.config, microphone=selection)
        updated.validate()
        self.config = updated
        return self._send_input_request(
            WorkerCommandKind.RECONFIGURE_INPUT,
            selection,
            request_id=request_id,
        )

    def retry_preferred_input(self, *, request_id: str | None = None) -> str:
        selection = self.config.microphone
        if selection.mode != MicrophoneMode.FIXED:
            raise ValueError("retrying a preferred input requires fixed microphone mode")
        return self._send_input_request(
            WorkerCommandKind.RETRY_PREFERRED_INPUT,
            selection,
            request_id=request_id,
        )

    def _send_input_request(
        self,
        kind: WorkerCommandKind,
        selection: MicrophoneSelection,
        *,
        request_id: str | None,
    ) -> str:
        identifier = request_id or str(uuid.uuid4())
        InputRouteRequest(identifier, selection)
        existing = self._input_requests.get(identifier)
        signature = (kind, selection)
        if existing is not None and existing != signature:
            raise ValueError("request_id was already used for a different input request")
        self._input_requests[identifier] = signature
        self._refill_input_requests()
        return identifier

    def submit(self, segment: CapturedSegment) -> bool:
        self._require_started()
        if segment.segment_id in self._controller_ack_pending:
            self._controller_ack_pending.discard(segment.segment_id)
            return True
        if segment.segment_id in self._pending_segments:
            return True
        try:
            self._finalizer_inputs.put_nowait(segment)
            self._pending_segments[segment.segment_id] = segment
            self._queued_segment_ids.add(segment.segment_id)
            return True
        except queue.Full:
            self._local_events.append(
                WorkerStatus(
                    worker=WorkerKind.FINALIZER,
                    state=WorkerState.DEGRADED,
                    message="finalizer queue is full; segment remains in the durable spool",
                    severity=Severity.WARNING,
                    queue_size=_queue_size(self._finalizer_inputs),
                    metadata={"segment_id": segment.segment_id},
                )
            )
            return False

    def update_hotwords(self, hotwords: Sequence[str]) -> None:
        self._require_started()
        normalized = list(dict.fromkeys(word.strip() for word in hotwords if word.strip()))
        self._hotwords = normalized
        self._preview_hotwords_pending = True
        self._refill_preview_commands()
        self._finalizer_hotwords_pending = True
        self._refill_finalizer_queue()

    def _require_started(self) -> None:
        if not self._started or self._stopping:
            raise RuntimeError("workers are not started")

    def poll_events(self) -> list[JournalEvent]:
        self._controller_ack_pending.difference_update(self._ack_expire_next_poll)
        self._ack_expire_next_poll.clear()
        events, self._local_events = self._local_events, []
        self._drain_raw_events()
        raw_events, self._deferred_raw_events = self._deferred_raw_events, []
        for event in raw_events:
            if (
                isinstance(event, InputRouteUpdate)
                and event.request_id is not None
                and not event.input_switching
                and event.request_id in self._input_requests
            ):
                self._acked_input_request_ids.add(event.request_id)
                self._sent_input_request_ids.discard(event.request_id)
            if isinstance(event, PreviewEndpointResult):
                awaiting = self._awaiting_previews.pop(event.segment_id, None)
                if awaiting is None:
                    self._preview_results[event.segment_id] = event
                else:
                    self._publish_previewed_capture(awaiting[0], event, events)
                continue
            if isinstance(event, CapturedSegment) and event.preview_pending:
                segment = replace(event, preview_pending=False)
                self._queue_internal_finalization(segment)
                result = self._preview_results.pop(segment.segment_id, None)
                if result is None:
                    self._awaiting_previews[segment.segment_id] = (
                        segment,
                        self._monotonic(),
                    )
                else:
                    self._publish_previewed_capture(segment, result, events)
                continue
            if isinstance(event, FinalResult):
                self._pending_segments.pop(event.segment_id, None)
                self._queued_segment_ids.discard(event.segment_id)
                if event.segment_id in self._awaiting_previews:
                    self._early_finals[event.segment_id] = event
                    continue
                events.append(event)
            elif isinstance(event, WorkerStatus) and event.state == WorkerState.READY:
                self._ready_since.setdefault(event.worker, self._monotonic())
                events.append(event)
            else:
                events.append(event)
        self._expire_preview_waits(events)
        self._refill_preview_commands()
        self._refill_finalizer_queue()
        self._supervise(events)
        self._refill_input_requests()
        if self._local_events:
            events.extend(self._local_events)
            self._local_events = []
        self._ack_expire_next_poll.update(
            event.segment_id
            for event in events
            if isinstance(event, CapturedSegment)
            and event.segment_id in self._controller_ack_pending
        )
        return events

    def _drain_raw_events(self) -> None:
        """Free child event-queue capacity without losing unprocessed events."""

        while True:
            try:
                event: RawWorkerEvent = self._events.get_nowait()
            except queue.Empty:
                return
            self._deferred_raw_events.append(event)

    def _queue_internal_finalization(self, segment: CapturedSegment) -> None:
        if segment.segment_id not in self._pending_segments:
            self._pending_segments[segment.segment_id] = segment
            self._controller_ack_pending.add(segment.segment_id)
        self._refill_finalizer_queue()

    def _publish_previewed_capture(
        self,
        segment: CapturedSegment,
        result: PreviewEndpointResult | None,
        events: list[JournalEvent],
    ) -> None:
        if result is None:
            published = replace(segment, preview_pending=False)
        else:
            published = replace(
                segment,
                preview_text=result.normalized_text or segment.preview_text,
                preview_raw_text=result.raw_text
                or segment.preview_raw_text
                or result.normalized_text,
                preview_pending=False,
            )
        events.append(published)
        early = self._early_finals.pop(segment.segment_id, None)
        if early is not None:
            events.append(early)

    def _expire_preview_waits(self, events: list[JournalEvent]) -> None:
        now = self._monotonic()
        expired = [
            segment_id
            for segment_id, (_, since) in self._awaiting_previews.items()
            if now - since >= self._preview_wait_seconds
        ]
        for segment_id in expired:
            segment, _ = self._awaiting_previews.pop(segment_id)
            self._publish_previewed_capture(segment, None, events)
            events.append(
                WorkerStatus(
                    worker=WorkerKind.PREVIEW,
                    state=WorkerState.DEGRADED,
                    message=(
                        "preview endpoint timed out; final transcription continues "
                        "from durable audio"
                    ),
                    severity=Severity.WARNING,
                    metadata={
                        "segment_id": segment_id,
                        "preview_timeout": True,
                    },
                )
            )

    def _supervise(self, events: list[JournalEvent]) -> None:
        if not self._started or self._stopping:
            return
        now = self._monotonic()
        for kind, process in (
            (WorkerKind.RECORDER, self._recorder),
            (WorkerKind.PREVIEW, self._preview),
            (WorkerKind.FINALIZER, self._finalizer),
        ):
            if process is None or kind in self._intentionally_stopped:
                continue
            if process.is_alive():
                ready_since = self._ready_since.get(kind)
                if (
                    ready_since is not None
                    and self._restart_counts[kind]
                    and now - ready_since >= self._restart_stable_seconds
                ):
                    self._restart_counts[kind] = 0
                    self._fatal_reported.discard(kind)
                    self._ready_since.pop(kind, None)
                continue
            self._ready_since.pop(kind, None)
            if kind not in self._reported_dead:
                self._reported_dead.add(kind)
                events.append(
                    WorkerStatus(
                        worker=kind,
                        state=WorkerState.ERROR,
                        message=(
                            f"{kind.value} process exited unexpectedly "
                            f"with code {process.exitcode}"
                        ),
                        severity=Severity.ERROR,
                        metadata={
                            "unexpected_exit": True,
                            "restart_count": self._restart_counts[kind],
                        },
                    )
                )
                if (
                    self._restart_counts[kind] >= self._max_restarts
                    and kind not in self._fatal_reported
                ):
                    self._fatal_reported.add(kind)
                    events.append(
                        WorkerStatus(
                            worker=kind,
                            state=WorkerState.ERROR,
                            message=(
                                f"{kind.value} worker exceeded its restart threshold; "
                                "continuing capped retries"
                            ),
                            severity=Severity.ERROR,
                            metadata={
                                "fatal": True,
                                "restart_count": self._restart_counts[kind],
                            },
                        )
                    )
                delay = min(
                    self._max_restart_backoff_seconds,
                    self._restart_backoff_seconds * (2 ** self._restart_counts[kind]),
                )
                self._restart_due[kind] = now + delay
            due = self._restart_due.get(kind)
            if due is not None and now >= due:
                try:
                    self._restart_worker(kind)
                except Exception as exc:
                    self._reported_dead.discard(kind)
                    events.append(
                        WorkerStatus(
                            worker=kind,
                            state=WorkerState.ERROR,
                            message=f"failed to restart {kind.value} worker: {exc}",
                            severity=Severity.ERROR,
                            metadata={"restart_count": self._restart_counts[kind]},
                        )
                    )

    def _stop_worker(self, kind: WorkerKind, timeout: float) -> None:
        if kind == WorkerKind.RECORDER:
            process = self._recorder
            channel = self._recorder_commands
        elif kind == WorkerKind.PREVIEW:
            process = self._preview
            channel = self._preview_inputs
        else:
            process = self._finalizer
            channel = self._finalizer_inputs
        self._intentionally_stopped.add(kind)
        self._restart_due.pop(kind, None)
        if process is None or not process.is_alive():
            return
        stop_command = WorkerCommand(WorkerCommandKind.STOP)
        stop_sent = False
        try:
            channel.put_nowait(stop_command)
            stop_sent = True
        except queue.Full:
            self._local_events.append(
                WorkerStatus(
                    worker=kind,
                    state=WorkerState.DEGRADED,
                    message="stop command was blocked by worker backpressure",
                    severity=Severity.WARNING,
                )
            )
        deadline = time.monotonic() + max(0.0, timeout)
        while process.is_alive():
            # A child can be blocked publishing its final non-replaceable
            # event. Keep draining while joining so shutdown cannot deadlock
            # merely because the UI poll timer has already stopped.
            self._drain_raw_events()
            if not stop_sent:
                try:
                    channel.put_nowait(stop_command)
                    stop_sent = True
                except queue.Full:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            process.join(min(0.05, remaining))
        self._drain_raw_events()
        if process.is_alive():
            if kind == WorkerKind.RECORDER:
                self._local_events.append(
                    WorkerStatus(
                        worker=kind,
                        state=WorkerState.ERROR,
                        message=(
                            "recorder shutdown is waiting for captured audio "
                            "to reach durable storage"
                        ),
                        severity=Severity.ERROR,
                        metadata={"shutdown_durability_pending": True},
                    )
                )
                raise RecorderShutdownPending(
                    "recorder still has captured audio waiting for durable storage"
                )
            process.terminate()
            process.join(2.0)
            self._drain_raw_events()

    def _release_awaiting_previews(self) -> None:
        for segment_id, (segment, _) in list(self._awaiting_previews.items()):
            self._awaiting_previews.pop(segment_id, None)
            self._publish_previewed_capture(segment, None, self._local_events)

    def _close_child_guard_if_stopped(self) -> None:
        processes = (self._recorder, self._preview, self._finalizer)
        if any(process is not None and process.is_alive() for process in processes):
            return
        guard, self._child_guard = self._child_guard, None
        if guard is not None:
            with suppress(Exception):
                guard.close()

    def stop_recorder(self, timeout: float = 10.0) -> None:
        if not self._started:
            return
        self._desired_recording = False
        self._stop_worker(WorkerKind.RECORDER, timeout)

    def stop_finalizer(self, timeout: float = 10.0) -> None:
        if not self._started:
            return
        deadline = time.monotonic() + timeout
        if WorkerKind.PREVIEW not in self._intentionally_stopped:
            self._stop_worker(
                WorkerKind.PREVIEW,
                max(0.0, deadline - time.monotonic()),
            )
            self._release_awaiting_previews()
        if WorkerKind.FINALIZER not in self._intentionally_stopped:
            self._stop_worker(
                WorkerKind.FINALIZER,
                max(0.0, deadline - time.monotonic()),
            )
        self._close_child_guard_if_stopped()

    def stop(self, timeout: float = 10.0) -> None:
        if not self._started or self._stopping:
            return
        self._stopping = True
        deadline = time.monotonic() + timeout
        try:
            self.stop_recorder(max(0.0, deadline - time.monotonic()))
            self.stop_finalizer(max(0.0, deadline - time.monotonic()))
            self._close_child_guard_if_stopped()
        except Exception:
            self._stopping = False
            raise

    def __enter__(self) -> JournalWorkers:
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.stop()


__all__ = [
    "JournalEvent",
    "JournalWorkers",
    "RecorderShutdownPending",
    "RealtimeModelProbe",
    "WorkerBackpressure",
    # Re-exported from `input_routing` so the recorder's routing types stay reachable
    # from the module that owns the loop they belong to.
    "_CaptureCandidate",
    "_InputRouteResolution",
    "probe_realtime_models",
]
