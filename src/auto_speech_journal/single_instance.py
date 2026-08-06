from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

DEFAULT_MUTEX_NAME = r"Local\AutoSpeechJournal"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceError(RuntimeError):
    pass


class NamedMutex:
    """Cross-process singleton guard backed by a Windows named mutex."""

    def __init__(self, name: str = DEFAULT_MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None
        self._lock_file: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None or self._lock_file is not None

    def acquire(self) -> bool:
        if self.acquired:
            return True
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix_fallback()

    def _acquire_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        handle = create_mutex(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = int(handle)
        return True

    def _acquire_posix_fallback(self) -> bool:
        import fcntl

        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", self.name)
        path = Path(tempfile.gettempdir()) / f"{safe_name}.lock"
        handle = path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._lock_file = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(self._handle)
            self._handle = None
        if self._lock_file is not None:
            try:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None

    def __enter__(self) -> NamedMutex:
        if not self.acquire():
            raise SingleInstanceError("自動語音筆記已在執行")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
