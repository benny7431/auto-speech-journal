"""Low-latency streaming preview for the recorder loop.

Preview is the *droppable* layer. Everything here can fail or fall behind without
threatening the recording: when the preview queue backs up, chunks are dropped and the
recorder keeps feeding VAD and the FLAC spool at full rate. That asymmetry is the whole
design — durable capture must never wait on the preview.

Preview runs one of two ways. With an inline engine the recorder transcribes in-process
and emits `PartialUpdate`s directly; with a preview queue it hands raw chunks to a
separate process. `PreviewCoordinator` presents the same surface for both.

The pre-roll deque exists because speech is detected slightly after it starts. Audio
from just before the trigger is kept so the preview of a segment begins at the first
word rather than partway into it.
"""

from __future__ import annotations

import queue
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .audio import AudioChunk
from .config import AppConfig
from .preview_engine import PreviewHypothesis
from .types import PartialUpdate, PreviewAudioChunk, PreviewFinalize, Severity, WorkerState

EmitStatus = Callable[..., None]
"""Publish a preview `WorkerStatus`; bound to the preview worker kind by the caller."""

EmitPartial = Callable[[PartialUpdate], bool]
"""Publish a partial transcript; returns False when it was dropped under backpressure."""


@dataclass(frozen=True, slots=True)
class PreviewPrerollChunk:
    """Audio held back so a segment's preview can start before speech was detected."""

    samples: Any
    sample_rate: int
    started_at_utc: datetime


@dataclass(slots=True)
class PreviewState:
    """Preview text under accumulation, what was last shown, and the pre-roll buffer.

    `prefix` holds text the engine has already committed at an endpoint; the live tail
    is appended to it, so an endpoint mid-segment does not discard what came before.
    """

    current_text: str = ""
    current_raw_text: str = ""
    prefix: str = ""
    raw_prefix: str = ""
    last_emit: float = 0.0
    last_emitted: str = ""
    last_emitted_raw: str = ""
    has_emitted: bool = False
    dropped: bool = False
    backpressure_active: bool = False
    preroll: deque[PreviewPrerollChunk] = field(default_factory=deque)
    preroll_samples: int = 0

    def reset_for_segment(self) -> None:
        """Clear per-segment text and emission bookkeeping, keeping backpressure state."""
        self.current_text = ""
        self.current_raw_text = ""
        self.prefix = ""
        self.raw_prefix = ""
        self.last_emit = 0.0
        self.last_emitted = ""
        self.last_emitted_raw = ""
        self.has_emitted = False
        self.dropped = False


def hypothesis_texts(hypothesis: Any) -> tuple[str, str]:
    """Split a preview hypothesis into its raw and normalized forms."""
    normalized = str(
        getattr(hypothesis, "normalized_text", getattr(hypothesis, "text", "")) or ""
    ).strip()
    raw = str(getattr(hypothesis, "raw_text", "") or normalized).strip()
    return raw, normalized


