"""Durable-write bookkeeping for the recorder loop.

Captured speech is only allowed to disappear once it is on disk. When a spool write
fails, the segment is parked in `SpoolState.pending_writes` with its samples still in
memory and retried every iteration until it lands — which is also why a stop request
cannot finish while writes are pending.

Retrying is not simply "write it again": a previous attempt may have left a partial
FLAC behind. `SpoolCoordinator.drain_pending_write` adopts that file when its frame
count matches exactly, and otherwise moves it aside before rewriting, so a truncated
prefix can never be mistaken for a complete segment.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audio import SpoolLimitExceeded
from .config import AppConfig
from .types import CapturedSegment, Severity, WorkerState

EmitStatus = Callable[..., None]
"""Publish a recorder `WorkerStatus`; bound to the recorder's worker kind by the caller."""


@dataclass(frozen=True, slots=True)
class PendingSpoolWrite:
    """A captured segment whose audio has not reached the spool yet."""

    segment: CapturedSegment
    samples: Any
    forced_endpoint: bool
    end_sample: int


@dataclass(slots=True)
class SpoolState:
    """Backpressure and retry bookkeeping for durable writes."""

    capacity_blocked: bool = False
    warning_active: bool = False
    last_recovery_check: float = 0.0
    pending_writes: deque[PendingSpoolWrite] = field(default_factory=deque)
    recovered_metadata: dict[str, Any] = field(default_factory=dict)
    rejected_recovery_backups: dict[str, Path] = field(default_factory=dict)
    next_retry: float = 0.0
    last_retry_status: float = 0.0
    stream_reset_pending: bool = False


