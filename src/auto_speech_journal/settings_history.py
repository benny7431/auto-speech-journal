from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SettingsHistoryEntry:
    timestamp_utc: datetime
    changed_fields: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        timestamp = self.timestamp_utc.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {
            "timestamp_utc": timestamp,
            "changed_fields": list(self.changed_fields),
            "before": self.before,
            "after": self.after,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SettingsHistoryEntry:
        timestamp_raw = raw.get("timestamp_utc")
        fields_raw = raw.get("changed_fields")
        before_raw = raw.get("before")
        after_raw = raw.get("after")
        if not isinstance(timestamp_raw, str):
            raise ValueError("settings history timestamp is missing")
        if not isinstance(fields_raw, list) or not all(
            isinstance(field, str) and field for field in fields_raw
        ):
            raise ValueError("settings history changed_fields is invalid")
        if not isinstance(before_raw, dict) or not isinstance(after_raw, dict):
            raise ValueError("settings history before/after values are invalid")
        timestamp = datetime.fromisoformat(timestamp_raw)
        if timestamp.tzinfo is None:
            raise ValueError("settings history timestamp must include a timezone")
        fields = tuple(fields_raw)
        if set(fields) != set(before_raw) or set(fields) != set(after_raw):
            raise ValueError("settings history values do not match changed_fields")
        return cls(
            timestamp_utc=timestamp.astimezone(UTC),
            changed_fields=fields,
            before=dict(before_raw),
            after=dict(after_raw),
        )


class SettingsHistoryStore:
    """Append-only, durable settings audit trail stored as UTF-8 JSONL."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def append_change(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> SettingsHistoryEntry | None:
        changed = _changed_values(before, after)
        if not changed:
            return None
        timestamp = self._clock()
        if timestamp.tzinfo is None:
            raise ValueError("settings history clock must return a timezone-aware datetime")
        fields = tuple(sorted(changed))
        entry = SettingsHistoryEntry(
            timestamp_utc=timestamp.astimezone(UTC),
            changed_fields=fields,
            before={field: changed[field][0] for field in fields},
            after={field: changed[field][1] for field in fields},
        )
        line = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def read_recent(self, limit: int = 5) -> tuple[SettingsHistoryEntry, ...]:
        if limit <= 0 or not self.path.is_file():
            return ()
        valid: list[SettingsHistoryEntry] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            continue
                        valid.append(SettingsHistoryEntry.from_dict(raw))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
        except OSError:
            return ()
        return tuple(reversed(valid[-limit:]))

    def ensure_file(self) -> Path:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        return self.path


def _changed_values(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    prefix: str = "",
) -> dict[str, tuple[Any, Any]]:
    changed: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            changed.update(_changed_values(old, new, path))
        elif old != new:
            changed[path] = (old, new)
    return changed
