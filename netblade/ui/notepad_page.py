"""Notepad page: a monospace scratch pad that saves itself as you type."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QFontMetricsF, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from netblade.ui import theme
from netblade.ui.widgets import caption

SAVE_DELAY_MS = 800  # Save shortly after typing stops, not on every key press.
TAB_WIDTH = 4


def notes_file() -> Path:
    """Per-user notes file. No admin rights needed."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    return Path(base or Path.home() / ".netblade") / "notes.txt"


class NotepadPage(QWidget):
    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.setObjectName("page")
        self.path = path or notes_file()

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("raw")
        self.editor.setFont(theme.mono_font(theme.BASE_PX))
        self.editor.setPlaceholderText(
            "Temporary notes: paste output, IP addresses, configs or anything else.\n"
            "Notes save automatically and are kept when NetBlade closes."
        )
        self.editor.setAccessibleName("Notes")
        self.editor.setTabStopDistance(
            QFontMetricsF(self.editor.font()).horizontalAdvance(" ") * TAB_WIDTH
        )

        self.wrap = QCheckBox("Wrap long lines")
        self.wrap.setChecked(True)
        self.wrap.toggled.connect(self._set_wrap)
        self.status = caption("")
        copy_button = QPushButton("Copy all")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.editor.toPlainText())
        )
        save_as = QPushButton("Save as…")
        save_as.clicked.connect(self.save_as)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.wrap)
        controls.addSpacing(8)
        controls.addWidget(self.status, 1)
        controls.addWidget(copy_button)
        controls.addWidget(save_as)
        controls.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(controls)
        layout.addWidget(self.editor, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)
        self._load()
        self.editor.textChanged.connect(self._text_changed)
        self._set_wrap(True)

    # Saving

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        except OSError as error:
            text = ""
            self.status.setText(
                f"Can't read the saved notes: {error.strerror or error}"
            )
        self.editor.setPlainText(text)
        self._update_status("Saved" if text else "")

    def _text_changed(self) -> None:
        self._update_status("Saving…")
        self._save_timer.start()

    def save(self) -> None:
        """Write the notes to the notes file now."""
        self._save_timer.stop()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(self.editor.toPlainText(), encoding="utf-8")
            temporary.replace(
                self.path
            )  # A crash mid-save never leaves a half-written notes file.
        except OSError as error:
            self.status.setText(f"Can't save the notes: {error.strerror or error}")
            return
        self._update_status("Saved")

    def _update_status(self, state: str) -> None:
        text = self.editor.toPlainText()
        lines = text.count("\n") + 1 if text else 0
        counts = f"{lines:,} line{'s' if lines != 1 else ''} · {len(text):,} characters"
        self.status.setText(f"{counts} · {state}" if state else counts)

    def save_as(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save notes as", "notes.txt", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as error:
            self.status.setText(
                f"Can't save {Path(path).name}: {error.strerror or error}"
            )
            return
        self.status.setText(f"Saved a copy to {path}")

    def clear(self) -> None:
        """Empty the notes, after the user confirms."""
        if not self.editor.toPlainText():
            return
        answer = QMessageBox.question(
            self,
            "Clear notes",
            "Delete all notes? This can't be undone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self.editor.clear()
            self.save()

    def _set_wrap(self, wrap: bool) -> None:
        mode = (
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if wrap
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.editor.setLineWrapMode(mode)
        self.editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    def shutdown(self) -> None:
        if self._save_timer.isActive():
            self.save()
