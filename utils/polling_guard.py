"""Process-wide guard for the local Telegram long-polling lifecycle."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import BinaryIO


class PollingInstanceAlreadyRunning(RuntimeError):
    """Raised before polling when another local process owns the guard."""


class PollingInstanceGuard:
    """Hold an OS-released file lock for exactly one polling process.

    The lock file itself may remain after a crash.  Only the operating-system
    lock matters, and the OS releases it with the process handle.
    """

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or (
            Path(tempfile.gettempdir()) / "fitness-coach-telegram-polling.lock"
        )
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "PollingInstanceGuard":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def acquire(self) -> "PollingInstanceGuard":
        if self._handle is not None:
            return self

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+b")
        try:
            self._prepare_lock_byte(handle)
            self._lock(handle)
        except OSError as error:
            handle.close()
            raise PollingInstanceAlreadyRunning(
                "Another local bot polling process is already running."
            ) from error

        self._handle = handle
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            handle.close()

    @staticmethod
    def _prepare_lock_byte(handle: BinaryIO) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
