"""Finds NetBlade's data folder: NetBlade-data beside the app, so the app and its data move together."""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

FOLDER_NAME = "NetBlade-data"
SETTINGS_FILE = "settings.ini"
NOTES_FILE = "notes.txt"
VENDOR_FOLDER = "oui"


def app_location() -> Path:
    """The folder that holds the app: beside the .exe or binary, beside the .app on macOS.

    A packaged build uses sys.executable, not __file__, because a one-file build unpacks its code
    to a temporary folder. Run from source, this is the project root.
    """
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent.parent
        return executable.parent
    return Path(__file__).resolve().parent.parent


def default_folder() -> Path:
    return app_location() / FOLDER_NAME


def can_write(folder: Path) -> bool:
    """True if NetBlade can save in `folder`. The check leaves nothing behind.

    The folder itself is created only when something is saved, so a folder that doesn't exist yet
    is checked through its nearest existing parent.
    """
    target = folder
    while not target.exists() and target != target.parent:
        target = target.parent
    if not target.is_dir():
        return False
    try:
        with tempfile.TemporaryFile(dir=target):
            pass
    except OSError:
        return False
    return True


def session_folder() -> Path:
    """A temporary data folder for one session. It is deleted when NetBlade closes."""
    folder = Path(tempfile.mkdtemp(prefix="netblade-data-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return folder
