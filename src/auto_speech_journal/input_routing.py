"""Microphone selection, fallback and hot-switching for the recorder loop.

The recorder never opens a device inline. Every change — a user picking a different
microphone, or the watchdog noticing the Windows default moved — becomes a pending
request that `RouteCoordinator.apply_pending_route` applies at a safe boundary, so a
switch can be deferred while captured audio is still waiting to become durable.

Resolution yields *stable fingerprints*, never PortAudio indexes, because indexes are
renumbered whenever the device catalog changes.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any

from .audio import (
    AudioDeviceNotFound,
    InputDevice,
    WasapiMicrophone,
    default_wasapi_input_device,
    resolve_wasapi_input_device,
)
from .config import AppConfig, DeviceFingerprint, MicrophoneMode, MicrophoneSelection
from .types import (
    InputRoute,
    InputRouteRequest,
    InputRouteUpdate,
    Severity,
    WorkerState,
)

EmitEvent = Callable[[InputRouteUpdate], Any]
"""Publish a route change to the supervisor."""

EmitStatus = Callable[..., None]
"""Publish a recorder `WorkerStatus`; bound to the recorder's worker kind by the caller."""


@dataclass(frozen=True, slots=True)
class CaptureCandidate:
    """One device the recorder may open, in preference order."""

    fingerprint: DeviceFingerprint
    route: InputRoute
    catalog_name: str
    resolved_device: InputDevice | None = None


@dataclass(frozen=True, slots=True)
class InputRouteResolution:
    """The ordered candidates a selection resolves to right now."""

    candidates: tuple[CaptureCandidate, ...]
    preferred_input_name: str | None
    preferred_input_available: bool
    reason: str | None = None


@dataclass(slots=True)
class RouteState:
    """Which microphone the recorder is on, and the in-flight request to change it."""

    selection: MicrophoneSelection
    active_route: InputRoute
    capture: Any | None = None
    active_input_name: str | None = None
    active_fingerprint: DeviceFingerprint | None = None
    preferred_input_available: bool = False
    input_route_reason: str | None = None
    pending_route_request: InputRouteRequest | None = None
    pending_include_preferred: bool = True
    seen_request_ids: set[str] = field(default_factory=set)
    next_route_check: float = 0.0
    next_switch_attempt: float = 0.0
    next_start_attempt: float = 0.0

    @classmethod
    def for_selection(cls, selection: MicrophoneSelection) -> RouteState:
        return cls(selection=selection, active_route=cls.idle_route(selection))

    @staticmethod
    def idle_route(selection: MicrophoneSelection) -> InputRoute:
        """The route to report while no capture is open."""
        if selection.mode == MicrophoneMode.PENDING:
            return InputRoute.PENDING
        if selection.mode == MicrophoneMode.SKIPPED:
            return InputRoute.SKIPPED
        return InputRoute.UNAVAILABLE

    @property
    def include_preferred(self) -> bool:
        """A fixed device that already fell back is not retried until asked."""
        return not (
            self.selection.mode == MicrophoneMode.FIXED
            and self.active_route == InputRoute.FALLBACK
        )

    @property
    def capture_running(self) -> bool:
        return self.capture is not None and bool(getattr(self.capture, "running", False))

    def clear_active_device(self) -> None:
        self.active_input_name = None
        self.active_fingerprint = None


def device_key(fingerprint: DeviceFingerprint) -> tuple[Any, ...]:
    """Identity used to tell two endpoints apart across catalog refreshes."""
    return (
        fingerprint.endpoint_id.strip().casefold(),
        fingerprint.host_api.strip().casefold(),
        fingerprint.name.strip().casefold(),
        fingerprint.default_sample_rate,
        fingerprint.max_input_channels,
    )


