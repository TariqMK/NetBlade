"""Runs blocking work on a background thread and delivers the result on the GUI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal


class _Bridge(QObject):
    done = Signal(object)
    failed = Signal(object)


_active: set[_Bridge] = set()


def run_in_thread(
    work: Callable[[], object],
    on_result: Callable[[object], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Call work() on a thread, then on_result(value) or on_error(exception) on the GUI thread."""
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
            bridge.failed.emit(error)
        else:
            bridge.done.emit(result)

    threading.Thread(target=run, daemon=True).start()
