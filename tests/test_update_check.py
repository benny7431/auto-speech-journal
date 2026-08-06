from __future__ import annotations

import io
import json
import threading

from auto_speech_journal.update_check import ReleaseCheckService


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_update_check_is_async_opt_in_and_includes_prereleases(tmp_path) -> None:
    requested = []

    def opener(request, _timeout):
        requested.append(request.full_url)
        return _Response(
            [
                {
                    "tag_name": "v0.2.0",
                    "html_url": (
                        "https://github.com/benny7431/auto-speech-journal/releases/tag/v0.2.0"
                    ),
                    "draft": False,
                    "prerelease": True,
                },
                {
                    "tag_name": "v9.0.0",
                    "html_url": (
                        "https://github.com/benny7431/auto-speech-journal/releases/tag/v9.0.0"
                    ),
                    "draft": True,
                },
            ]
        )

    service = ReleaseCheckService(
        tmp_path / "update.json",
        "0.1.0",
        opener=opener,
        clock=lambda: 1_000.0,
    )
    completed = threading.Event()
    results = []

    started = service.check_async(
        enabled=True,
        callback=lambda result: (results.append(result), completed.set()),
    )

    assert started is True
    assert completed.wait(1.0)
    assert results[0].update_available is True
    assert results[0].latest_version == "0.2.0"
    assert results[0].release_url.endswith("/tag/v0.2.0")
    assert len(requested) == 1
    assert (tmp_path / "update.json").is_file()


def test_update_check_uses_24_hour_cache_without_network(tmp_path) -> None:
    state = tmp_path / "update.json"
    state.write_text(
        json.dumps(
            {
                "checked": True,
                "update_available": True,
                "current_version": "0.1.0",
                "latest_version": "0.2.0",
                "release_url": (
                    "https://github.com/benny7431/auto-speech-journal/releases/tag/v0.2.0"
                ),
                "error": None,
                "checked_at": 1_000.0,
            }
        ),
        encoding="utf-8",
    )
    service = ReleaseCheckService(
        state,
        "0.1.0",
        opener=lambda *_args: (_ for _ in ()).throw(AssertionError("network used")),
        clock=lambda: 1_100.0,
    )
    results = []

    started = service.check_async(enabled=True, callback=results.append)

    assert started is False
    assert results[0].latest_version == "0.2.0"


def test_disabled_update_check_does_nothing(tmp_path) -> None:
    service = ReleaseCheckService(tmp_path / "state.json", "0.1.0")
    called = []

    assert service.check_async(enabled=False, callback=called.append) is False
    assert called == []
    assert not (tmp_path / "state.json").exists()


def test_update_check_failure_is_reported_without_raising(tmp_path) -> None:
    def failing_opener(_request, _timeout):
        raise OSError("offline")

    service = ReleaseCheckService(
        tmp_path / "state.json",
        "0.1.0",
        opener=failing_opener,
        clock=lambda: 2_000.0,
    )
    completed = threading.Event()
    results = []

    service.check_async(
        enabled=True,
        callback=lambda result: (results.append(result), completed.set()),
    )

    assert completed.wait(1.0)
    assert results[0].update_available is False
    assert results[0].error == "offline"


def test_inflight_update_check_delivers_result_to_new_opt_in_callback(tmp_path) -> None:
    request_started = threading.Event()
    release_response = threading.Event()

    def opener(_request, _timeout):
        request_started.set()
        assert release_response.wait(1.0)
        return _Response(
            [
                {
                    "tag_name": "v0.2.0",
                    "html_url": (
                        "https://github.com/benny7431/auto-speech-journal/releases/tag/v0.2.0"
                    ),
                    "draft": False,
                }
            ]
        )

    service = ReleaseCheckService(tmp_path / "state.json", "0.1.0", opener=opener)
    results = []
    completed = threading.Event()

    assert service.check_async(enabled=True, callback=lambda result: results.append((1, result)))
    assert request_started.wait(1.0)
    assert (
        service.check_async(
            enabled=True,
            callback=lambda result: (results.append((2, result)), completed.set()),
        )
        is False
    )
    release_response.set()

    assert completed.wait(1.0)
    assert [generation for generation, _result in results] == [1, 2]
    assert all(result.update_available for _generation, result in results)
