"""Project-relative paths (no third-party imports, so tests run without deps).

Two base folders, which are the same in a normal checkout:
- APP_DIR:      writable files (.env, accounts.db, data/). In a PyInstaller
                build this is the folder that contains the executable.
- RESOURCE_DIR: read-only files shipped with the app (.env.example,
                lexicons/). In a PyInstaller build this is the temporary
                extraction folder (sys._MEIPASS).
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
_REPO_ROOT = Path(__file__).resolve().parents[1]

APP_DIR = Path(sys.executable).resolve().parent if FROZEN else _REPO_ROOT
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _REPO_ROOT))

PROJECT_ROOT = APP_DIR  # backwards-compatible name


def project_path(value: str) -> Path:
    """Writable location: relative paths are relative to APP_DIR, not the current directory."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def resource_path(value: str) -> Path:
    """Read-only file: a user copy in APP_DIR wins, otherwise the bundled one."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    user_copy = APP_DIR / path
    return user_copy if user_copy.exists() else RESOURCE_DIR / path
