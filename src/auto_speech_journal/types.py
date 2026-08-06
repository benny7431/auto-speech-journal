from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import MicrophoneSelection


class SegmentState(StrEnum):
    CAPTURED = "captured"
    FINALIZING = "finalizing"
    FINAL_READY = "final_ready"
    EXPORTED = "exported"
    AUDIO_DELETED = "audio_deleted"
    RETRY = "retry"
    FAILED = "failed"


class WorkerKind(StrEnum):
    RECORDER = "recorder"
    PREVIEW = "preview"
    FINALIZER = "finalizer"


class WorkerState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    RECORDING = "recording"
    PAUSED = "paused"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPED = "stopped"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WorkerCommandKind(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    TRANSCRIBE = "transcribe"
    UPDATE_HOTWORDS = "update_hotwords"
    RECONFIGURE_INPUT = "reconfigure_input"
    RETRY_PREFERRED_INPUT = "retry_preferred_input"


class InputRoute(StrEnum):
    """How the recorder's active input relates to the saved preference."""

    PENDING = "pending"
    SKIPPED = "skipped"
    PREFERRED = "preferred"
    SYSTEM_DEFAULT = "system_default"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PartialUpdate:
    segment_id: str
    text: str
    started_at_utc: datetime
    raw_text: str = ""
    emitted_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        ensure_utc(self.started_at_utc)
        ensure_utc(self.emitted_at_utc)

    @property
    def normalized_text(self) -> str:
        """The s2tw-normalized preview; ``text`` remains the stable UI API."""

        return self.text


@dataclass(frozen=True, slots=True)
class AudioLevelUpdate:
    """Best-effort microphone level for responsive UI feedback."""

    rms_dbfs: float
    peak_dbfs: float
    speech_active: bool
    segment_id: str | None = None
    measured_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        rms = float(self.rms_dbfs)
        peak = float(self.peak_dbfs)
        if not math.isfinite(rms) or not math.isfinite(peak):
            raise ValueError("audio levels must be finite")
        if not (-120.0 <= rms <= 0.0) or not (-120.0 <= peak <= 0.0):
            raise ValueError("audio levels must be in [-120, 0] dBFS")
        if rms > peak:
            raise ValueError("rms_dbfs cannot exceed peak_dbfs")
        if not isinstance(self.speech_active, bool):
            raise ValueError("speech_active must be boolean")
        if self.segment_id is not None and not self.segment_id.strip():
            raise ValueError("segment_id cannot be empty")
        object.__setattr__(self, "rms_dbfs", rms)
        object.__setattr__(self, "peak_dbfs", peak)
        object.__setattr__(self, "measured_at_utc", ensure_utc(self.measured_at_utc))


@dataclass(frozen=True, slots=True)
class CapturedSegment:
    segment_id: str
    audio_path: Path
    started_at_utc: datetime
    ended_at_utc: datetime
    preview_text: str = ""
    sample_rate: int = 16_000
    duration_ms: int = 0
    preview_raw_text: str = ""
    leading_overlap_ms: int = 0
    previous_segment_id: str | None = None
    preview_pending: bool = False

    def __post_init__(self) -> None:
        ensure_utc(self.started_at_utc)
        ensure_utc(self.ended_at_utc)
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("segment end precedes start")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.leading_overlap_ms < 0:
            raise ValueError("leading_overlap_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class PreviewAudioChunk:
    """Bounded recorder-to-preview IPC payload.

    Preview loss is explicitly degradable; this payload is never the durable
    source for VAD or FLAC output.
    """

    segment_id: str
    samples: Any
    sample_rate: int
    segment_started_at_utc: datetime

    def __post_init__(self) -> None:
        ensure_utc(self.segment_started_at_utc)
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")


@dataclass(frozen=True, slots=True)
class PreviewFinalize:
    """Ordered preview flush request for an already durable FLAC segment."""

    segment: CapturedSegment


@dataclass(frozen=True, slots=True)
class PreviewEndpointResult:
    segment_id: str
    raw_text: str
    normalized_text: str
    success: bool = True
    error: str | None = None
    completed_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        ensure_utc(self.completed_at_utc)


@dataclass(frozen=True, slots=True)
class FinalResult:
    segment_id: str
    raw_text: str
    normalized_text: str
    engine_profile: str
    success: bool = True
    error: str | None = None
    latency_ms: int = 0
    completed_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        ensure_utc(self.completed_at_utc)


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    worker: WorkerKind
    state: WorkerState
    message: str = ""
    severity: Severity = Severity.INFO
    queue_size: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    emitted_at_utc: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class InputRouteRequest:
    """Idempotent request to change the recorder's saved input selection."""

    request_id: str
    selection: MicrophoneSelection

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id cannot be empty")
        self.selection.validate()


@dataclass(frozen=True, slots=True)
class InputRouteUpdate:
    """Recorder route state published independently from worker health."""

    request_id: str | None
    preferred_input_name: str | None
    active_input_name: str | None
    input_route: InputRoute
    input_switching: bool = False
    preferred_input_available: bool = False
    reason: str | None = None
    emitted_at_utc: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.request_id is not None and not self.request_id.strip():
            raise ValueError("request_id cannot be empty")
        if not isinstance(self.preferred_input_available, bool):
            raise ValueError("preferred_input_available must be boolean")
        if not isinstance(self.input_switching, bool):
            raise ValueError("input_switching must be boolean")
        object.__setattr__(self, "emitted_at_utc", ensure_utc(self.emitted_at_utc))


@dataclass(frozen=True, slots=True)
class WorkerCommand:
    kind: WorkerCommandKind
    payload: Any = None
