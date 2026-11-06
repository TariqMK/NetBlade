"""Checks that notes save automatically, come back after a restart, and are cleared only after confirmation."""

from PySide6.QtWidgets import QMessageBox

from netblade.ui.notepad_page import NotepadPage


def test_notes_save_and_reload(qapp, tmp_path):
    path = tmp_path / "notes.txt"
    page = NotepadPage(path)
    page.editor.setPlainText("router 10.0.0.1\n\tvlan 20")
    assert page._save_timer.isActive()  # Saving waits until typing stops.
    page.shutdown()  # Closing saves at once.
    assert path.read_text(encoding="utf-8") == "router 10.0.0.1\n\tvlan 20"

    reopened = NotepadPage(path)
    assert reopened.editor.toPlainText() == "router 10.0.0.1\n\tvlan 20"
    assert reopened.status.text().startswith("2 lines")


def test_clear_asks_first(qapp, tmp_path, monkeypatch):
    path = tmp_path / "notes.txt"
    page = NotepadPage(path)
    page.editor.setPlainText("keep me")
    page.save()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    page.clear()
    assert page.editor.toPlainText() == "keep me"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    page.clear()
    assert page.editor.toPlainText() == ""
    assert path.read_text(encoding="utf-8") == ""


def test_wrap_toggle(qapp, tmp_path):
    page = NotepadPage(tmp_path / "notes.txt")
    page.wrap.setChecked(False)
    assert page.editor.lineWrapMode() == page.editor.LineWrapMode.NoWrap
    page.wrap.setChecked(True)
    assert page.editor.lineWrapMode() == page.editor.LineWrapMode.WidgetWidth


def test_editor_uses_the_monospace_font(qapp, tmp_path):
    # Offscreen test runs have no fonts installed, so compare the requested font, not the resolved one.
    # In the real app this resolves to Cascadia Mono or Consolas on Windows, SF Mono or Menlo on macOS.
    from netblade.ui import theme

    page = NotepadPage(tmp_path / "notes.txt")
    assert page.editor.font().families() == theme.mono_font().families()
