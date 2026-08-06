from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_WINDOWS = os.name == "nt"


class ShutdownIpcError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    status: str
    detail: str
    nonce: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {"stopped", "not_running"}


Clock = Callable[[], float]
ShutdownHandler = Callable[[], bool | None]
PidChecker = Callable[[int], bool]


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _windows_pid_is_running(pid: int) -> bool:
    """Query a Windows process without using os.kill(pid, 0).

    CPython maps ordinary ``os.kill`` signals to ``TerminateProcess`` on Windows,
    so the POSIX liveness idiom would destroy the recorder before graceful IPC.
    """

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, wintypes.LPDWORD)
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if _WINDOWS:
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _control_paths(runtime_root: Path) -> tuple[Path, Path, Path]:
    control = runtime_root / "control"
    return control, control / "shutdown.server.json", control / "shutdown.request.json"


def queue_qt_quit() -> bool:
    """Queue QApplication.quit on the Qt thread without stopping workers from this thread."""
    try:
        from PySide6.QtCore import QCoreApplication, QMetaObject, Qt
    except ImportError:
        return False
    application = QCoreApplication.instance()
    if application is None:
        return False
    return bool(
        QMetaObject.invokeMethod(
            application,
            "quit",
            Qt.ConnectionType.QueuedConnection,
        )
    )


class ShutdownServer:
    def __init__(
        self,
        runtime_root: Path,
        handler: ShutdownHandler,
        *,
        poll_interval: float = 0.1,
        request_ttl: float = 60.0,
        clock: Clock = time.time,
    ) -> None:
        self.runtime_root = runtime_root
        self.handler = handler
        self.poll_interval = poll_interval
        self.request_ttl = request_ttl
        self.clock = clock
        self.control_dir, self.server_path, self.request_path = _control_paths(runtime_root)
        self.instance_id = uuid.uuid4().hex
        self.started_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled: set[str] = set()

    def start(self) -> ShutdownServer:
        if self._thread is not None:
            return self
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.request_path.unlink(missing_ok=True)
        self.started_at = self.clock()
        _atomic_write_json(
            self.server_path,
            {
                "protocol": PROTOCOL_VERSION,
                "instance": self.instance_id,
                "pid": os.getpid(),
                "started_at": self.started_at,
            },
        )
        self._thread = threading.Thread(
            target=self._serve,
            name="asj-shutdown-ipc",
            daemon=True,
        )
        self._thread.start()
        return self

    def _ack_path(self, nonce: str) -> Path:
        return self.control_dir / f"shutdown.ack.{nonce}.json"

    def _write_ack(self, nonce: str, status: str, detail: str) -> None:
        _atomic_write_json(
            self._ack_path(nonce),
            {
                "protocol": PROTOCOL_VERSION,
                "instance": self.instance_id,
                "nonce": nonce,
                "status": status,
                "detail": detail,
                "acknowledged_at": self.clock(),
            },
        )

    def _handle_request(self, request: dict[str, Any]) -> None:
        nonce = request.get("nonce")
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            return
        if nonce in self._handled:
            return
        self._handled.add(nonce)
        created_at = request.get("created_at")
        if not isinstance(created_at, int | float):
            self._write_ack(nonce, "rejected", "request has no valid timestamp")
            return
        now = self.clock()
        if request.get("protocol") != PROTOCOL_VERSION:
            self._write_ack(nonce, "rejected", "unsupported shutdown protocol")
            return
        if created_at < self.started_at - 1 or created_at > now + 5:
            self._write_ack(nonce, "rejected", "stale or future shutdown request")
            return
        if now - created_at > self.request_ttl:
            self._write_ack(nonce, "rejected", "shutdown request expired")
            return
        try:
            accepted = self.handler()
        except Exception as error:
            self._write_ack(nonce, "rejected", f"shutdown handler failed: {error}")
            return
        if accepted is False:
            self._write_ack(nonce, "rejected", "application could not queue a graceful quit")
            return
        self._write_ack(nonce, "accepted", "graceful quit queued")

    def _serve(self) -> None:
        while not self._stop.wait(self.poll_interval):
            request = _read_json(self.request_path)
            if request is not None:
                self._handle_request(request)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_interval * 4))
            self._thread = None
        current = _read_json(self.server_path)
        if current is not None and current.get("instance") == self.instance_id:
            self.server_path.unlink(missing_ok=True)
        request = _read_json(self.request_path)
        if request is not None and request.get("nonce") in self._handled:
            self.request_path.unlink(missing_ok=True)

    def __enter__(self) -> ShutdownServer:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.stop()


def request_shutdown(
    runtime_root: Path,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
    clock: Clock = time.time,
    monotonic: Clock = time.monotonic,
    pid_checker: PidChecker = _pid_is_running,
) -> ShutdownResult:
    if timeout <= 0:
        raise ValueError("shutdown timeout must be positive")
    control_dir, server_path, request_path = _control_paths(runtime_root)
    server = _read_json(server_path)
    if server is None:
        return ShutdownResult("not_running", "no running application announced shutdown IPC")
    pid = server.get("pid")
    instance = server.get("instance")
    if not isinstance(pid, int) or not isinstance(instance, str) or not pid_checker(pid):
        current = _read_json(server_path)
        if current is not None and current.get("instance") == instance:
            server_path.unlink(missing_ok=True)
        return ShutdownResult("not_running", "removed stale shutdown IPC metadata")

    nonce = uuid.uuid4().hex
    ack_path = control_dir / f"shutdown.ack.{nonce}.json"
    ack_path.unlink(missing_ok=True)
    _atomic_write_json(
        request_path,
        {
            "protocol": PROTOCOL_VERSION,
            "nonce": nonce,
            "created_at": clock(),
            "requester_pid": os.getpid(),
        },
    )
    deadline = monotonic() + timeout
    accepted = False
    detail = "application did not acknowledge the shutdown request"
    try:
        while monotonic() < deadline:
            ack = _read_json(ack_path)
            if ack is not None and ack.get("instance") == instance and ack.get("nonce") == nonce:
                status = ack.get("status")
                detail = str(ack.get("detail", detail))
                if status == "rejected":
                    return ShutdownResult("rejected", detail, nonce)
                accepted = status == "accepted"
            current = _read_json(server_path)
            process_running = pid_checker(pid)
            if accepted and (
                current is None or current.get("instance") != instance or not process_running
            ):
                return ShutdownResult("stopped", "application stopped gracefully", nonce)
            time.sleep(min(poll_interval, max(deadline - monotonic(), 0)))
        status = "timed_out" if accepted else "unresponsive"
        return ShutdownResult(status, detail, nonce)
    finally:
        ack_path.unlink(missing_ok=True)
        current_request = _read_json(request_path)
        if current_request is not None and current_request.get("nonce") == nonce:
            request_path.unlink(missing_ok=True)


__all__ = [
    "PROTOCOL_VERSION",
    "ShutdownIpcError",
    "ShutdownResult",
    "ShutdownServer",
    "queue_qt_quit",
    "request_shutdown",
]
