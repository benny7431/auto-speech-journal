from __future__ import annotations

import importlib
import math
import os
import queue
import re
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .config import DeviceFingerprint
from .native_runtime import register_onnxruntime_dll_directory

TARGET_SAMPLE_RATE = 16_000
VOLUME_HEADROOM_BYTES = 64 * 1024 * 1024


class AudioError(RuntimeError):
    pass


class AudioDeviceNotFound(AudioError):
    pass


class SpoolLimitExceeded(AudioError):
    pass


def _numpy() -> Any:
    try:
        return importlib.import_module("numpy")
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise AudioError("numpy is required for audio processing") from exc


def _optional_module(name: str, purpose: str) -> Any:
    if name == "sherpa_onnx":
        register_onnxruntime_dll_directory()
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise AudioError(f"{name} is required for {purpose}") from exc


@dataclass(frozen=True, slots=True)
class InputDevice:
    index: int
    name: str
    host_api: str
    endpoint_id: str
    default_sample_rate: int
    max_input_channels: int
    is_default: bool = False
    fixed_binding_available: bool = True
    binding_error: str = ""

    def fingerprint(self) -> DeviceFingerprint:
        return DeviceFingerprint(
            name=self.name,
            host_api=self.host_api,
            endpoint_id=self.endpoint_id,
            default_sample_rate=float(self.default_sample_rate),
            max_input_channels=self.max_input_channels,
        )


@dataclass(frozen=True, slots=True)
class AudioChunk:
    samples: Any
    sample_rate: int
    started_at_utc: datetime
    dropped_frames: int = 0
    status: str = ""

    @property
    def duration_ms(self) -> int:
        return round(len(self.samples) * 1000 / self.sample_rate)


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    samples: Any
    start_sample: int
    end_sample: int
    forced_endpoint: bool = False


@dataclass(frozen=True, slots=True)
class InputLevel:
    device: InputDevice
    duration_ms: int
    peak: float
    rms: float
    dropped_frames: int = 0