def build_capture(
    config: AppConfig,
    fingerprint: DeviceFingerprint | None = None,
    *,
    resolved_device: InputDevice | None = None,
    require_system_default: bool = False,
) -> WasapiMicrophone:
    if fingerprint is None:
        resolution = resolve_input_route(config.microphone)
        if not resolution.candidates:
            raise AudioDeviceNotFound(resolution.reason or "microphone is not configured")
        fingerprint = resolution.candidates[0].fingerprint
    return WasapiMicrophone(
        fingerprint,
        target_sample_rate=config.audio_sample_rate,
        block_ms=100,
        queue_blocks=32,
        resolved_device=resolved_device,
        require_system_default=require_system_default,
    )


def _candidate(device: InputDevice, route: InputRoute) -> CaptureCandidate:
    return CaptureCandidate(
        fingerprint=device.fingerprint(),
        route=route,
        catalog_name=device.name,
        resolved_device=device,
    )


def resolve_input_route(
    selection: MicrophoneSelection,
    *,
    include_preferred: bool = True,
) -> InputRouteResolution:
    """Resolve stable fingerprints without persisting PortAudio indexes."""

    preferred = selection.preferred_device
    preferred_name = preferred.name if preferred is not None else None
    if selection.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}:
        return InputRouteResolution(
            candidates=(),
            preferred_input_name=preferred_name,
            preferred_input_available=False,
            reason="microphone selection is not configured",
        )

    if selection.mode == MicrophoneMode.SYSTEM_DEFAULT:
        try:
            default = default_wasapi_input_device()
        except Exception as exc:
            return InputRouteResolution(
                candidates=(),
                preferred_input_name=None,
                preferred_input_available=False,
                reason=f"Windows default microphone is unavailable: {exc}",
            )
        return InputRouteResolution(
            candidates=(_candidate(default, InputRoute.SYSTEM_DEFAULT),),
            preferred_input_name=None,
            preferred_input_available=True,
        )

    if preferred is None:
        return InputRouteResolution(
            candidates=(),
            preferred_input_name=None,
            preferred_input_available=False,
            reason="fixed microphone selection has no fingerprint",
        )

    preferred_device: InputDevice | None = None
    preferred_error: Exception | None = None
    try:
        preferred_device = resolve_wasapi_input_device(preferred)
    except Exception as exc:
        preferred_error = exc

    candidates: list[CaptureCandidate] = []
    if include_preferred and preferred_device is not None:
        candidates.append(_candidate(preferred_device, InputRoute.PREFERRED))

    default_error: Exception | None = None
    try:
        default = default_wasapi_input_device()
    except Exception as exc:
        default_error = exc
    else:
        default_candidate = _candidate(default, InputRoute.FALLBACK)
        if not candidates or device_key(candidates[0].fingerprint) != device_key(
            default_candidate.fingerprint
        ):
            candidates.append(default_candidate)

    reason = None
    if preferred_error is not None:
        reason = f"preferred microphone is unavailable: {preferred_error}"
        if default_error is not None:
            reason += f"; Windows default is unavailable: {default_error}"
    elif not candidates and default_error is not None:
        reason = f"Windows default microphone is unavailable: {default_error}"
    return InputRouteResolution(
        candidates=tuple(candidates),
        preferred_input_name=preferred_name,
        preferred_input_available=preferred_device is not None,
        reason=reason,
    )


def synthetic_input_route(
    selection: MicrophoneSelection,
    *,
    include_preferred: bool = True,
) -> InputRouteResolution:
    """Keep private recorder-loop fakes independent from host audio hardware."""

    preferred = selection.preferred_device
    if selection.mode == MicrophoneMode.FIXED and preferred is not None:
        candidates = (
            (CaptureCandidate(preferred, InputRoute.PREFERRED, preferred.name),)
            if include_preferred
            else ()
        )
        return InputRouteResolution(
            candidates=candidates,
            preferred_input_name=preferred.name,
            preferred_input_available=True,
        )
    fingerprint = DeviceFingerprint(
        name="Test microphone",
        host_api="Windows WASAPI",
        endpoint_id="test:microphone",
        default_sample_rate=float(48_000),
        max_input_channels=1,
    )
    return InputRouteResolution(
        candidates=(
            CaptureCandidate(
                fingerprint,
                InputRoute.SYSTEM_DEFAULT,
                fingerprint.name,
            ),
        ),
        preferred_input_name=None,
        preferred_input_available=True,
    )


