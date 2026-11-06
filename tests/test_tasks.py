"""Checks that background tasks never report back after shutdown.

Regression test: a task that finished after the app (or the test run) had shut Qt down reported its result
into a deleted Qt object. On Windows that raised "Signal source has been deleted"; on Linux it crashed the
process with a segmentation fault after all tests had passed.
"""

import threading

import pytest

from netblade.ui import tasks


@pytest.fixture
def thread_errors():
    """Collect exceptions raised inside background threads, which pytest would otherwise miss."""
    errors = []
    previous = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    yield errors
    threading.excepthook = previous


def test_task_finishing_after_shutdown_does_not_report(
    qapp, thread_errors, monkeypatch
):
    # monkeypatch restores _closing after the test, so later tests still work.
    monkeypatch.setattr(tasks, "_closing", False)
    release = threading.Event()
    started = threading.Event()
    delivered = []

    def slow_work():
        started.set()
        release.wait(5)
        return "late result"

    tasks.run_in_thread(slow_work, delivered.append, delivered.append)
    assert started.wait(5)
    worker = next(
        t
        for t in threading.enumerate()
        if t is not threading.main_thread() and t.is_alive()
    )

    tasks.shutdown()  # As when the app quits or the test run ends.
    release.set()  # The task finishes only now, after shutdown.
    worker.join(5)
    qapp.processEvents()

    assert delivered == []
    assert thread_errors == []


def test_no_new_tasks_after_shutdown(qapp, monkeypatch):
    monkeypatch.setattr(tasks, "_closing", False)
    tasks.shutdown()
    ran = threading.Event()
    tasks.run_in_thread(ran.set, lambda _r: None, lambda _e: None)
    assert not ran.wait(0.3)


def test_tasks_still_deliver_normally(qapp):
    delivered = []
    tasks.run_in_thread(lambda: 42, delivered.append, delivered.append)
    for _ in range(100):
        qapp.processEvents()
        if delivered:
            break
        threading.Event().wait(0.02)
    assert delivered == [42]
