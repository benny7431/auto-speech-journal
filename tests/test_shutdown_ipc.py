from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import auto_speech_journal.shutdown_ipc as shutdown_ipc
from auto_speech_journal.shutdown_ipc import ShutdownServer, request_shutdown


def test_shutdown_request_succeeds_when_application_is_not_running(tmp_path) -> None:
    result = request_shutdown(tmp_path, timeout=0.1)

    assert result.status == "not_running"
    assert result.succeeded is True
    assert not (tmp_path / "control").exists()


def test_windows_pid_probe_never_uses_os_kill(monkeypatch) -> None:
    monkeypatch.setattr(shutdown_ipc, "_WINDOWS", True)
    monkeypatch.setattr(shutdown_ipc, "_windows_pid_is_running", lambda pid: pid == 42)
    monkeypatch.setattr(
        shutdown_ipc.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("os.kill must not run on Windows")),
    )

    assert shutdown_ipc._pid_is_running(42) is True


def test_shutdown_request_waits_until_server_cleanup(tmp_path) -> None:
    queued = threading.Event()
    server = ShutdownServer(
        tmp_path,
        lambda: queued.set() or True,
        poll_interval=0.01,
    ).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                request_shutdown,
                tmp_path,
                timeout=2.0,
                poll_interval=0.01,
            )
            assert queued.wait(1.0)
            assert future.done() is False
            server.stop()
            result = future.result(timeout=1.0)
    finally:
        server.stop()

    assert result.status == "stopped"
    assert result.succeeded is True


def test_shutdown_server_discards_request_left_by_previous_process(tmp_path) -> None:
    control = tmp_path / "control"
    control.mkdir(parents=True)
    request = control / "shutdown.request.json"
    request.write_text(
        json.dumps(
            {
                "protocol": 1,
                "nonce": "a" * 32,
                "created_at": time.time(),
                "requester_pid": 123,
            }
        ),
        encoding="utf-8",
    )
    called = threading.Event()
    server = ShutdownServer(tmp_path, lambda: called.set() or True, poll_interval=0.01).start()
    try:
        time.sleep(0.05)
    finally:
        server.stop()

    assert called.is_set() is False


def test_shutdown_rejection_never_reports_success(tmp_path) -> None:
    server = ShutdownServer(tmp_path, lambda: False, poll_interval=0.01).start()
    try:
        result = request_shutdown(tmp_path, timeout=1.0, poll_interval=0.01)
    finally:
        server.stop()

    assert result.status == "rejected"
    assert result.succeeded is False


def test_shutdown_times_out_if_process_does_not_finish_cleanup(tmp_path) -> None:
    server = ShutdownServer(tmp_path, lambda: True, poll_interval=0.01).start()
    try:
        result = request_shutdown(tmp_path, timeout=0.08, poll_interval=0.01)
    finally:
        server.stop()

    assert result.status == "timed_out"
    assert result.succeeded is False