def _host_default_input_index(host_api: Any) -> int | None:
    value = host_api.get("default_input_device")
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def _normalized_device_identity(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _wasapi_endpoint_id(host_api: str, name: str) -> str:
    return (
        f"wasapi:{_normalized_device_identity(host_api)}:"
        f"{_normalized_device_identity(name)}"
    )


def list_wasapi_input_devices(sounddevice_module: Any | None = None) -> list[InputDevice]:
    sd = sounddevice_module or _optional_module("sounddevice", "WASAPI capture")
    raw_devices = list(sd.query_devices())
    host_apis = list(sd.query_hostapis())
    devices: list[InputDevice] = []
    for index, raw in enumerate(raw_devices):
        channels = int(raw.get("max_input_channels", 0))
        host_index = int(raw.get("hostapi", -1))
        host_name = (
            str(host_apis[host_index].get("name", ""))
            if 0 <= host_index < len(host_apis)
            else ""
        )
        if channels <= 0 or "wasapi" not in host_name.casefold():
            continue
        name = str(raw.get("name", "")).strip()
        endpoint_id = _wasapi_endpoint_id(host_name, name)
        devices.append(
            InputDevice(
                index=index,
                name=name,
                host_api=host_name,
                endpoint_id=endpoint_id,
                default_sample_rate=round(float(raw.get("default_samplerate", 0) or 0)),
                max_input_channels=channels,
                is_default=(
                    0 <= host_index < len(host_apis)
                    and index == _host_default_input_index(host_apis[host_index])
                ),
            )
        )

    identity_counts: dict[tuple[str, str], int] = {}
    for device in devices:
        identity = (device.host_api.casefold(), device.name.casefold())
        identity_counts[identity] = identity_counts.get(identity, 0) + 1
    return [
        replace(
            device,
            fixed_binding_available=False,
            binding_error=(
                "同名 WASAPI 端點無法安全區分；請停用重複端點或改用 Windows 預設"
            ),
        )
        if identity_counts[(device.host_api.casefold(), device.name.casefold())] > 1
        else device
        for device in devices
    ]


def default_wasapi_input_device(
    sounddevice_module: Any | None = None,
) -> InputDevice:
    defaults = [
        device
        for device in list_wasapi_input_devices(sounddevice_module)
        if device.is_default
    ]
    if len(defaults) == 1:
        return defaults[0]
    if not defaults:
        raise AudioDeviceNotFound("Windows WASAPI has no default input device")
    raise AudioDeviceNotFound("multiple Windows WASAPI default input devices were reported")


def resolve_wasapi_input_device(
    fingerprint: DeviceFingerprint,
    sounddevice_module: Any | None = None,
) -> InputDevice:
    devices = list_wasapi_input_devices(sounddevice_module)
    if not devices:
        raise AudioDeviceNotFound("no Windows WASAPI input device is available")

    if fingerprint.endpoint_id:
        endpoint_matches = [
            device for device in devices if device.endpoint_id == fingerprint.endpoint_id
        ]
        if len(endpoint_matches) == 1:
            return endpoint_matches[0]
        if len(endpoint_matches) > 1:
            raise AudioDeviceNotFound(
                "configured WASAPI fingerprint is ambiguous; refusing to select a microphone"
            )

    expected_name = fingerprint.name.strip().casefold()
    expected_host = fingerprint.host_api.strip().casefold()
    matches = [
        device
        for device in devices
        if device.name.casefold() == expected_name
        and (not expected_host or device.host_api.casefold() == expected_host)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AudioDeviceNotFound(
            "multiple matching WASAPI microphones were found; refusing automatic selection"
        )
    available = ", ".join(f"{device.name} [{device.host_api}]" for device in devices)
    raise AudioDeviceNotFound(
        f"configured microphone {fingerprint.name!r} was not found; available: {available}"
    )


class StreamingResampler:
    def __init__(
        self,
        input_rate: int,
        output_rate: int = TARGET_SAMPLE_RATE,
        *,
        quality: str = "HQ",
        soxr_module: Any | None = None,
    ) -> None:
        if input_rate <= 0 or output_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.quality = quality
        self._soxr_module = soxr_module
        self._stream: Any | None = None

    def _ensure_stream(self) -> None:
        if self.input_rate == self.output_rate or self._stream is not None:
            return
        soxr = self._soxr_module or _optional_module("soxr", "sample-rate conversion")
        self._stream = soxr.ResampleStream(
            self.input_rate,
            self.output_rate,
            1,
            dtype="float32",
            quality=self.quality,
        )

    def process(self, samples: Any, *, final: bool = False) -> Any:
        np = _numpy()
        source = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        if self.input_rate == self.output_rate:
            return source.copy()
        self._ensure_stream()
        result = self._stream.resample_chunk(source, last=final)
        return np.ascontiguousarray(result, dtype=np.float32).reshape(-1)


@dataclass(slots=True)
class _RawChunk:
    samples: Any
    started_at_utc: datetime
    status: str
    dropped_frames: int = 0


class WasapiMicrophone:
    def __init__(
        self,
        fingerprint: DeviceFingerprint,
        *,
        target_sample_rate: int = TARGET_SAMPLE_RATE,
        block_ms: int = 100,
        queue_blocks: int = 32,
        exclusive: bool = False,
        sounddevice_module: Any | None = None,
        soxr_module: Any | None = None,
        resolved_device: InputDevice | None = None,
        require_system_default: bool = False,
    ) -> None:
        if block_ms <= 0 or queue_blocks <= 0:
            raise ValueError("block_ms and queue_blocks must be positive")
        self.fingerprint = fingerprint
        self.target_sample_rate = target_sample_rate
        self.block_ms = block_ms
        self.exclusive = exclusive
        self._sd = sounddevice_module
        self._soxr = soxr_module
        self._resolved_device = resolved_device
        self._require_system_default = require_system_default
        self._opened_once = False
        self._queue: queue.Queue[_RawChunk] = queue.Queue(maxsize=queue_blocks)
        self._stream: Any | None = None
        self._resampler: StreamingResampler | None = None
        self._device: InputDevice | None = None
        self._pending_dropped_frames = 0
        self._drop_lock = threading.Lock()

    @property
    def device(self) -> InputDevice | None:
        return self._device

    @property
    def running(self) -> bool:
        if self._stream is None:
            return False
        try:
            active = getattr(self._stream, "active", None)
        except Exception:
            return False
        return True if active is None else bool(active)

    def start(self) -> InputDevice:
        if self.running:
            return self._device  # type: ignore[return-value]
        if self._stream is not None:
            self.stop()
        sd = self._sd or _optional_module("sounddevice", "WASAPI capture")
        if self._resolved_device is None:
            device = resolve_wasapi_input_device(self.fingerprint, sd)
        else:
            expected = self._resolved_device
            matches = [
                item
                for item in list_wasapi_input_devices(sd)
                if item.index == expected.index
                and item.name == expected.name
                and item.host_api == expected.host_api
            ]
            if len(matches) != 1:
                raise AudioDeviceNotFound(
                    "the resolved WASAPI input changed before the stream could open"
                )
            device = matches[0]
            if self._require_system_default and not self._opened_once and not device.is_default:
                raise AudioDeviceNotFound(
                    "the Windows default WASAPI input changed before the stream could open"
                )
        sample_rate = device.default_sample_rate or 48_000
        block_size = max(1, round(sample_rate * self.block_ms / 1000))
        if hasattr(sd, "check_input_settings"):
            sd.check_input_settings(
                device=device.index,
                channels=1,
                dtype="float32",
                samplerate=sample_rate,
            )
        extra_settings = None
        if self.exclusive and hasattr(sd, "WasapiSettings"):
            extra_settings = sd.WasapiSettings(exclusive=True)

        self._device = device
        self._resampler = StreamingResampler(
            sample_rate,
            self.target_sample_rate,
            soxr_module=self._soxr,
        )
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            blocksize=block_size,
            device=device.index,
            channels=1,
            dtype="float32",
            latency="low",
            extra_settings=extra_settings,
            callback=self._callback,
        )
        self._stream.start()
        self._opened_once = True
        return device

    def _callback(self, indata: Any, frames: int, _time_info: Any, status: Any) -> None:
        np = _numpy()
        samples = np.asarray(indata, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples[:, 0]
        samples = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1).copy()
        sample_rate = self._device.default_sample_rate if self._device else 48_000
        started_at = datetime.now(UTC) - timedelta(seconds=frames / sample_rate)
        raw = _RawChunk(samples=samples, started_at_utc=started_at, status=str(status or ""))
        try:
            self._queue.put_nowait(raw)
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
                dropped_count = len(dropped.samples) + dropped.dropped_frames
            except queue.Empty:
                dropped_count = frames
            with self._drop_lock:
                self._pending_dropped_frames += dropped_count
            try:
                self._queue.put_nowait(raw)
            except queue.Full:
                with self._drop_lock:
                    self._pending_dropped_frames += frames

    def read(self, timeout: float | None = None) -> AudioChunk:
        if self._stream is None or self._resampler is None:
            raise AudioError("microphone is not running")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                raw = self._queue.get(timeout=remaining)
            except queue.Empty as exc:
                if not self.running:
                    raise AudioError("WASAPI microphone stream became inactive") from exc
                raise
            output = self._resampler.process(raw.samples)
            if len(output) == 0:
                if deadline is not None and time.monotonic() >= deadline:
                    raise queue.Empty
                continue
            with self._drop_lock:
                dropped = self._pending_dropped_frames
                self._pending_dropped_frames = 0
            scaled_drops = round(dropped * self.target_sample_rate / self._resampler.input_rate)
            return AudioChunk(
                samples=output,
                sample_rate=self.target_sample_rate,
                started_at_utc=raw.started_at_utc,
                dropped_frames=scaled_drops,
                status=raw.status,
            )

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._resampler = None

    def __enter__(self) -> WasapiMicrophone:
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.stop()


def measure_input_level(
    fingerprint: DeviceFingerprint,
    *,
    duration_ms: int = 500,
    follow_system_default: bool = False,
    sounddevice_module: Any | None = None,
    soxr_module: Any | None = None,
) -> InputLevel:
    if not 100 <= duration_ms <= 5_000:
        raise ValueError("duration_ms must be between 100 and 5000")
    sd = sounddevice_module
    resolved_device = None
    if follow_system_default:
        sd = sd or _optional_module("sounddevice", "WASAPI capture")
        resolved_device = default_wasapi_input_device(sd)
        fingerprint = resolved_device.fingerprint()
    microphone = WasapiMicrophone(
        fingerprint,
        target_sample_rate=TARGET_SAMPLE_RATE,
        block_ms=min(100, duration_ms),
        sounddevice_module=sd,
        soxr_module=soxr_module,
        resolved_device=resolved_device,
        require_system_default=follow_system_default,
    )
    chunks: list[Any] = []
    dropped = 0
    target_samples = round(duration_ms * TARGET_SAMPLE_RATE / 1000)
    collected = 0
    device = microphone.start()
    try:
        while collected < target_samples:
            chunk = microphone.read(timeout=1.0)
            chunks.append(chunk.samples)
            collected += len(chunk.samples)
            dropped += chunk.dropped_frames
    finally:
        microphone.stop()
    np = _numpy()
    values = np.concatenate(chunks)[:target_samples] if chunks else np.empty(0, np.float32)
    peak = float(np.max(np.abs(values))) if len(values) else 0.0
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64)))) if len(values) else 0.0
    return InputLevel(
        device=device,
        duration_ms=round(len(values) * 1000 / TARGET_SAMPLE_RATE),
        peak=peak,
        rms=rms,
        dropped_frames=dropped,
    )


