"""Runs blocking work on a background thread and delivers the result on the GUI thread.

Call shutdown() when the app quits. After that, tasks that are still running drop their results
instead of reporting them, because Qt may already be gone.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal


class _Bridge(QObject):
    done = Signal(object)
    failed = Signal(object)


_active: set[_Bridge] = set()
# Makes "check closing, then report" one step, so shutdown can't fall in between.
_lock = threading.Lock()
_closing = False


def shutdown() -> None:
    """Stop all reporting. Results of tasks that are still running are dropped.

    Without this, a task that finishes after Qt is shut down reports into a deleted Qt object,
    which raises an error on Windows and crashes the process on Linux.
    """
    global _closing
    with _lock:
        _closing = True


atexit.register(shutdown)  # Safety net; the app and the tests also call it explicitly.


def report(signal, *values: object) -> None:
    """Send a signal from a worker thread, unless the app has shut down. Use this for every cross-thread signal."""
    with _lock:
        if not _closing:
            signal.emit(*values)


def run_in_thread(
    work: Callable[[], object],
    on_result: Callable[[object], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Call work() on a thread, then on_result(value) or on_error(exception) on the GUI thread."""
    if _closing:
        return
    bridge = _Bridge()  # Created on the GUI thread, so its signals are delivered there.
    _active.add(bridge)

    def finish(callback: Callable, value: object) -> None:
        _active.discard(bridge)
        callback(value)

    bridge.done.connect(lambda value: finish(on_result, value))
    bridge.failed.connect(lambda error: finish(on_error, error))

    def run() -> None:
        try:
            result = work()
        except Exception as error:  # noqa: BLE001 - every failure is shown to the user.
            report(bridge.failed, error)
        else:
            report(bridge.done, result)

    threading.Thread(target=run, daemon=True).start()