class PreviewCoordinator:
    """Owns `PreviewState` and every path audio takes toward a preview."""

    def __init__(
        self,
        state: PreviewState,
        config: AppConfig,
        *,
        inline_preview: Any | None,
        preview_queue: Any | None,
        status: EmitStatus,
        emit_partial: EmitPartial,
        queue_size: Callable[[], int],
    ) -> None:
        self.state = state
        self._config = config
        self._inline = inline_preview
        self._queue = preview_queue
        self._status = status
        self._emit_partial = emit_partial
        self._queue_size = queue_size

    # -- pre-roll ------------------------------------------------------------------

    def clear_preroll(self) -> None:
        self.state.preroll.clear()
        self.state.preroll_samples = 0

    def remember_preroll(self, chunk: AudioChunk) -> None:
        """Keep the most recent `pre_roll_ms` of audio, trimming the oldest chunk."""
        state = self.state
        limit = round(self._config.pre_roll_ms * chunk.sample_rate / 1000)
        if limit <= 0:
            self.clear_preroll()
            return
        samples = _copied(chunk.samples)
        state.preroll.append(PreviewPrerollChunk(samples, chunk.sample_rate, chunk.started_at_utc))
        state.preroll_samples += len(samples)
        while state.preroll and state.preroll_samples > limit:
            excess = state.preroll_samples - limit
            first = state.preroll[0]
            if len(first.samples) <= excess:
                state.preroll.popleft()
                state.preroll_samples -= len(first.samples)
                continue
            # Trim within the oldest chunk and advance its timestamp to match.
            trimmed = first.samples[excess:].copy()
            state.preroll[0] = PreviewPrerollChunk(
                trimmed,
                first.sample_rate,
                first.started_at_utc + timedelta(seconds=excess / first.sample_rate),
            )
            state.preroll_samples -= excess

    def take_preroll(self) -> list[PreviewPrerollChunk]:
        """Hand over the buffered pre-roll and empty it."""
        chunks = list(self.state.preroll)
        self.clear_preroll()
        return chunks

    # -- out-of-process queue ------------------------------------------------------

    def offer(self, item: PreviewAudioChunk | PreviewFinalize) -> bool:
        """Hand an item to the preview process, dropping it if the queue is full."""
        state = self.state
        if self._queue is None or state.dropped:
            return False
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            state.dropped = True
            if not state.backpressure_active:
                state.backpressure_active = True
                self._status(
                    WorkerState.DEGRADED,
                    "preview queue is full; durable audio capture continues without preview",
                    severity=Severity.WARNING,
                    queue_size=self._queue_size(),
                    metadata={"preview_backpressure": True},
                )
            return False
        if state.backpressure_active:
            state.backpressure_active = False
            self._status(
                WorkerState.READY,
                "preview queue recovered",
                queue_size=self._queue_size(),
            )
        return True

    # -- feeding -------------------------------------------------------------------

    def feed(
        self,
        chunks: Iterable[PreviewPrerollChunk],
        *,
        segment_id: str,
        segment_started_at: datetime,
        monotonic: Callable[[], float],
    ) -> None:
        for chunk in chunks:
            if self._inline is None:
                self.offer(
                    PreviewAudioChunk(
                        segment_id=segment_id,
                        samples=chunk.samples,
                        sample_rate=chunk.sample_rate,
                        segment_started_at_utc=segment_started_at,
                    )
                )
                continue
            self._accept_inline(
                chunk,
                segment_id=segment_id,
                segment_started_at=segment_started_at,
                monotonic=monotonic,
            )

    def _accept_inline(
        self,
        chunk: PreviewPrerollChunk,
        *,
        segment_id: str,
        segment_started_at: datetime,
        monotonic: Callable[[], float],
    ) -> None:
        state = self.state
        try:
            hypothesis: PreviewHypothesis = self._inline.accept(
                chunk.samples,
                sample_rate=chunk.sample_rate,
            )
            raw_tail, normalized_tail = hypothesis_texts(hypothesis)
            state.current_text = (state.prefix + normalized_tail).strip()
            state.current_raw_text = (state.raw_prefix + raw_tail).strip()
            if hypothesis.is_endpoint:
                state.prefix = state.current_text
                state.raw_prefix = state.current_raw_text
            if self._should_emit(hypothesis, monotonic):
                self._publish_partial(
                    segment_id=segment_id,
                    segment_started_at=segment_started_at,
                    monotonic=monotonic,
                )
        except Exception as exc:
            self._status(
                WorkerState.DEGRADED,
                f"streaming preview failed; durable capture continues: {exc}",
                severity=Severity.WARNING,
            )

    def _should_emit(self, hypothesis: Any, monotonic: Callable[[], float]) -> bool:
        """Rate-limit partials, except at an endpoint which always publishes."""
        state = self.state
        due = monotonic() - state.last_emit >= (self._config.preview_interval_ms / 1000)
        changed_since_emit = (
            state.current_text != state.last_emitted
            or state.current_raw_text != state.last_emitted_raw
        )
        return bool(
            state.current_text
            and (
                (changed_since_emit and (not state.has_emitted or due))
                or hypothesis.is_endpoint
            )
        )

    def _publish_partial(
        self,
        *,
        segment_id: str,
        segment_started_at: datetime,
        monotonic: Callable[[], float],
    ) -> None:
        state = self.state
        if not self._emit_partial(
            PartialUpdate(
                segment_id=segment_id,
                text=state.current_text,
                raw_text=state.current_raw_text,
                started_at_utc=segment_started_at,
            )
        ):
            return
        state.last_emit = monotonic()
        state.has_emitted = True
        state.last_emitted = state.current_text
        state.last_emitted_raw = state.current_raw_text

    # -- finishing -----------------------------------------------------------------

    def finish_for_flush(self) -> tuple[str, str]:
        """Close out the engine when the stream is being torn down.

        Returns empty strings if the engine fails, because a flush has no partial
        result worth keeping — the final transcription will redo the work.
        """
        if self._inline is None:
            return "", ""
        state = self.state
        try:
            raw_tail, normalized_tail = hypothesis_texts(self._inline.finish())
        except Exception as exc:
            self._status(
                WorkerState.DEGRADED,
                f"preview flush failed: {exc}",
                severity=Severity.WARNING,
            )
            return "", ""
        return (state.prefix + normalized_tail).strip(), (state.raw_prefix + raw_tail).strip()

    def finish_for_segment(self) -> tuple[str, str]:
        """Close out the engine for a completed segment.

        Unlike `finish_for_flush`, the accumulated text is kept as a fallback: this
        preview is the text the segment will carry until final transcription replaces it.
        """
        state = self.state
        flushed = state.current_text
        flushed_raw = state.current_raw_text
        if self._inline is None:
            return flushed, flushed_raw
        try:
            raw_tail, normalized_tail = hypothesis_texts(self._inline.finish())
            flushed = (state.prefix + normalized_tail).strip() or state.current_text
            flushed_raw = (
                (state.raw_prefix + raw_tail).strip() or state.current_raw_text or flushed
            )
        except Exception:
            pass
        return flushed, flushed_raw


def _copied(samples: Any) -> Any:
    """Detach samples from a buffer the caller may reuse."""
    return samples.copy() if callable(getattr(samples, "copy", None)) else samples


def as_preview_chunk(chunk: AudioChunk) -> PreviewPrerollChunk:
    """Wrap a live capture chunk so it can go through the same path as pre-roll."""
    return PreviewPrerollChunk(_copied(chunk.samples), chunk.sample_rate, chunk.started_at_utc)


__all__ = [
    "PreviewCoordinator",
    "PreviewPrerollChunk",
    "PreviewState",
    "as_preview_chunk",
    "hypothesis_texts",
]
