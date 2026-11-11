"""Encode and decode page: Base64, URL percent-encoding, hex and JWT, converted as you type.

The text stays in memory only. Tokens and auth headers are secrets, so it is never saved.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from netblade.core import codec
from netblade.core.codec import CodecError, Format, Result
from netblade.ui import theme
from netblade.ui.widgets import caption, segmented

CONVERT_DELAY_MS = 150  # Convert shortly after typing stops, not on every key press.
DECODE, ENCODE = 0, 1
FORMATS = (Format.BASE64, Format.URL, Format.HEX, Format.JWT)
PLACEHOLDERS = {
    DECODE: "Paste text to decode: Base64, a Basic auth header, a PEM block, %-encoded text, hex or a JWT.",
    ENCODE: "Type or paste text to encode.",
}


class CodecPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.result: Result | None = None

        directions, self.direction_group = segmented(["Decode", "Encode"], self)
        self.direction_group.idClicked.connect(self._options_changed)
        formats, self.format_group = segmented([f.value for f in FORMATS], self)
        self.format_group.idClicked.connect(self._options_changed)
        self.url_safe = QCheckBox("URL-safe")
        self.url_safe.setToolTip(
            "Use - and _ instead of + and /, with no = padding (base64url)"
        )
        self.url_safe.toggled.connect(self.convert)
        self.plus_as_space = QCheckBox("+ means space")
        self.plus_as_space.setToolTip(
            "Decode + as a space, as in query strings and form data"
        )
        self.plus_as_space.setChecked(True)
        self.plus_as_space.toggled.connect(self.convert)
        options = QHBoxLayout()
        options.setSpacing(16)
        options.addWidget(directions)
        options.addWidget(formats)
        options.addWidget(self.url_safe)
        options.addWidget(self.plus_as_space)
        options.addStretch()

        self.input = self._editor("Input")
        self.input.textChanged.connect(self._input_changed)
        self.output = self._editor("Output")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("The result shows here.")
        boxes = QHBoxLayout()
        boxes.setSpacing(16)
        for title, editor in (("Input", self.input), ("Output", self.output)):
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(caption(title))
            column.addWidget(editor, 1)
            boxes.addLayout(column, 1)

        self.status = caption("")
        self.status.setWordWrap(True)
        self.hint = caption("")
        self.hint_button = QPushButton()
        self.hint_button.clicked.connect(self._use_hint)
        self._hinted: Format | None = None
        self.swap_button = QPushButton("Swap")
        self.swap_button.setToolTip("Move the output to the input and change direction")
        self.swap_button.clicked.connect(self.swap)
        self.copy_button = QPushButton("Copy output")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.output.toPlainText())
        )
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.input.clear)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.status, 1)
        actions.addWidget(self.hint)
        actions.addWidget(self.hint_button)
        actions.addSpacing(8)
        actions.addWidget(self.swap_button)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.clear_button)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(options)
        layout.addLayout(actions)
        layout.addWidget(self.error)
        layout.addLayout(boxes, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CONVERT_DELAY_MS)
        self._timer.timeout.connect(self.convert)
        self._options_changed()

    def _editor(self, name: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName("raw")
        editor.setFont(theme.mono_font())
        editor.setAccessibleName(name)
        editor.setTabChangesFocus(
            True
        )  # Tab moves on, so the keyboard can leave the box.
        return editor

    # State

    @property
    def fmt(self) -> Format:
        return FORMATS[self.format_group.checkedId()]

    @property
    def decoding(self) -> bool:
        return self.direction_group.checkedId() == DECODE

    def set_mode(self, fmt: Format, decoding: bool = True) -> None:
        self.format_group.button(FORMATS.index(fmt)).setChecked(True)
        self.direction_group.button(DECODE if decoding else ENCODE).setChecked(True)
        self._options_changed()

    def _options_changed(self, *_args) -> None:
        jwt = self.fmt is Format.JWT
        # A JWT can only be read: creating one needs a signing key.
        if jwt and not self.decoding:
            self.direction_group.button(DECODE).setChecked(True)
        self.direction_group.button(ENCODE).setEnabled(not jwt)
        self.direction_group.button(ENCODE).setToolTip(
            "A JWT can only be decoded. Creating one needs a signing key."
            if jwt
            else ""
        )
        self.url_safe.setVisible(self.fmt is Format.BASE64 and not self.decoding)
        self.plus_as_space.setVisible(self.fmt is Format.URL and self.decoding)
        self.input.setPlaceholderText(PLACEHOLDERS[DECODE if self.decoding else ENCODE])
        self.convert()

    # Converting

    def _input_changed(self) -> None:
        self._timer.start()

    def convert(self, *_args) -> None:
        self._timer.stop()
        text = self.input.toPlainText()
        self.error.hide()
        if not text.strip():
            self._show(None)
            self._update_hint("")
            return
        try:
            if self.decoding:
                result = codec.decode(text, self.fmt, self.plus_as_space.isChecked())
            else:
                result = codec.encode(text, self.fmt, self.url_safe.isChecked())
        except CodecError as error:
            self._show(None)
            self.error.setText(str(error))
            self.error.show()
        else:
            self._show(result)
        self._update_hint(text)

    def _show(self, result: Result | None) -> None:
        self.result = result
        self.output.setPlainText(result.text if result else "")
        # A hex dump keeps its columns; text wraps so long Base64 or JSON values stay readable.
        self.output.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
            if result and result.binary
            else QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self.status.setText(result.note if result else "")
        has_output = result is not None and bool(result.text)
        self.copy_button.setEnabled(has_output)
        self.swap_button.setEnabled(
            has_output and not result.binary and self.fmt is not Format.JWT
        )
        self.clear_button.setEnabled(bool(self.input.toPlainText()))

    def _update_hint(self, text: str) -> None:
        """Suggest a better format when the pasted text clearly looks like one."""
        found = codec.detect(text) if self.decoding and text.strip() else None
        if found is self.fmt:
            found = None
        self._hinted = found
        self.hint.setVisible(found is not None)
        self.hint_button.setVisible(found is not None)
        if found is not None:
            self.hint.setText(
                f"This looks like {'a JWT' if found is Format.JWT else found.value}."
            )
            self.hint_button.setText(f"Use {found.value}")

    def _use_hint(self) -> None:
        if self._hinted is not None:
            self.set_mode(self._hinted, decoding=True)

    def swap(self) -> None:
        """Put the output in the input and reverse the direction, to check a round trip."""
        if self.result is None or self.result.binary or self.fmt is Format.JWT:
            return
        text = self.output.toPlainText()
        self.direction_group.button(ENCODE if self.decoding else DECODE).setChecked(
            True
        )
        self.input.blockSignals(True)
        self.input.setPlainText(text)
        self.input.blockSignals(False)
        self._options_changed()
