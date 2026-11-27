"""Where Squire keeps its own files (notification state, remembered lighting, key presets, the device lock fallback)."""
import os
import sys
from pathlib import Path

IS_LINUX = sys.platform.startswith("linux")


def state_dir() -> Path:
    if IS_LINUX:
        return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "squire-gear"
    return Path.home() / "Library" / "Application Support" / "squire-gear"
