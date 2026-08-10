from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from .atomic_io import write_json_atomic

DEFAULT_REPOSITORY = "benny7431/auto-speech-journal"
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
LOGGER = logging.getLogger("auto_speech_journal.update_check")


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    checked: bool
    update_available: bool
    current_version: str
    latest_version: str | None
    release_url: str | None
    error: str | None
    checked_at: float


class Response(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...


UrlOpener = Callable[[urllib.request.Request, float], Response]
ResultCallback = Callable[[UpdateCheckResult], None]


def _default_opener(request: urllib.request.Request, timeout: float) -> Response:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[return-value]


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _safe_release_url(value: object, repository: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    expected_prefix = f"/{repository.casefold()}/releases/"
    if (
        parsed.scheme.casefold() == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and parsed.path.casefold().startswith(expected_prefix)
    ):
        return value
    return None


def _result_from_dict(raw: object) -> UpdateCheckResult | None:
    if not isinstance(raw, dict):
        return None
    try:
        return UpdateCheckResult(
            checked=bool(raw["checked"]),
            update_available=bool(raw["update_available"]),
            current_version=str(raw["current_version"]),
            latest_version=(
                None if raw.get("latest_version") is None else str(raw["latest_version"])
            ),
            release_url=None if raw.get("release_url") is None else str(raw["release_url"]),
            error=None if raw.get("error") is None else str(raw["error"]),
            checked_at=float(raw["checked_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class ReleaseCheckService:
    """Opt-in, throttled release notice service. It never downloads release assets."""

    def __init__(
        self,
        state_file: Path,
        current_version: str,
        *,
        repository: str = DEFAULT_REPOSITORY,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        opener: UrlOpener = _default_opener,
        clock: Callable[[], float] = time.time,
        timeout: float = 10.0,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must use owner/name syntax")
        if _parse_version(current_version) is None:
            raise ValueError(f"current_version is not semantic: {current_version!r}")
        self.state_file = state_file
        self.current_version = current_version.lstrip("v")
        self.repository = repository
        self.interval_seconds = interval_seconds
        self._opener = opener
        self._clock = clock
        self.timeout = timeout
        self._lock = threading.Lock()
        self._inflight = False
        self._callbacks: list[ResultCallback] = []
        self._last_result = self._load_state()

    @property
    def last_result(self) -> UpdateCheckResult | None:
        with self._lock:
            return self._last_result

    def _load_state(self) -> UpdateCheckResult | None:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        result = _result_from_dict(raw)
        if result is None or result.current_version != self.current_version:
            return None
        if result.release_url is not None and (
            _safe_release_url(result.release_url, self.repository) is None
        ):
            return None
        return result

    def _save_state(self, result: UpdateCheckResult) -> None:
        try:
            write_json_atomic(self.state_file, asdict(result), ensure_ascii=True)
        except OSError:
            LOGGER.exception("Unable to persist update-check throttle state")

    def _fetch(self) -> UpdateCheckResult:
        checked_at = self._clock()
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/releases?per_page=20",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"AutoSpeechJournal/{self.current_version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("GitHub release response is not an array")
            candidates: list[tuple[tuple[int, int, int], str, str]] = []
            for release in payload:
                if not isinstance(release, dict) or release.get("draft") is True:
                    continue
                tag = release.get("tag_name")
                parsed = _parse_version(tag) if isinstance(tag, str) else None
                url = _safe_release_url(release.get("html_url"), self.repository)
                if parsed is not None and url is not None:
                    candidates.append((parsed, tag.lstrip("v"), url))
            if not candidates:
                raise ValueError("GitHub returned no usable releases")
            _parsed, latest, release_url = max(candidates, key=lambda item: item[0])
            available = _parse_version(latest) > _parse_version(self.current_version)  # type: ignore[operator]
            return UpdateCheckResult(
                checked=True,
                update_available=available,
                current_version=self.current_version,
                latest_version=latest,
                release_url=release_url if available else None,
                error=None,
                checked_at=checked_at,
            )
        except (OSError, ValueError, UnicodeError, urllib.error.URLError) as error:
            return UpdateCheckResult(
                checked=True,
                update_available=False,
                current_version=self.current_version,
                latest_version=None,
                release_url=None,
                error=str(error),
                checked_at=checked_at,
            )

    def _run(self) -> None:
        try:
            result = self._fetch()
            with self._lock:
                self._last_result = result
                callbacks = tuple(self._callbacks)
                self._callbacks.clear()
                self._inflight = False
            self._save_state(result)
            if result.error:
                LOGGER.info("Update check unavailable: %s", result.error)
            elif result.update_available:
                LOGGER.info(
                    "Update %s is available at %s",
                    result.latest_version,
                    result.release_url,
                )
            for callback in callbacks:
                try:
                    callback(result)
                except Exception:
                    LOGGER.exception("Update-check result callback failed")
        except Exception:
            # _fetch is deliberately non-raising, but never leave the single-flight
            # gate or callbacks stuck if a future implementation regresses.
            with self._lock:
                self._callbacks.clear()
                self._inflight = False
            LOGGER.exception("Unexpected update-check service failure")

    def check_async(self, *, enabled: bool, callback: ResultCallback) -> bool:
        if not enabled:
            return False
        now = self._clock()
        with self._lock:
            cached = self._last_result
            if cached is not None and now - cached.checked_at < self.interval_seconds:
                should_use_cache = True
            else:
                should_use_cache = False
            if should_use_cache:
                pass
            elif self._inflight:
                self._callbacks.append(callback)
                return False
            else:
                self._inflight = True
                self._callbacks.append(callback)
        if should_use_cache:
            callback(cached)  # type: ignore[arg-type]
            return False
        thread = threading.Thread(
            target=self._run,
            name="asj-update-check",
            daemon=True,
        )
        thread.start()
        return True

    def open_release_page(self, result: UpdateCheckResult | None = None) -> bool:
        selected = result or self.last_result
        if selected is None or not selected.update_available or selected.release_url is None:
            return False
        url = _safe_release_url(selected.release_url, self.repository)
        return False if url is None else bool(webbrowser.open(url, new=2))


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_REPOSITORY",
    "ReleaseCheckService",
    "UpdateCheckResult",
]
