"""Text diff page: paste two texts and see the changed lines, words or characters side by side.

The pasted text stays in memory only. It often holds configs and secrets, so it is never saved.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QMimeData, QRect, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade.core import text_diff
from netblade.core.text_diff import Diff, DiffInputError, Kind, Level, Line, Row
from netblade.ui import tasks, theme
from netblade.ui.widgets import caption, segmented

COMPARE_DELAY_MS = 250  # Compare shortly after typing stops, not on every key press.
LEVELS = (Level.LINES, Level.WORDS, Level.CHARACTERS)
CONTEXT_ROWS = 2  # Rows shown above a change when moving to it.
LINE_TINT = 0.14
SPAN_TINT = 0.38
GUTTER_PADDING = 8
CURRENT_BAR_PX = 2


def _tint(color: str, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


class _Gutter(QWidget):
    """The margin of a pane: line numbers, a + or - marker and a bar on the current change."""

    def __init__(self, pane: DiffPane) -> None:
        super().__init__(pane)
        self.pane = pane

    def paintEvent(self, event) -> None:
        self.pane.paint_gutter(self, event)


class DiffPane(QPlainTextEdit):
    """One read-only side of the comparison. Every row is one line, so the two panes stay aligned."""

    def __init__(self, name: str, marker: str, tint_role: str) -> None:
        super().__init__()
        self.setObjectName("raw")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Always on: a bar on one pane only would make the panes differ in height.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setFont(theme.mono_font())
        self.setAccessibleName(name)
        self.marker = marker
        self.tint_role = tint_role  # The theme token for changes on this side.
        self.rows: list[tuple[Kind, Line | None]] = []
        self.current: tuple[int, int] | None = None
        self.gutter = _Gutter(self)
        self.updateRequest.connect(self._gutter_update)

    # Content

    def show_rows(self, rows: list[tuple[Kind, Line | None]]) -> None:
        self.rows = rows
        self.current = None
        span_format = QTextCharFormat()
        span_format.setBackground(self._change_color(SPAN_TINT))
        plain = QTextCharFormat()
        self.clear()
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for index, (_kind, line) in enumerate(rows):
            if index:
                cursor.insertBlock()
            if line is None:
                continue
            position = 0
            for start, end in line.spans:
                cursor.insertText(line.text[position:start], plain)
                cursor.insertText(line.text[start:end], span_format)
                position = end
            cursor.insertText(line.text[position:], plain)
        cursor.endEditBlock()
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self._place_gutter()  # Its width depends on the highest line number.
        self.gutter.update()

    def set_current(self, section: tuple[int, int] | None) -> None:
        self.current = section
        self.gutter.update()

    def _change_color(self, alpha: float) -> QColor:
        return _tint(getattr(theme.current(), self.tint_role), alpha)

    # Copy without the empty rows that keep the panes aligned

    def createMimeDataFromSelection(self) -> QMimeData:
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        parts = []
        block = self.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            number = block.blockNumber()
            if number >= len(self.rows) or self.rows[number][1] is not None:
                offset = block.position()
                parts.append(block.text()[max(start - offset, 0) : end - offset])
            block = block.next()
        data = QMimeData()
        data.setText("\n".join(parts))
        return data

    # Painting

    def paintEvent(self, event) -> None:
        """Tint each row to the full width. A block format would stop at the longest line."""
        painter = QPainter(self.viewport())
        t = theme.current()
        change = self._change_color(LINE_TINT)
        empty = QBrush(QColor(t.hairline_strong), Qt.BrushStyle.BDiagPattern)
        width = self.viewport().width()
        for number, top, height in self._visible_rows(event.rect().bottom()):
            kind, line = self.rows[number]
            area = QRect(0, top, width, height)
            if line is None:
                painter.fillRect(area, empty)
            elif kind is not Kind.EQUAL:
                painter.fillRect(area, change)
        painter.end()
        super().paintEvent(event)

    def _visible_rows(self, bottom: int):
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        top = self.blockBoundingGeometry(block).translated(offset).top()
        while block.isValid() and top <= bottom:
            height = self.blockBoundingRect(block).height()
            number = block.blockNumber()
            if number < len(self.rows):
                yield number, round(top), round(top + height) - round(top)
            top += height
            block = block.next()

    def paint_gutter(self, gutter: QWidget, event) -> None:
        t = theme.current()
        painter = QPainter(gutter)
        painter.setFont(self.font())
        width = gutter.width()
        marker_width = self.fontMetrics().horizontalAdvance("+")
        for number, top, height in self._visible_rows(event.rect().bottom()):
            kind, line = self.rows[number]
            if self.current and self.current[0] <= number <= self.current[1]:
                painter.fillRect(0, top, CURRENT_BAR_PX, height, QColor(t.accent))
            if line is None:
                continue
            painter.setPen(QColor(t.text_muted))
            painter.drawText(
                0,
                top,
                width - marker_width - 2 * GUTTER_PADDING,
                height,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(line.number),
            )
            if kind is not Kind.EQUAL:
                painter.setPen(QColor(getattr(t, self.tint_role)))
                painter.drawText(
                    width - marker_width - GUTTER_PADDING,
                    top,
                    marker_width,
                    height,
                    Qt.AlignmentFlag.AlignCenter,
                    self.marker,
                )
        painter.end()

    def _gutter_width(self) -> int:
        highest = max((line.number for _kind, line in self.rows if line), default=1)
        digits = max(len(str(highest)), 2)
        return (
            self.fontMetrics().horizontalAdvance("9" * (digits + 1))
            + 3 * GUTTER_PADDING
        )

    def _gutter_update(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def _place_gutter(self) -> None:
        width = self._gutter_width()
        self.setViewportMargins(width, 0, 0, 0)
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left(), area.top(), width, area.height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_gutter()


class DiffPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.diff: Diff | None = None
        self._section = 0
        self._running = False
        self._dirty = False

        self.left_input = self._input("Text A", "Paste the first text here.")
        self.right_input = self._input("Text B", "Paste the second text here.")
        inputs = QHBoxLayout()
        inputs.setContentsMargins(0, 0, 0, 0)
        inputs.setSpacing(16)
        for title, editor in (
            ("Text A", self.left_input),
            ("Text B", self.right_input),
        ):
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(caption(title))
            column.addWidget(editor, 1)
            inputs.addLayout(column, 1)
        top = QWidget()
        top.setLayout(inputs)

        levels, self.level_group = segmented(["Lines", "Words", "Characters"], self)
        self.level_group.button(LEVELS.index(Level.WORDS)).setChecked(True)
        self.level_group.idClicked.connect(self.compare)
        self.ignore_whitespace = QCheckBox("Ignore whitespace")
        self.ignore_whitespace.toggled.connect(self.compare)
        self.ignore_case = QCheckBox("Ignore case")
        self.ignore_case.toggled.connect(self.compare)
        options = QHBoxLayout()
        options.setSpacing(16)
        options.addWidget(levels)
        options.addWidget(self.ignore_whitespace)
        options.addWidget(self.ignore_case)
        options.addStretch()

        self.summary = caption("")
        self.summary.setWordWrap(True)
        self.previous_button = QPushButton("Previous")
        self.previous_button.setAccessibleName("Previous difference")
        self.previous_button.clicked.connect(partial(self.move_section, -1))
        self.next_button = QPushButton("Next")
        self.next_button.setAccessibleName("Next difference")
        self.next_button.clicked.connect(partial(self.move_section, 1))
        self.copy_button = QPushButton("Copy diff")
        self.copy_button.setToolTip("Copy the differences in unified diff format")
        self.copy_button.clicked.connect(self._copy_diff)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.summary, 1)
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        actions.addWidget(self.copy_button)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        self.left_pane = DiffPane("Text A, compared", "-", "negative")
        self.right_pane = DiffPane("Text B, compared", "+", "positive")
        self._link_scrolling()
        self.results = QWidget()
        panes = QHBoxLayout(self.results)
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(16)
        panes.addWidget(self.left_pane, 1)
        panes.addWidget(self.right_pane, 1)

        self.empty = QLabel("Paste text into both boxes to compare it.")
        self.empty.setProperty("role", "secondary")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.results)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(12)
        bottom_layout.addLayout(options)
        bottom_layout.addLayout(actions)
        bottom_layout.addWidget(self.error)
        bottom_layout.addWidget(self.body, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(16)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMPARE_DELAY_MS)
        self._timer.timeout.connect(self.compare)
        self.left_input.textChanged.connect(self._timer.start)
        self.right_input.textChanged.connect(self._timer.start)
        self._update_actions()

    def _input(self, name: str, placeholder: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName("raw")
        editor.setFont(theme.mono_font())
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlaceholderText(placeholder)
        editor.setAccessibleName(name)
        editor.setTabChangesFocus(
            True
        )  # Tab moves on, so the keyboard can leave the box.
        return editor

    def _link_scrolling(self) -> None:
        left, right = self.left_pane, self.right_pane
        # setValue does nothing when the value is the same, so this can't loop.
        left.verticalScrollBar().valueChanged.connect(
            right.verticalScrollBar().setValue
        )
        right.verticalScrollBar().valueChanged.connect(
            left.verticalScrollBar().setValue
        )
        left.horizontalScrollBar().valueChanged.connect(
            right.horizontalScrollBar().setValue
        )
        right.horizontalScrollBar().valueChanged.connect(
            left.horizontalScrollBar().setValue
        )

    # Comparing

    def compare(self, *_args) -> None:
        """Compare the two texts on a background thread. Only one comparison runs at a time."""
        self._timer.stop()
        if self._running:
            self._dirty = True
            return
        left, right = self.left_input.toPlainText(), self.right_input.toPlainText()
        if not left or not right:
            self._clear_results()
            return
        self._running = True
        self._dirty = False
        self.summary.setText("Comparing…")
        work = partial(
            text_diff.compare,
            left,
            right,
            LEVELS[self.level_group.checkedId()],
            self.ignore_whitespace.isChecked(),
            self.ignore_case.isChecked(),
        )
        tasks.run_in_thread(work, self._finished, self._failed)

    def _finished(self, diff: Diff) -> None:
        self._running = False
        if (
            self._dirty
        ):  # The input changed during the comparison, so this result is old.
            self.compare()
            return
        self.show_diff(diff)

    def _failed(self, error: Exception) -> None:
        self._running = False
        if self._dirty:
            self.compare()
            return
        message = (
            str(error)
            if isinstance(error, DiffInputError)
            else f"The comparison failed: {error}"
        )
        self._clear_results()
        self.error.setText(message)
        self.error.show()

    def _clear_results(self) -> None:
        self.diff = None
        self.error.hide()
        self.summary.setText("")
        self.body.setCurrentWidget(self.empty)
        self._update_actions()

    # Results

    def show_diff(self, diff: Diff) -> None:
        self.diff = diff
        self.error.hide()
        self._section = 0
        self._fill_panes()
        self.body.setCurrentWidget(self.results)
        self._show_section()

    def _fill_panes(self) -> None:
        rows: list[Row] = self.diff.rows
        self.left_pane.show_rows([(row.kind, row.left) for row in rows])
        self.right_pane.show_rows([(row.kind, row.right) for row in rows])

    def move_section(self, step: int, *_args) -> None:
        if self.diff is None or not self.diff.sections:
            return
        self._section = (self._section + step) % len(self.diff.sections)
        self._show_section()

    def _show_section(self) -> None:
        section = self.diff.sections[self._section] if self.diff.sections else None
        for pane in (self.left_pane, self.right_pane):
            pane.set_current(section)
        if section:
            self.left_pane.verticalScrollBar().setValue(
                max(0, section[0] - CONTEXT_ROWS)
            )
        self.summary.setText(self._summary_text())
        self._update_actions()

    def _summary_text(self) -> str:
        diff = self.diff
        if not diff.has_changes:
            if diff.exact_match:
                return "No differences."
            return "No differences. Ignored differences or line endings exist."
        parts = [f"Difference {self._section + 1} of {len(diff.sections)}"]
        # Neutral words: the user may not know which text is the newer one.
        for count, words in (
            (diff.changed, "different"),
            (diff.removed, "only in A"),
            (diff.added, "only in B"),
        ):
            if count:
                parts.append(f"{count:,} {words}")
        text = " · ".join(parts)
        if diff.inline_skipped:
            text += ". Some long lines or large blocks have no inner highlights."
        return text

    def _update_actions(self) -> None:
        has_changes = self.diff is not None and self.diff.has_changes
        self.previous_button.setEnabled(has_changes)
        self.next_button.setEnabled(has_changes)
        self.copy_button.setEnabled(has_changes)

    def _copy_diff(self) -> None:
        if self.diff is not None:
            QApplication.clipboard().setText(text_diff.unified(self.diff))

    def apply_theme(self) -> None:
        if self.diff is None:
            return
        scroll = self.left_pane.verticalScrollBar().value()
        section = self.diff.sections[self._section] if self.diff.sections else None
        self._fill_panes()
        for pane in (self.left_pane, self.right_pane):
            pane.set_current(section)
        self.left_pane.verticalScrollBar().setValue(scroll)