class SpoolCoordinator:
    """Owns `SpoolState` and every interaction with the spool's durability guarantees."""

    def __init__(
        self,
        state: SpoolState,
        spool: Any,
        config: AppConfig,
        *,
        status: EmitStatus,
        publish_persisted: Callable[[CapturedSegment, Path], None],
        reset_stream_after_drain: Callable[[], None],
        is_stopping: Callable[[], bool],
        recording_state: Callable[[], WorkerState],
        reserve_bytes: int,
        retry_delay: float,
    ) -> None:
        self.state = state
        self._spool = spool
        self._config = config
        self._status = status
        self._publish_persisted = publish_persisted
        self._reset_stream_after_drain = reset_stream_after_drain
        self._is_stopping = is_stopping
        self._recording_state = recording_state
        self._reserve_bytes = reserve_bytes
        self._retry_delay = retry_delay

    @property
    def usage_ratio(self) -> float:
        return float(getattr(self._spool, "usage_ratio", 0.0))

    def has_headroom(self) -> bool:
        checker = getattr(self._spool, "can_reserve", None)
        if callable(checker):
            return bool(checker(self._reserve_bytes))
        return self.usage_ratio < 1.0

    # -- warning hysteresis --------------------------------------------------------

    def _clear_warning(self, state: WorkerState, ratio: float) -> None:
        self.state.warning_active = False
        self._status(
            state,
            "audio spool usage recovered below warning threshold",
            metadata={"spool_ratio": ratio},
        )

    def poll_warning_recovery(self, now: float) -> None:
        """Clear a standing spool warning once usage drops back, checked at most 1 Hz."""
        state = self.state
        if not state.warning_active or now - state.last_recovery_check < 1.0:
            return
        state.last_recovery_check = now
        ratio = self.usage_ratio
        if ratio < self._config.spool_warning_ratio:
            self._clear_warning(self._recording_state(), ratio)

    def _report_usage_after_write(self) -> None:
        state = self.state
        ratio = self.usage_ratio
        if ratio >= self._config.spool_warning_ratio and not state.warning_active:
            state.warning_active = True
            self._status(
                WorkerState.DEGRADED,
                f"audio spool is {ratio:.0%} full",
                severity=Severity.ERROR,
                metadata={"spool_ratio": ratio},
            )
        elif ratio < self._config.spool_warning_ratio and state.warning_active:
            self._clear_warning(WorkerState.RECORDING, ratio)

    # -- hard limit ----------------------------------------------------------------

    def report_hard_limit(self, message: str) -> None:
        """Announce that the spool cannot reserve room for another segment."""
        self.state.capacity_blocked = True
        self._status(
            WorkerState.ERROR,
            message,
            severity=Severity.ERROR,
            metadata={"spool_hard_limit": True, "reserved_bytes": self._reserve_bytes},
        )

    # -- the write itself ----------------------------------------------------------

    def write_segment(
        self,
        segment_for: Callable[[Path], CapturedSegment],
        samples: Any,
        *,
        segment_id: str,
        forced_endpoint: bool,
        end_sample: int,
        expected_path: Path,
        monotonic: Callable[[], float],
        on_capture_stop: Callable[[], None],
    ) -> bool:
        """Write captured speech, parking it for retry if anything goes wrong.

        Publishing is inside the same `try` as the write on purpose: if the segment
        cannot be announced it has to be retried too, or the audio becomes orphaned.

        `monotonic` is a callable rather than a timestamp because the clock is only
        read on the failure path.

        Returns True once the segment is either published or safely queued.
        """
        state = self.state
        try:
            audio_path = self._spool.write(
                samples,
                sample_rate=self._config.audio_sample_rate,
                segment_id=segment_id,
            )
            self._publish_persisted(segment_for(Path(audio_path)), Path(audio_path))
            self._report_usage_after_write()
            return True
        except Exception as exc:
            state.pending_writes.append(
                PendingSpoolWrite(
                    segment=segment_for(expected_path),
                    # The retry needs the audio, so it must outlive the capture buffer.
                    samples=(
                        samples.copy() if callable(getattr(samples, "copy", None)) else samples
                    ),
                    forced_endpoint=forced_endpoint,
                    end_sample=end_sample,
                )
            )
            hard_limit = isinstance(exc, SpoolLimitExceeded)
            state.capacity_blocked = state.capacity_blocked or hard_limit
            failed_at = monotonic()
            state.next_retry = (
                failed_at if self._is_stopping() else failed_at + self._retry_delay
            )
            state.stream_reset_pending = True
            on_capture_stop()
            self._status(
                WorkerState.ERROR,
                (
                    "audio spool hard limit reached; captured speech is held for retry"
                    if hard_limit
                    else "failed to spool captured speech; captured speech is held for retry"
                )
                + f": {exc}",
                severity=Severity.ERROR,
                metadata={
                    "spool_write_failed": True,
                    "spool_hard_limit": hard_limit,
                    "segment_id": segment_id,
                    "recovery_path": str(expected_path),
                    "pending_spool_writes": len(state.pending_writes),
                },
            )
            return True

    # -- retry ---------------------------------------------------------------------

    def _adopt_existing_partial(self, pending: PendingSpoolWrite) -> Path | None:
        """Find a usable FLAC already on disk for this segment, rejecting truncated ones."""
        state = self.state
        segment_id = pending.segment.segment_id
        expected_path = Path(pending.segment.audio_path)
        recovered_path: Path | None = None
        recovered_info = state.recovered_metadata.get(segment_id)
        rejected_backup = state.rejected_recovery_backups.get(segment_id)
        if expected_path.is_file() and recovered_info is None and rejected_backup is None:
            recovered_path = expected_path

        recover = getattr(self._spool, "recover_partials", None)
        if (
            not expected_path.is_file()
            and not (rejected_backup is not None and rejected_backup.is_file())
            and callable(recover)
        ):
            with suppress(Exception):
                state.recovered_metadata.update((item.segment_id, item) for item in recover())
            recovered_info = state.recovered_metadata.get(segment_id)

        if recovered_info is None:
            return recovered_path

        exact_recovery = (
            int(recovered_info.sample_rate) == pending.segment.sample_rate
            and int(recovered_info.frame_count) == len(pending.samples)
        )
        if exact_recovery and Path(recovered_info.path).is_file():
            return Path(recovered_info.path)

        state.recovered_metadata.pop(segment_id, None)
        recovered_file = Path(recovered_info.path)
        backup = recovered_file.with_name(f".{segment_id}.{uuid.uuid4().hex}.partial.flac")
        try:
            os.replace(recovered_file, backup)
        except OSError:
            # Keep the decodable prefix at its current path. The id remains rejected
            # so it cannot be mistaken for a full segment on the next in-process retry.
            backup = recovered_file
        state.rejected_recovery_backups[segment_id] = backup
        self._status(
            WorkerState.ERROR,
            "partial FLAC length mismatch; rewriting from complete in-memory audio",
            severity=Severity.ERROR,
            metadata={
                "partial_length_mismatch": True,
                "segment_id": segment_id,
                "expected_frames": len(pending.samples),
                "recovered_frames": int(recovered_info.frame_count),
            },
        )
        return None

    def drain_pending_write(self, now: float) -> None:
        """Make one attempt at the oldest pending write. Always ends the loop iteration."""
        state = self.state
        if now < state.next_retry:
            time.sleep(min(0.05, state.next_retry - now))
            return

        pending = state.pending_writes[0]
        segment_id = pending.segment.segment_id
        recovered_path = self._adopt_existing_partial(pending)
        try:
            # A stop already in progress uses the emergency path, which is allowed to
            # exceed the spool budget rather than lose captured speech.
            write_pending = (
                getattr(self._spool, "write_emergency", self._spool.write)
                if self._is_stopping()
                else self._spool.write
            )
            audio_path = recovered_path or Path(
                write_pending(
                    pending.samples,
                    sample_rate=pending.segment.sample_rate,
                    segment_id=segment_id,
                )
            )
            if not audio_path.is_file():
                raise OSError(f"spool write returned missing path: {audio_path}")
        except Exception as exc:
            state.next_retry = now if self._is_stopping() else now + self._retry_delay
            if now - state.last_retry_status >= 30.0:
                state.last_retry_status = now
                self._status(
                    WorkerState.ERROR,
                    f"captured speech is still waiting for durable storage: {exc}",
                    severity=Severity.ERROR,
                    metadata={
                        "spool_write_retry": True,
                        "segment_id": segment_id,
                        "pending_spool_writes": len(state.pending_writes),
                    },
                )
            if self._is_stopping():
                time.sleep(0.05)
            return

        self._publish_persisted(pending.segment, audio_path)
        state.pending_writes.popleft()
        state.recovered_metadata.pop(segment_id, None)
        rejected_backup = state.rejected_recovery_backups.pop(segment_id, None)
        if rejected_backup is not None and rejected_backup != audio_path:
            with suppress(OSError):
                rejected_backup.unlink(missing_ok=True)
        state.next_retry = now
        state.capacity_blocked = not self.has_headroom()
        if not state.pending_writes and state.stream_reset_pending:
            self._reset_stream_after_drain()
            state.stream_reset_pending = False
        self._status(
            WorkerState.READY,
            "captured speech was recovered to durable FLAC storage",
            metadata={
                "spool_write_recovered": True,
                "segment_id": segment_id,
                "pending_spool_writes": len(state.pending_writes),
            },
        )


__all__ = [
    "PendingSpoolWrite",
    "SpoolCoordinator",
    "SpoolState",
]