class VadSegmenter(Protocol):
    @property
    def is_speech_detected(self) -> bool: ...

    def accept(self, samples: Any) -> list[SpeechAudio]: ...

    def flush(self) -> list[SpeechAudio]: ...

    def reset(self) -> None: ...


class SherpaSileroVadSegmenter:
    def __init__(
        self,
        model_path: Path,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        endpoint_silence_ms: int = 2_000,
        max_segment_ms: int = 28_000,
        pre_roll_ms: int = 300,
        overlap_ms: int = 1_000,
        sherpa_module: Any | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.endpoint_silence_ms = endpoint_silence_ms
        self.max_segment_ms = max_segment_ms
        self.pre_roll_samples = round(pre_roll_ms * sample_rate / 1000)
        self.overlap_samples = round(overlap_ms * sample_rate / 1000)
        self._sherpa = sherpa_module
        self._vad: Any | None = None
        self._cursor = 0
        self._history: deque[tuple[int, int, Any]] = deque()
        self._overlap_from: int | None = None

    def self_test(self) -> None:
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._vad is not None:
            return
        if not self.model_path.is_file():
            raise AudioError(f"Silero VAD model is missing: {self.model_path}")
        sherpa = self._sherpa or _optional_module("sherpa_onnx", "Silero VAD")
        config = sherpa.VadModelConfig()
        config.silero_vad.model = str(self.model_path)
        config.silero_vad.threshold = self.threshold
        config.silero_vad.min_silence_duration = self.endpoint_silence_ms / 1000
        config.silero_vad.min_speech_duration = self.min_speech_ms / 1000
        config.silero_vad.max_speech_duration = self.max_segment_ms / 1000
        config.sample_rate = self.sample_rate
        if hasattr(config, "validate") and not config.validate():
            raise AudioError("invalid sherpa-onnx Silero VAD configuration")
        self._vad = sherpa.VoiceActivityDetector(
            config,
            buffer_size_in_seconds=max(60, math.ceil(self.max_segment_ms / 1000) + 5),
        )

    @property
    def is_speech_detected(self) -> bool:
        self._ensure_loaded()
        return bool(self._vad.is_speech_detected())

    def _append_history(self, samples: Any) -> None:
        np = _numpy()
        values = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        start = self._cursor
        end = start + len(values)
        self._history.append((start, end, values.copy()))
        self._cursor = end
        keep_samples = round(
            (self.max_segment_ms + self.endpoint_silence_ms) * self.sample_rate / 1000
        ) + max(self.pre_roll_samples, self.overlap_samples)
        trim_before = max(0, self._cursor - keep_samples)
        while self._history and self._history[0][1] <= trim_before:
            self._history.popleft()

    def _history_slice(self, start: int, end: int, fallback: Any) -> Any:
        np = _numpy()
        pieces = [
            values[max(0, start - chunk_start) : min(chunk_end, end) - chunk_start]
            for chunk_start, chunk_end, values in self._history
            if chunk_end > start and chunk_start < end
        ]
        if not pieces:
            return np.ascontiguousarray(fallback, dtype=np.float32).reshape(-1)
        return np.ascontiguousarray(np.concatenate(pieces), dtype=np.float32)

    def _drain(self) -> list[SpeechAudio]:
        np = _numpy()
        segments: list[SpeechAudio] = []
        max_samples = round(self.max_segment_ms * self.sample_rate / 1000)
        while not self._vad.empty():
            native = self._vad.front
            native_samples = np.ascontiguousarray(native.samples, dtype=np.float32).reshape(-1)
            native_start = int(native.start)
            native_end = native_start + len(native_samples)
            desired_start = max(0, native_start - self.pre_roll_samples)
            if self._overlap_from is not None:
                previous_end = self._overlap_from + self.overlap_samples
                if native_start <= previous_end + self.sample_rate:
                    desired_start = min(desired_start, self._overlap_from)
                self._overlap_from = None
            values = self._history_slice(desired_start, native_end, native_samples)
            actual_start = native_end - len(values)
            forced = len(native_samples) >= max(1, max_samples - self.sample_rate)
            segments.append(
                SpeechAudio(
                    samples=values,
                    start_sample=actual_start,
                    end_sample=native_end,
                    forced_endpoint=forced,
                )
            )
            if forced and self.overlap_samples:
                self._overlap_from = max(actual_start, native_end - self.overlap_samples)
            self._vad.pop()
        return segments

    def accept(self, samples: Any) -> list[SpeechAudio]:
        self._ensure_loaded()
        self._append_history(samples)
        self._vad.accept_waveform(samples)
        return self._drain()

    def flush(self) -> list[SpeechAudio]:
        self._ensure_loaded()
        self._vad.flush()
        return self._drain()

    def reset(self) -> None:
        if self._vad is not None:
            self._vad.reset()
        self._cursor = 0
        self._history.clear()
        self._overlap_from = None


class EnergyVadSegmenter:
    """Explicit degraded fallback used only when Silero cannot initialize."""

    def __init__(
        self,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        threshold: float = 0.012,
        endpoint_silence_ms: int = 2_000,
        max_segment_ms: int = 28_000,
        pre_roll_ms: int = 300,
        overlap_ms: int = 1_000,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.endpoint_samples = round(endpoint_silence_ms * sample_rate / 1000)
        self.max_samples = round(max_segment_ms * sample_rate / 1000)
        self.pre_roll_samples = round(pre_roll_ms * sample_rate / 1000)
        self.overlap_samples = round(overlap_ms * sample_rate / 1000)
        self._cursor = 0
        self._active = False
        self._segment_start = 0
        self._silence_samples = 0
        self._active_chunks: list[Any] = []
        self._pre_roll: deque[Any] = deque()
        self._pre_roll_count = 0

    @property
    def is_speech_detected(self) -> bool:
        return self._active

    def _is_speech(self, samples: Any) -> bool:
        np = _numpy()
        if len(samples) == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        return rms >= self.threshold

    def _push_pre_roll(self, samples: Any) -> None:
        self._pre_roll.append(samples.copy())
        self._pre_roll_count += len(samples)
        while self._pre_roll and self._pre_roll_count > self.pre_roll_samples:
            excess = self._pre_roll_count - self.pre_roll_samples
            first = self._pre_roll[0]
            if len(first) <= excess:
                self._pre_roll.popleft()
                self._pre_roll_count -= len(first)
            else:
                self._pre_roll[0] = first[excess:].copy()
                self._pre_roll_count -= excess

    def _finish(self, forced: bool) -> SpeechAudio | None:
        np = _numpy()
        if not self._active_chunks:
            return None
        values = np.ascontiguousarray(np.concatenate(self._active_chunks), dtype=np.float32)
        segment = SpeechAudio(
            samples=values,
            start_sample=self._segment_start,
            end_sample=self._segment_start + len(values),
            forced_endpoint=forced,
        )
        overlap = (
            values[-self.overlap_samples :].copy()
            if forced and self.overlap_samples
            else None
        )
        self._active = False
        self._active_chunks = []
        self._silence_samples = 0
        self._pre_roll.clear()
        self._pre_roll_count = 0
        if overlap is not None and len(overlap):
            self._push_pre_roll(overlap)
        return segment

    def accept(self, samples: Any) -> list[SpeechAudio]:
        np = _numpy()
        values = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        chunk_start = self._cursor
        self._cursor += len(values)
        speech = self._is_speech(values)
        if not self._active:
            if speech:
                self._active = True
                self._segment_start = max(0, chunk_start - self._pre_roll_count)
                self._active_chunks = [*self._pre_roll, values.copy()]
                self._pre_roll.clear()
                self._pre_roll_count = 0
            else:
                self._push_pre_roll(values)
            return []

        self._active_chunks.append(values.copy())
        self._silence_samples = 0 if speech else self._silence_samples + len(values)
        length = sum(len(chunk) for chunk in self._active_chunks)
        if length >= self.max_samples:
            segment = self._finish(True)
            return [segment] if segment is not None else []
        if self._silence_samples >= self.endpoint_samples:
            segment = self._finish(False)
            return [segment] if segment is not None else []
        return []

    def flush(self) -> list[SpeechAudio]:
        segment = self._finish(False)
        return [segment] if segment is not None else []

    def reset(self) -> None:
        self._cursor = 0
        self._active = False
        self._segment_start = 0
        self._silence_samples = 0
        self._active_chunks = []
        self._pre_roll.clear()
        self._pre_roll_count = 0


def create_vad_segmenter(
    model_path: Path,
    *,
    sample_rate: int = TARGET_SAMPLE_RATE,
    endpoint_silence_ms: int = 2_000,
    max_segment_ms: int = 28_000,
    pre_roll_ms: int = 300,
    overlap_ms: int = 1_000,
    sherpa_module: Any | None = None,
) -> tuple[VadSegmenter, str | None]:
    primary = SherpaSileroVadSegmenter(
        model_path,
        sample_rate=sample_rate,
        endpoint_silence_ms=endpoint_silence_ms,
        max_segment_ms=max_segment_ms,
        pre_roll_ms=pre_roll_ms,
        overlap_ms=overlap_ms,
        sherpa_module=sherpa_module,
    )
    try:
        primary.self_test()
        return primary, None
    except Exception as exc:
        fallback = EnergyVadSegmenter(
            sample_rate=sample_rate,
            endpoint_silence_ms=endpoint_silence_ms,
            max_segment_ms=max_segment_ms,
            pre_roll_ms=pre_roll_ms,
            overlap_ms=overlap_ms,
        )
        return fallback, f"Silero VAD unavailable; using degraded energy VAD: {exc}"


class FlacWriter(Protocol):
    def __call__(
        self,
        file: str,
        data: Any,
        samplerate: int,
        *,
        format: str,
        subtype: str,
    ) -> None: ...


class FlacReader(Protocol):
    def __call__(
        self,
        file: str,
        *,
        dtype: str,
        always_2d: bool,
    ) -> tuple[Any, int]: ...


@dataclass(frozen=True, slots=True)
class RecoveredFlac:
    segment_id: str
    path: Path
    sample_rate: int
    frame_count: int


class FlacSpool:
    def __init__(
        self,
        root: Path,
        *,
        limit_bytes: int,
        writer: FlacWriter | None = None,
        reader: FlacReader | None = None,
    ) -> None:
        if limit_bytes <= 0:
            raise ValueError("spool limit must be positive")
        self.root = Path(root)
        self.limit_bytes = limit_bytes
        self._writer = writer
        self._reader = reader
        self.recovery_warnings: list[str] = []
        self.recovered_partials: list[RecoveredFlac] = []

    def usage_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    @property
    def usage_ratio(self) -> float:
        return self.usage_bytes() / self.limit_bytes

    @property
    def available_bytes(self) -> int:
        return max(0, self.limit_bytes - self.usage_bytes())

    @property
    def volume_free_bytes(self) -> int:
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return int(shutil.disk_usage(probe).free)

    def can_reserve(self, byte_count: int) -> bool:
        if byte_count < 0:
            raise ValueError("reservation cannot be negative")
        return self.available_bytes >= byte_count and self.volume_free_bytes >= (
            byte_count + VOLUME_HEADROOM_BYTES
        )

    def recover_partials(self) -> list[RecoveredFlac]:
        self.root.mkdir(parents=True, exist_ok=True)
        reader = self._reader
        if reader is None:
            soundfile = _optional_module("soundfile", "FLAC spool recovery")
            reader = soundfile.read
        recovered: list[RecoveredFlac] = []
        self.recovery_warnings = []
        suffix = ".partial.flac"
        for partial in list(self.root.glob(f".*{suffix}")):
            try:
                body = partial.name[1 : -len(suffix)]
                identifier = body.split(".", 1)[0]
                uuid.UUID(identifier)
                target = self.root / f"{identifier}.flac"
                if target.exists():
                    partial.unlink()
                    continue
                samples, sample_rate = reader(
                    str(partial),
                    dtype="float32",
                    always_2d=False,
                )
                if sample_rate <= 0 or len(samples) == 0:
                    raise AudioError("partial FLAC contains no decodable audio")
                os.replace(partial, target)
                recovered.append(
                    RecoveredFlac(
                        segment_id=identifier,
                        path=target,
                        sample_rate=sample_rate,
                        frame_count=len(samples),
                    )
                )
            except Exception as exc:
                quarantine = partial.with_name(f"{partial.name}.corrupt")
                try:
                    os.replace(partial, quarantine)
                except OSError:
                    quarantine = partial
                self.recovery_warnings.append(
                    f"could not recover partial FLAC {partial.name}: {exc}; "
                    f"kept as {quarantine.name}"
                )
        self.recovered_partials = recovered
        return recovered

    def write(
        self,
        samples: Any,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        segment_id: str | None = None,
    ) -> Path:
        return self._write(
            samples,
            sample_rate=sample_rate,
            segment_id=segment_id,
            volume_headroom_bytes=VOLUME_HEADROOM_BYTES,
            enforce_logical_limit=True,
        )

    def write_emergency(
        self,
        samples: Any,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        segment_id: str | None = None,
    ) -> Path:
        """Persist already accepted speech using the reserved volume headroom."""

        return self._write(
            samples,
            sample_rate=sample_rate,
            segment_id=segment_id,
            volume_headroom_bytes=0,
            enforce_logical_limit=False,
        )

    def _write(
        self,
        samples: Any,
        *,
        sample_rate: int,
        segment_id: str | None,
        volume_headroom_bytes: int,
        enforce_logical_limit: bool,
    ) -> Path:
        np = _numpy()
        values = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        estimated_bytes = len(values) * 2
        usage_before = self.usage_bytes()
        if enforce_logical_limit and usage_before + estimated_bytes > self.limit_bytes:
            raise SpoolLimitExceeded(
                f"FLAC spool limit would be exceeded ({self.limit_bytes} bytes)"
            )
        identifier = segment_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
            raise ValueError("segment_id contains unsafe filename characters")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.volume_free_bytes < estimated_bytes + volume_headroom_bytes:
            raise SpoolLimitExceeded(
                "volume lacks reserved space for a complete FLAC segment"
            )
        final_path = self.root / f"{identifier}.flac"
        temporary = self.root / f".{identifier}.{uuid.uuid4().hex}.partial.flac"
        writer = self._writer
        if writer is None:
            soundfile = _optional_module("soundfile", "FLAC spool writing")
            writer = soundfile.write
        installed = False
        try:
            writer(
                str(temporary),
                values,
                sample_rate,
                format="FLAC",
                subtype="PCM_16",
            )
            if (
                enforce_logical_limit
                and usage_before + temporary.stat().st_size > self.limit_bytes
            ):
                raise SpoolLimitExceeded(
                    f"FLAC spool limit would be exceeded ({self.limit_bytes} bytes)"
                )
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, final_path)
            installed = True
        finally:
            if temporary.exists() and (installed or temporary.stat().st_size == 0):
                temporary.unlink(missing_ok=True)
        return final_path


__all__ = [
    "TARGET_SAMPLE_RATE",
    "AudioChunk",
    "AudioDeviceNotFound",
    "AudioError",
    "EnergyVadSegmenter",
    "FlacSpool",
    "InputLevel",
    "RecoveredFlac",
    "InputDevice",
    "SherpaSileroVadSegmenter",
    "SpeechAudio",
    "SpoolLimitExceeded",
    "StreamingResampler",
    "VadSegmenter",
    "WasapiMicrophone",
    "create_vad_segmenter",
    "default_wasapi_input_device",
    "list_wasapi_input_devices",
    "measure_input_level",
    "resolve_wasapi_input_device",
]
