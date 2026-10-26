"""Cross-process lock shared by AstrBot and DSH adapters.

The AstrBot plugin and the DSH MCP server are separate processes.  Both may
receive a GPU task at the same time, so the orchestrator must serialize actual
service transitions through the same lock file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import IO


class FileLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: IO[bytes] | None = None

    def acquire(self, timeout: float = 600.0) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(0.1)

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass
