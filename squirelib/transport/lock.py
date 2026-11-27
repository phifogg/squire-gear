"""device_lock: one device session at a time across Squire processes (same flock file as the Swift DeviceLock)."""
import os
from pathlib import Path
from typing import Optional

from ..state import state_dir


class device_lock:
    """Serialises device sessions between Squire processes (tray, settings window, CLI) for this user.

    Each process gets its own copy of every reply, so this is about keeping request/reply pairs together,
    not about safety. Best effort: if the lock file cannot be created, run without it."""

    def __init__(self, path: Optional[Path] = None):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        self.path = path or (Path(runtime) if runtime else state_dir()) / "squire-gear-device.lock"
        self.handle = None

    def __enter__(self):
        try:
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "w")
            fcntl.flock(self.handle, fcntl.LOCK_EX)   # waits; a session takes about a second at most
        except (ImportError, OSError):
            self.handle = None
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            self.handle.close()   # closing releases the lock
        return False
