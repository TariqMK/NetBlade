"""Checks that NetBlade exits cleanly: every widget is destroyed while Qt still runs.

Regression test: a widget kept alive only by a reference loop (the Notepad's Copy all button is connected
to a lambda that uses the page) was freed during Python's final cleanup, after the QApplication was gone.
On Linux that crashed the process ("shared QObject was deleted directly") after all tests had passed.
The crash happens at process exit, so each check runs in its own Python process.
"""

import os
import subprocess
import sys

SCRIPT = """
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from netblade.app import release_qt_objects
from netblade.ui.notepad_page import NotepadPage

app = QApplication([])
NotepadPage(Path(tempfile.mkdtemp()) / "notes.txt")  # Not kept: only its reference loop holds it.
release_qt_objects()
print("top-level widgets left:", len(QApplication.topLevelWidgets()))
"""


def run_script(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,  # The return code is checked by the tests themselves.
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_widgets_are_released_and_process_exits_cleanly():
    # The crash was intermittent, so run several times.
    for _ in range(5):
        result = run_script(SCRIPT)
        assert result.returncode == 0, result.stderr[-2000:]
        assert "top-level widgets left: 0" in result.stdout
        assert "shared QObject was deleted directly" not in result.stderr


def test_self_test_exits_cleanly():
    """The release workflow runs the self-test on each platform; it must not crash on the way out."""
    for _ in range(3):
        result = run_script(
            "import sys; from netblade.app import main; sys.exit(main(['netblade', '--self-test']))"
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.startswith("NetBlade v")
