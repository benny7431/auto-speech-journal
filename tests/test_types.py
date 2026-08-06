from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_speech_journal.config import DeviceFingerprint, MicrophoneMode, MicrophoneSelection
from auto_speech_journal.types import (
    AudioLevelUpdate,
    CapturedSegment,
    InputRoute,
    InputRouteRequest,
    InputRouteUpdate,
    PartialUpdate,
)


def test_captured_segment_requires_ordered_aware_timestamps():
    started = datetime(2026, 7, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="precedes"):
        CapturedSegment(
            segment_id="segment",
            audio_path=Path("segment.flac"),
            started_at_utc=started,
            ended_at_utc=started - timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        PartialUpdate(
            segment_id="segment",
            text="測試",
            started_at_utc=datetime(2026, 7, 12),
        )


def test_captured_segment_accepts_valid_utc_range():
    started = datetime(2026, 7, 12, tzinfo=UTC)
    segment = CapturedSegment(
        segment_id="segment",
        audio_path=Path("segment.flac"),
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=2),
        duration_ms=2_000,
    )

    assert segment.duration_ms == 2_000


def test_audio_level_update_normalizes_timestamp_and_values() -> None:
    measured = datetime(2026, 7, 12, 8, tzinfo=timezone(timedelta(hours=8)))

    update = AudioLevelUpdate(
        rms_dbfs=-24,
        peak_dbfs=-6,
        speech_active=True,
        segment_id="segment",
        measured_at_utc=measured,
    )

    assert update.rms_dbfs == -24.0
    assert update.peak_dbfs == -6.0
    assert update.measured_at_utc == datetime(2026, 7, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rms_dbfs": -121}, "dBFS"),
        ({"peak_dbfs": 0.1}, "dBFS"),
        ({"rms_dbfs": float("nan")}, "finite"),
        ({"rms_dbfs": -3, "peak_dbfs": -12}, "cannot exceed"),
        ({"speech_active": 1}, "boolean"),
        ({"segment_id": "  "}, "segment_id"),
        ({"measured_at_utc": datetime(2026, 7, 12)}, "timezone-aware"),
    ],
)
def test_audio_level_update_rejects_invalid_contract(changes: dict, message: str) -> None:
    values = {
        "rms_dbfs": -20,
        "peak_dbfs": -3,
        "speech_active": False,
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        AudioLevelUpdate(**values)


def test_input_route_request_requires_id_and_valid_selection_shape() -> None:
    fixed = MicrophoneSelection(
        mode=MicrophoneMode.FIXED,
        preferred_device=DeviceFingerprint(name="USB microphone"),
    )

    assert InputRouteRequest("request-1", fixed).selection == fixed
    with pytest.raises(ValueError, match="request_id"):
        InputRouteRequest("  ", fixed)

    invalid = MicrophoneSelection(
        mode=MicrophoneMode.SYSTEM_DEFAULT,
        preferred_device=DeviceFingerprint(name="not allowed"),
    )
    with pytest.raises(ValueError, match="fixed microphone"):
        InputRouteRequest("request-2", invalid)


def test_input_route_update_normalizes_timestamp() -> None:
    emitted = datetime(2026, 7, 12, 8, tzinfo=timezone(timedelta(hours=8)))

    update = InputRouteUpdate(
        request_id="route-1",
        preferred_input_name="Preferred",
        active_input_name="Fallback",
        input_route=InputRoute.FALLBACK,
        preferred_input_available=True,
        emitted_at_utc=emitted,
    )

    assert update.emitted_at_utc == datetime(2026, 7, 12, tzinfo=UTC)