def call_capture_factory(
    factory: Callable[..., Any],
    fingerprint: DeviceFingerprint,
) -> Any:
    """Call an injected capture factory that may or may not want a fingerprint."""
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory()
    accepts_argument = any(
        parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }
        for parameter in signature.parameters.values()
    )
    return factory(fingerprint) if accepts_argument else factory()


class RouteCoordinator:
    """Owns `RouteState` and every transition into or out of an open capture.

    Concerns the recorder owns rather than routing — flushing buffered speech, resetting
    the stream clock, knowing whether writes are still pending — arrive as callbacks so
    this module never has to import the recorder loop back.
    """

    def __init__(
        self,
        state: RouteState,
        config: AppConfig,
        *,
        emit: EmitEvent,
        status: EmitStatus,
        capture_factory: Callable[..., Any] | None,
        route_resolver: Callable[..., InputRouteResolution] | None,
        flush_current: Callable[[], bool],
        reset_stream_state: Callable[[], None],
        is_recording: Callable[[], bool],
        has_pending_writes: Callable[[], bool],
        retry_delay: float,
    ) -> None:
        self.state = state
        self._config = config
        self._emit = emit
        self._status = status
        self._capture_factory = capture_factory
        self._route_resolver = route_resolver
        self._flush_current = flush_current
        self._reset_stream_state = reset_stream_state
        self._is_recording = is_recording
        self._has_pending_writes = has_pending_writes
        self._retry_delay = retry_delay

    # -- resolution ----------------------------------------------------------------

    def resolve_route(self, *, include_preferred: bool) -> InputRouteResolution:
        if self._route_resolver is not None:
            return self._route_resolver(self.state.selection, include_preferred=include_preferred)
        if self._capture_factory is not None:
            return synthetic_input_route(
                self.state.selection,
                include_preferred=include_preferred,
            )
        return resolve_input_route(self.state.selection, include_preferred=include_preferred)

    def preferred_name(self) -> str | None:
        device = self.state.selection.preferred_device
        return device.name if device is not None else None

    def route_reason(
        self,
        resolution: InputRouteResolution,
        route: InputRoute,
    ) -> str | None:
        if route != InputRoute.FALLBACK:
            return None
        if resolution.reason:
            return resolution.reason
        preferred = self.preferred_name()
        if self.state.selection.mode == MicrophoneMode.FIXED and preferred:
            if resolution.preferred_input_available:
                return (
                    f"using Windows default microphone; preferred microphone "
                    f"{preferred!r} is available but will not be selected automatically"
                )
            return (
                "using Windows default microphone while preferred microphone "
                f"{preferred!r} is unavailable"
            )
        return "using Windows default microphone"

    def publish_route(
        self,
        *,
        request_id: str | None = None,
        switching: bool = False,
        reason: str | None = None,
    ) -> None:
        state = self.state
        self._emit(
            InputRouteUpdate(
                request_id=request_id,
                preferred_input_name=self.preferred_name(),
                active_input_name=state.active_input_name,
                input_route=state.active_route,
                input_switching=switching,
                preferred_input_available=state.preferred_input_available,
                reason=reason if reason is not None else state.input_route_reason,
            ),
        )

    # -- opening a device ----------------------------------------------------------

    def start_candidates(
        self,
        resolution: InputRouteResolution,
    ) -> tuple[Any | None, Any | None, CaptureCandidate | None, str | None]:
        """Try each candidate in order; report the errors from the ones that failed."""
        errors: list[str] = []
        for candidate in resolution.candidates:
            next_capture: Any | None = None
            try:
                next_capture = (
                    call_capture_factory(self._capture_factory, candidate.fingerprint)
                    if self._capture_factory is not None
                    else build_capture(
                        self._config,
                        candidate.fingerprint,
                        resolved_device=candidate.resolved_device,
                        require_system_default=candidate.route
                        in {InputRoute.SYSTEM_DEFAULT, InputRoute.FALLBACK},
                    )
                )
                device = next_capture.start()
            except Exception as exc:
                errors.append(f"{candidate.catalog_name}: {exc}")
                if next_capture is not None:
                    with suppress(Exception):
                        next_capture.stop()
                continue
            return next_capture, device, candidate, "; ".join(errors) or None
        return None, None, None, "; ".join(errors) or resolution.reason

    def commit_started_capture(
        self,
        next_capture: Any,
        device: Any,
        candidate: CaptureCandidate,
        resolution: InputRouteResolution,
        *,
        request_id: str | None,
    ) -> None:
        state = self.state
        state.capture = next_capture
        state.active_route = candidate.route
        state.active_input_name = str(getattr(device, "name", candidate.catalog_name))
        state.active_fingerprint = candidate.fingerprint
        state.preferred_input_available = resolution.preferred_input_available
        state.input_route_reason = self.route_reason(resolution, candidate.route)
        self._reset_stream_state()
        self._status(
            WorkerState.RECORDING,
            f"recording from {state.active_input_name}",
            severity=(
                Severity.WARNING if candidate.route == InputRoute.FALLBACK else Severity.INFO
            ),
            metadata={
                "device_index": getattr(device, "index", None),
                "active_input_name": state.active_input_name,
                "input_route": state.active_route.value,
                "preferred_input_available": state.preferred_input_available,
            },
        )
        self.publish_route(request_id=request_id)

    def _open_and_commit(
        self,
        resolution: InputRouteResolution,
        *,
        request_id: str | None,
    ) -> tuple[bool, str | None]:
        """Open the best available candidate and adopt it.

        Returns `(committed, error)`. A fallback that only succeeded because the
        preferred device failed carries that failure forward as the route's reason.
        """
        next_capture, device, candidate, error = self.start_candidates(resolution)
        if next_capture is None or device is None or candidate is None:
            return False, error
        if candidate.route == InputRoute.FALLBACK and error:
            resolution = replace(
                resolution,
                reason=f"preferred microphone could not be opened: {error}",
            )
        self.commit_started_capture(
            next_capture,
            device,
            candidate,
            resolution,
            request_id=request_id,
        )
        return True, error

    # -- transitions ---------------------------------------------------------------

    def apply_pending_route(self, now: float) -> bool:
        """Apply the queued device change. Returns True when the loop should restart."""
        state = self.state
        request = state.pending_route_request
        if request is None or now < state.next_switch_attempt or self._has_pending_writes():
            return False
        resolution = self.resolve_route(include_preferred=state.pending_include_preferred)
        state.preferred_input_available = resolution.preferred_input_available

        if not self._is_recording():
            state.clear_active_device()
            state.active_route = (
                RouteState.idle_route(state.selection)
                if state.selection.mode in {MicrophoneMode.PENDING, MicrophoneMode.SKIPPED}
                else (
                    resolution.candidates[0].route
                    if resolution.candidates
                    else InputRoute.UNAVAILABLE
                )
            )
            state.input_route_reason = resolution.reason or "input change will apply on resume"
            state.pending_route_request = None
            self.publish_route(request_id=request.request_id)
            return True

        if not resolution.candidates:
            state.input_route_reason = resolution.reason or "microphone is unavailable"
            state.pending_route_request = None
            self._status(
                WorkerState.DEGRADED,
                state.input_route_reason,
                severity=Severity.WARNING,
                metadata={"input_route": state.active_route.value},
            )
            self.publish_route(request_id=request.request_id)
            return True

        first = resolution.candidates[0]
        if (
            state.capture_running
            and state.active_fingerprint is not None
            and device_key(state.active_fingerprint) == device_key(first.fingerprint)
        ):
            # Already on this device: adopt the new route label without reopening.
            state.active_route = first.route
            state.preferred_input_available = resolution.preferred_input_available
            state.input_route_reason = self.route_reason(resolution, first.route)
            state.pending_route_request = None
            self.publish_route(request_id=request.request_id)
            return True

        old_capture = state.capture
        old_route = state.active_route
        old_name = state.active_input_name
        old_fingerprint = state.active_fingerprint
        if old_capture is not None and getattr(old_capture, "running", False):
            if not self._flush_current():
                state.next_switch_attempt = now + self._retry_delay
                return False
            try:
                old_capture.stop()
            except Exception as exc:
                state.next_switch_attempt = now + self._retry_delay
                state.input_route_reason = f"failed to close current microphone: {exc}"
                self._status(
                    WorkerState.DEGRADED,
                    state.input_route_reason,
                    severity=Severity.WARNING,
                )
                return False

        committed, error = self._open_and_commit(resolution, request_id=request.request_id)
        if committed:
            state.pending_route_request = None
            state.next_switch_attempt = now
            return True

        restored = self._restore_previous_capture(old_capture, old_route, old_name, old_fingerprint)
        state.input_route_reason = f"microphone switch failed: {error or 'unavailable'}"
        state.pending_route_request = None
        state.next_switch_attempt = now + self._retry_delay
        self._status(
            WorkerState.DEGRADED,
            state.input_route_reason,
            severity=Severity.WARNING,
            metadata={"previous_input_restored": restored},
        )
        self.publish_route(request_id=request.request_id)
        return True

    def _restore_previous_capture(
        self,
        old_capture: Any | None,
        old_route: InputRoute,
        old_name: str | None,
        old_fingerprint: DeviceFingerprint | None,
    ) -> bool:
        """Reopen the device we just closed, so a failed switch does not lose the mic."""
        state = self.state
        state.capture = None
        if old_capture is not None:
            try:
                old_device = old_capture.start()
            except Exception:
                pass
            else:
                state.capture = old_capture
                state.active_route = old_route
                state.active_input_name = str(
                    getattr(old_device, "name", old_name or "microphone")
                )
                state.active_fingerprint = old_fingerprint
                self._reset_stream_state()
                return True
        state.active_route = InputRoute.UNAVAILABLE
        state.clear_active_device()
        return False

    def start_current_route(self, now: float) -> bool:
        """Open a capture for the current selection. Returns False to back off and retry."""
        state = self.state
        resolution = self.resolve_route(include_preferred=state.include_preferred)
        state.preferred_input_available = resolution.preferred_input_available
        first = resolution.candidates[0] if resolution.candidates else None
        if (
            state.capture is not None
            and first is not None
            and state.active_fingerprint is not None
            and device_key(first.fingerprint) == device_key(state.active_fingerprint)
        ):
            # Same device as last time: restart the capture we already hold.
            try:
                device = state.capture.start()
            except Exception:
                with suppress(Exception):
                    state.capture.stop()
                state.capture = None
            else:
                self.commit_started_capture(
                    state.capture,
                    device,
                    first,
                    resolution,
                    request_id=None,
                )
                return True

        committed, error = self._open_and_commit(resolution, request_id=None)
        if committed:
            return True
        state.input_route_reason = error or resolution.reason or "microphone is unavailable"
        state.next_start_attempt = now + self._retry_delay
        self._status(
            WorkerState.DEGRADED,
            f"microphone unavailable; retrying: {state.input_route_reason}",
            severity=Severity.WARNING,
        )
        self.publish_route(reason=state.input_route_reason)
        return False
