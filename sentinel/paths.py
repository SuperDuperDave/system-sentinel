"""Where the tool keeps its own files: the token, the stack, the prompts, captures.

One directory, chosen once. Windows: ``%LOCALAPPDATA%\\SystemSentinel``. Anywhere
else (development from WSL): ``~/.system-sentinel``. ``SYSTEM_SENTINEL_HOME``
overrides both, which is how tests keep their state out of the real directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("SYSTEM_SENTINEL_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "SystemSentinel"
    else:
        base = Path.home() / ".system-sentinel"
    base.mkdir(parents=True, exist_ok=True)
    return base


def captures_dir() -> Path:
    d = data_dir() / "captures"
    d.mkdir(parents=True, exist_ok=True)
    return d
