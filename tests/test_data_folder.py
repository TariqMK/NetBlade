"""Checks that NetBlade keeps its data in NetBlade-data beside the app, and never quietly saves elsewhere."""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from netblade import app, data_folder
from netblade.app import ORDER_KEY, MainWindow
from netblade.ui.notepad_page import NotepadPage


def test_source_run_uses_project_root(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    root = Path(data_folder.__file__).resolve().parent.parent
    assert data_folder.default_folder() == root / "NetBlade-data"
    assert (root / "pyproject.toml").exists()


def test_packaged_build_uses_folder_beside_the_executable(monkeypatch, tmp_path):
    exe = tmp_path / "Tools" / "NetBlade.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert (
        data_folder.default_folder() == (tmp_path / "Tools").resolve() / "NetBlade-data"
    )


def test_macos_bundle_uses_folder_beside_the_app(monkeypatch, tmp_path):
    exe = tmp_path / "Apps" / "NetBlade.app" / "Contents" / "MacOS" / "NetBlade"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert (
        data_folder.default_folder() == (tmp_path / "Apps").resolve() / "NetBlade-data"
    )


def test_can_write_checks_without_leaving_files(tmp_path):
    folder = tmp_path / "NetBlade-data"
    assert data_folder.can_write(folder)
    assert (
        list(tmp_path.iterdir()) == []
    )  # The folder is created only when something is saved.


def test_can_write_is_false_when_the_name_is_a_file(tmp_path):
    (tmp_path / "NetBlade-data").write_text("not a folder")
    assert not data_folder.can_write(tmp_path / "NetBlade-data")


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="Needs POSIX permissions and a non-root user",
)
def test_can_write_is_false_in_a_read_only_folder(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        assert not data_folder.can_write(locked / "NetBlade-data")
    finally:
        locked.chmod(0o755)


def test_window_saves_everything_in_its_data_folder(qapp, tmp_path):
    data = tmp_path / "NetBlade-data"
    window = MainWindow(data=data)
    window.move_current(1)
    window.settings.sync()
    window.notepad_page.editor.setPlainText("vlan 20")
    window.notepad_page.shutdown()
    assert (data / "settings.ini").exists()
    assert (data / "notes.txt").read_text(encoding="utf-8") == "vlan 20"
    assert window.mac_page.folder == data / "oui"
    assert list(window.settings.value(ORDER_KEY)) == window.tool_names()


def test_window_without_folder_uses_a_temporary_one(qapp):
    window = MainWindow()
    assert window.data != data_folder.default_folder()
    assert window.notepad_page.path.parent == window.data


def test_writable_folder_asks_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        data_folder, "default_folder", lambda: tmp_path / "NetBlade-data"
    )
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: pytest.fail("No question expected")
    )
    assert app.choose_data_folder() == (tmp_path / "NetBlade-data", True)


def answer_with(monkeypatch, label: str) -> None:
    """Make the next message box act as if the user selected the button with this text."""

    def exec_(box):
        button = next(b for b in box.buttons() if b.text() == label)
        monkeypatch.setattr(box, "clickedButton", lambda: button)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)


def test_read_only_folder_can_continue_without_saving(monkeypatch, tmp_path):
    monkeypatch.setattr(
        data_folder, "default_folder", lambda: tmp_path / "NetBlade-data"
    )
    monkeypatch.setattr(data_folder, "can_write", lambda folder: False)
    answer_with(monkeypatch, "Continue without saving")
    folder, saved = app.choose_data_folder()
    assert not saved
    assert not folder.is_relative_to(
        tmp_path
    )  # A temporary folder, never a quiet alternative.


def test_read_only_folder_can_use_a_chosen_folder(monkeypatch, tmp_path):
    chosen = tmp_path / "Chosen"
    monkeypatch.setattr(
        data_folder, "default_folder", lambda: tmp_path / "locked" / "NetBlade-data"
    )
    monkeypatch.setattr(
        data_folder, "can_write", lambda folder: chosen in folder.parents
    )
    answer_with(monkeypatch, "Choose folder…")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(chosen))
    assert app.choose_data_folder() == (chosen / "NetBlade-data", True)


def test_unsaved_notes_say_so(qapp, tmp_path):
    page = NotepadPage(tmp_path / "notes.txt", saved=False)
    page.editor.setPlainText("temp")
    page.save()
    assert page.status.text().endswith("Kept until NetBlade closes")
    assert "lost when NetBlade closes" in page.editor.placeholderText()
