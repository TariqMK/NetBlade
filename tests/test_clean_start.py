"""Checks that no stray window opens while the main window is built. Such a window flashes on screen at startup."""

import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from netblade.app import MainWindow
from netblade.ui.ipconfig_page import IpConfigPage


class WindowWatcher(QObject):
    """Records each widget that opens as its own window."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[str] = []

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            self.opened.append(type(watched).__name__)
        return False


def windows_opened_by(build) -> list[str]:
    watcher = WindowWatcher()
    app = QApplication.instance()
    app.installEventFilter(watcher)
    try:
        build()
    finally:
        app.removeEventFilter(watcher)
    return watcher.opened


def test_building_the_main_window_opens_no_window(settings):
    assert windows_opened_by(lambda: MainWindow(settings)) == []


def test_ip_config_page_opens_no_window_on_windows(qapp, monkeypatch):
    """The page shows its adapter controls only on Windows, so check that case on every platform."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert windows_opened_by(IpConfigPage) == []


def test_ip_config_controls_stay_on_the_page(qapp, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    page = IpConfigPage()
    assert page.show_disconnected.isVisibleTo(page)
    assert page.view_group.button(0).parentWidget().isVisibleTo(page)
    assert not page.show_disconnected.isWindow()
    assert not page.view_group.button(0).parentWidget().isWindow()
