"""
ctr_generator/paths.py

Resolves a stable, per-user, writable directory for data that must survive
between runs of the packaged .exe — presets and saved renames.

Path(__file__).parent is not safe for this: under PyInstaller's onefile
bundling, that resolves to a temporary extraction directory that's wiped
and re-extracted fresh on every launch, silently discarding anything saved
there in a previous session (the app would look like it "forgets" presets
or reverts to whatever was baked in at build time).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_data_dir() -> Path:
    """
    Returns a writable, per-user directory for this app's data, stable
    across restarts (and across PyInstaller onefile re-extraction).
    Creates the directory if it doesn't exist yet.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

    path = Path(base) / "SOCAR" / "CTRGenerator"
    path.mkdir(parents=True, exist_ok=True)
    return path
