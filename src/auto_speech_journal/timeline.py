from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .types import SegmentState


@dataclass(frozen=True, slots=True)
class TimelineSegmentView:
    segment_id: str
    time_label: str
    text: str
    status_label: str
    editable: bool
    hour_key: str
    state: SegmentState
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineHourView:
    hour_key: str
    label: str
    segments: tuple[TimelineSegmentView, ...]


@dataclass(frozen=True, slots=True)
class DayTimelineView:
    day_key: str
    hours: tuple[TimelineHourView, ...]


_FINAL_STATES = frozenset(
    {
        SegmentState.FINAL_READY,
        SegmentState.EXPORTED,
        SegmentState.AUDIO_DELETED,
    }
)


def build_day_timeline(
    day_key: str,
    records: Sequence[Any],
    *,
    timezone_name: str = "Asia/Taipei",
) -> DayTimelineView:
    """Create the immutable UI read-model from durable storage records."""

    timezone = ZoneInfo(timezone_name)
    grouped: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        hour_key = str(getattr(record, "hour_key", ""))
        state = _segment_state(getattr(record, "state", SegmentState.CAPTURED))
        display_text = str(getattr(record, "display_text", "") or "")
        if (
            hour_key.startswith(f"{day_key}_")
            and (display_text or state not in _FINAL_STATES)
        ):
            grouped[hour_key].append(record)

    hours = tuple(
        TimelineHourView(
            hour_key=key,
            label=f"{key.rsplit('_', 1)[-1]}:00",
            segments=tuple(
                _segment_view(record, timezone)
                for record in sorted(grouped[key], key=_record_sort_key)
            ),
        )
        for key in sorted(grouped)
    )
    return DayTimelineView(day_key=day_key, hours=hours)


def _record_sort_key(record: Any) -> tuple[datetime, str]:
    started_at = getattr(record, "started_at_utc", None)
    if not isinstance(started_at, datetime):
        started_at = datetime.max.replace(tzinfo=UTC)
    elif started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    else:
        started_at = started_at.astimezone(UTC)
    return started_at, str(getattr(record, "segment_id", ""))


def _segment_view(record: Any, timezone: ZoneInfo) -> TimelineSegmentView:
    state = _segment_state(getattr(record, "state", SegmentState.CAPTURED))
    hour_key = str(getattr(record, "hour_key", ""))
    text = str(getattr(record, "display_text", "") or "")
    user_locked = bool(getattr(record, "user_locked", False))
    final_text = str(getattr(record, "final_text", "") or "")

    if user_locked:
        status_label = "已修正"
    elif state == SegmentState.RETRY:
        status_label = "重試中"
    elif state == SegmentState.FAILED:
        status_label = "失敗"
    elif final_text or state in _FINAL_STATES:
        status_label = "已定稿"
    else:
        status_label = "待定稿"

    started_at = getattr(record, "started_at_utc", None)
    if isinstance(started_at, datetime) and started_at.tzinfo is not None:
        time_text = started_at.astimezone(timezone).strftime("%H:%M:%S")
    else:
        hour = hour_key.rsplit("_", 1)[-1] if "_" in hour_key else "00"
        time_text = f"{hour}:00:00"

    return TimelineSegmentView(
        segment_id=str(getattr(record, "segment_id", "")),
        time_label=f"[{time_text}]",
        text=text,
        status_label=status_label,
        editable=bool(text.strip()),
        hour_key=hour_key,
        state=state,
        last_error=getattr(record, "last_error", None),
    )


def _segment_state(value: object) -> SegmentState:
    if isinstance(value, SegmentState):
        return value
    try:
        return SegmentState(str(value))
    except ValueError:
        return SegmentState.CAPTURED
