"""Shared test setup: runs Qt without a display, provides one QApplication and keeps settings out of the registry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication

from netblade.ui import tasks


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    # Tests must never read or change the user's real NetBlade settings, such as the sidebar order.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    # User folders (notes, vendor list) go to Qt's separate test location, never the real ones.
    QStandardPaths.setTestModeEnabled(True)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path_factory.mktemp("settings")),
    )
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("NetBlade-tests")
    app.setApplicationName("NetBlade-tests")
    yield app
    # Tasks still running when the tests end must not report into Qt while it shuts down.
    # Without this, CI on Linux crashed with a segmentation fault after all tests passed.
    tasks.shutdown()


@pytest.fixture
def settings(qapp, tmp_path):
    """An empty settings file for one test."""
    return QSettings(str(tmp_path / "netblade.ini"), QSettings.Format.IniFormat)
