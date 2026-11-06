"""curl page: build a request in a form, see the exact curl command, run it and inspect the response."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIntValidator, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade.core.curl import (
    DEFAULT_TIMEOUT_S,
    HTTP_VERSIONS,
    IP_VERSIONS,
    METHODS,
    SUGGESTED_OPTIONS,
    CurlInputError,
    CurlRequest,
    CurlResult,
    CurlRun,
    Phase,
    body_text,
    build_args,
    display_command,
    split_verbose,
    status_text,
    timing_phases,
)
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread

HEADERS_VIEW, BODY_VIEW, VERBOSE_VIEW = 0, 1, 2


def _size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


class TimingBar(QWidget):
    """Stacked bar of the request phases. The slowest phase takes the accent colour."""

    BAR_HEIGHT = 8
    GAP = 2

    def __init__(self) -> None:
        super().__init__()
        self.phases: list[Phase] = []
        self.setFixedHeight(44)

    def set_phases(self, phases: list[Phase]) -> None:
        self.phases = phases
        total = sum(p.ms for p in phases)
        parts = ", ".join(f"{p.name} {p.ms:.1f} ms" for p in phases)
        self.setAccessibleDescription(f"Timing: {parts}. Total {total:.1f} ms")
        self.update()

    def paintEvent(self, event) -> None:
        if not self.phases:
            return
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(p.ms for p in self.phases) or 1.0
        slowest = max(self.phases, key=lambda p: p.ms)
        visible = [p for p in self.phases if p.ms > 0]
        width = self.width() - self.GAP * max(len(visible) - 1, 0)

        x = 0.0
        painter.setPen(Qt.PenStyle.NoPen)
        for phase in visible:
            w = max(width * phase.ms / total, 3.0)
            painter.setBrush(QColor(t.accent if phase is slowest else t.text_muted))
            painter.drawRoundedRect(QRectF(x, 0, w, self.BAR_HEIGHT), 2, 2)
            x += w + self.GAP

        painter.setFont(theme.mono_font(theme.SMALL_PX))
        metrics = painter.fontMetrics()
        x = 0.0
        y = self.BAR_HEIGHT + 12
        for phase in self.phases:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent if phase is slowest else t.text_muted))
            painter.drawRoundedRect(QRectF(x, y + 4, 8, 8), 2, 2)
            label = f"{phase.name} {phase.ms:.1f} ms"
            painter.setPen(QColor(t.text_secondary))
            painter.drawText(
                QRectF(x + 14, y, metrics.horizontalAdvance(label) + 4, 16),
                Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            x += 14 + metrics.horizontalAdvance(label) + 20
        total_label = f"Total {total:.1f} ms"
        painter.setPen(QColor(t.text))
        painter.drawText(
            QRectF(0, y, self.width(), 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            total_label,
        )
        painter.end()


def _segmented(labels: list[str], parent: QWidget) -> tuple[QWidget, QButtonGroup]:
    host = QWidget()
    host.setObjectName("segmented")
    host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(2)
    group = QButtonGroup(parent)
    for index, text in enumerate(labels):
        button = QPushButton(text)
        button.setObjectName("segment")
        button.setCheckable(True)
        group.addButton(button, index)
        layout.addWidget(button)
    group.button(0).setChecked(True)
    return host, group


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "secondary")
    return label


class CurlPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._run: CurlRun | None = None
        self._request_id = 0
        self._result: CurlResult | None = None

        # Request line
        self.method = QComboBox()
        self.method.addItems(METHODS)
        self.method.setAccessibleName("Method")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://example.com")
        self.url.setAccessibleName("URL")
        self.url.returnPressed.connect(self.toggle)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.setMinimumWidth(88)
        self.send_button.clicked.connect(self.toggle)
        request_row = QHBoxLayout()
        request_row.setSpacing(8)
        request_row.addWidget(self.method)
        request_row.addWidget(self.url, 1)
        request_row.addWidget(self.send_button)

        # Common troubleshooting options
        self.follow = QCheckBox("Follow redirects")
        self.follow.setChecked(True)
        self.insecure = QCheckBox("Ignore TLS errors")
        self.ip_version = QComboBox()
        self.ip_version.addItems(list(IP_VERSIONS))
        self.ip_version.setAccessibleName("IP version")
        self.http_version = QComboBox()
        self.http_version.addItems(list(HTTP_VERSIONS))
        self.http_version.setAccessibleName("HTTP version")
        self.connect_ip = QLineEdit()
        self.connect_ip.setPlaceholderText("DNS result")
        self.connect_ip.setToolTip(
            "Connect to this IP address but keep the host name, like curl --resolve"
        )
        self.connect_ip.setAccessibleName("Connect to IP")
        self.connect_ip.setFixedWidth(160)
        self.timeout = QLineEdit(str(DEFAULT_TIMEOUT_S))
        self.timeout.setValidator(QIntValidator(1, 600, self.timeout))
        self.timeout.setAccessibleName("Timeout in seconds")
        self.timeout.setFixedWidth(64)
        options_row = QHBoxLayout()
        options_row.setSpacing(16)
        options_row.addWidget(self.follow)
        options_row.addWidget(self.insecure)
        options_row.addStretch()
        options_row.addWidget(self.ip_version)
        options_row.addWidget(self.http_version)
        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_row.addWidget(_caption("Connect to IP"))
        target_row.addWidget(self.connect_ip)
        target_row.addSpacing(16)
        target_row.addWidget(_caption("Timeout"))
        target_row.addWidget(self.timeout)
        target_row.addWidget(_caption("s"))
        target_row.addStretch()

        # Any other curl option
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(
            "More options: any other curl options, for example --compressed"
        )
        self.extra.setAccessibleName("More options")
        self.extra.returnPressed.connect(self.toggle)
        self.suggestions = QPushButton("Suggestions")
        menu = QMenu(self.suggestions)
        for text, description in SUGGESTED_OPTIONS:
            action = QAction(f"{text}    {description}", menu)
            action.triggered.connect(
                lambda _checked=False, value=text: self._add_option(value)
            )
            menu.addAction(action)
        self.suggestions.setMenu(menu)
        self.headers_toggle = QPushButton("Headers and body")
        self.headers_toggle.setCheckable(True)
        self.headers_toggle.toggled.connect(
            lambda shown: self.editors.setVisible(shown)
        )
        extra_row = QHBoxLayout()
        extra_row.setSpacing(8)
        extra_row.addWidget(self.extra, 1)
        extra_row.addWidget(self.suggestions)
        target_row.addWidget(self.headers_toggle)

        # Headers and body editors, hidden until needed
        self.headers = QPlainTextEdit()
        self.headers.setObjectName("raw")
        self.headers.setPlaceholderText("One header per line\nAccept: application/json")
        self.headers.setFont(theme.mono_font())
        self.headers.setAccessibleName("Request headers")
        self.body = QPlainTextEdit()
        self.body.setObjectName("raw")
        self.body.setPlaceholderText('Request body\n{"name": "value"}')
        self.body.setFont(theme.mono_font())
        self.body.setAccessibleName("Request body")
        self.editors = QWidget()
        editors_layout = QHBoxLayout(self.editors)
        editors_layout.setContentsMargins(0, 0, 0, 0)
        editors_layout.setSpacing(8)
        editors_layout.addWidget(self.headers)
        editors_layout.addWidget(self.body)
        self.editors.setFixedHeight(96)
        self.editors.hide()

        # The command that will run
        self.command = QLabel()
        self.command.setFont(theme.mono_font())
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.command.setAccessibleName("curl command")
        copy_command = QPushButton("Copy command")
        copy_command.clicked.connect(
            lambda: QApplication.clipboard().setText(self.command.text())
        )
        command_card = QFrame()
        command_card.setObjectName("card")
        command_layout = QHBoxLayout(command_card)
        command_layout.setContentsMargins(16, 8, 8, 8)
        command_layout.setSpacing(12)
        command_layout.addWidget(self.command, 1)
        command_layout.addWidget(copy_command, 0, Qt.AlignmentFlag.AlignTop)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        # Results
        self.status = QLabel()
        self.status.setFont(theme.ui_font(15, QFont.Weight.Medium))
        self.meta = _caption("")
        self.meta.setWordWrap(True)
        summary = QHBoxLayout()
        summary.setSpacing(16)
        summary.addWidget(self.status)
        summary.addWidget(self.meta, 1)
        self.timing = TimingBar()
        segmented, self.view_group = _segmented(["Headers", "Body", "Verbose"], self)
        self.view_group.idClicked.connect(self._show_view)
        self.copy_view = QPushButton("Copy")
        self.copy_view.clicked.connect(
            lambda: QApplication.clipboard().setText(self.viewer.toPlainText())
        )
        view_row = QHBoxLayout()
        view_row.addWidget(segmented)
        view_row.addStretch()
        view_row.addWidget(self.copy_view)
        self.viewer = QPlainTextEdit()
        self.viewer.setObjectName("raw")
        self.viewer.setReadOnly(True)
        self.viewer.setFont(theme.mono_font())
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.viewer.setAccessibleName("Response")

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addLayout(summary)
        results_layout.addWidget(self.timing)
        results_layout.addLayout(view_row)
        results_layout.addWidget(self.viewer, 1)

        self.empty = QLabel("Enter a URL, then select Send to run curl.")
        self.empty.setProperty("role", "secondary")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_stack = QStackedWidget()
        self.body_stack.addWidget(self.empty)
        self.body_stack.addWidget(results)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(request_row)
        layout.addLayout(options_row)
        layout.addLayout(target_row)
        layout.addLayout(extra_row)
        layout.addWidget(self.editors)
        layout.addWidget(command_card)
        layout.addWidget(self.error)
        layout.addSpacing(4)
        layout.addWidget(self.body_stack, 1)

        for signal in (
            self.method.currentTextChanged,
            self.url.textChanged,
            self.follow.toggled,
            self.insecure.toggled,
            self.ip_version.currentTextChanged,
            self.http_version.currentTextChanged,
            self.connect_ip.textChanged,
            self.timeout.textChanged,
            self.extra.textChanged,
            self.headers.textChanged,
            self.body.textChanged,
        ):
            signal.connect(self._update_command)
        self._update_command()

    # Form

    def _request(self) -> CurlRequest:
        return CurlRequest(
            url=self.url.text(),
            method=self.method.currentText(),
            headers=self.headers.toPlainText().splitlines(),
            body=self.body.toPlainText(),
            follow_redirects=self.follow.isChecked(),
            insecure=self.insecure.isChecked(),
            ip_version=self.ip_version.currentText(),
            http_version=self.http_version.currentText(),
            connect_to_ip=self.connect_ip.text(),
            timeout_s=int(self.timeout.text() or 0),
            extra_args=self.extra.text(),
        )

    def _update_command(self, *_args) -> None:
        request = self._request()
        if not request.url.strip():
            request.url = "https://example.com"
        try:
            self.command.setText(display_command(build_args(request)))
        except CurlInputError as error:
            self.command.setText(str(error))
        count = sum(
            1 for line in self.headers.toPlainText().splitlines() if line.strip()
        )
        has_body = bool(self.body.toPlainText())
        details = [f"{count} header{'s' if count != 1 else ''}"] if count else []
        if has_body:
            details.append("body")
        self.headers_toggle.setText(
            "Headers and body" + (f" ({', '.join(details)})" if details else "")
        )

    def _add_option(self, value: str) -> None:
        current = self.extra.text().strip()
        self.extra.setText(f"{current} {value}".strip())
        self.extra.setFocus()

    def _set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # Running

    def toggle(self) -> None:
        if self._run is not None:
            self._run.cancel()
        else:
            self.send()

    def send(self) -> None:
        try:
            args = build_args(self._request())
        except CurlInputError as error:
            self._set_error(str(error))
            return
        self._set_error("")
        self._request_id += 1
        request_id = self._request_id
        run = CurlRun(args, int(self.timeout.text()))
        self._run = run
        self.send_button.setText("Cancel")
        self.status.setText("Running…")
        self.status.setStyleSheet(f"color: {theme.current().text_secondary};")
        self.meta.setText("")
        self.timing.set_phases([])
        self.viewer.setPlainText("")
        self.body_stack.setCurrentIndex(1)

        def done(result: CurlResult) -> None:
            if request_id == self._request_id:
                self._finish()
                self._show_result(result)

        def failed(error: Exception) -> None:
            if request_id == self._request_id:
                self._finish()
                self._set_error(str(error))
                self.body_stack.setCurrentIndex(0)

        run_in_thread(run.run, done, failed)

    def _finish(self) -> None:
        self._run = None
        self.send_button.setText("Send")

    def _show_result(self, result: CurlResult) -> None:
        self._result = result
        t = theme.current()
        w = result.write_out
        error = result.error
        if error and not result.status:
            self.status.setText("Failed" if not result.cancelled else "Cancelled")
            color = t.negative if not result.cancelled else t.text_secondary
            self.meta.setText(error)
        else:
            code = result.status
            self.status.setText(status_text(code))
            color = {
                2: t.positive,
                3: t.text_secondary,
                4: t.warning,
                5: t.negative,
            }.get(code // 100, t.text)
            meta = []
            if w.get("http_version"):
                meta.append(f"HTTP/{w['http_version']}")
            if w.get("remote_ip"):
                ip = w["remote_ip"]
                meta.append(f"{f'[{ip}]' if ':' in ip else ip}:{w.get('remote_port')}")
            meta.append(_size(int(w.get("size_download") or 0)))
            redirects = int(w.get("num_redirects") or 0)
            if redirects:
                meta.append(
                    f"{redirects} redirect{'s' if redirects != 1 else ''} to {w.get('url_effective')}"
                )
            if error:
                meta.append(error)
            self.meta.setText(" · ".join(meta))
        self.status.setStyleSheet(f"color: {color};")
        phases = timing_phases(w) if result.status else []
        self.timing.set_phases(phases)
        self.timing.setVisible(bool(phases))
        self._show_view(self.view_group.checkedId())

    def _show_view(self, view_id: int) -> None:
        result = self._result
        if result is None:
            return
        if view_id == HEADERS_VIEW:
            request, response = split_verbose(result.verbose)
            if not request and not response:
                self.viewer.setPlainText(
                    "No headers were sent or received. Select Verbose to see what curl did."
                )
                return
            text = (
                "Request\n\n"
                + "\n".join(request)
                + "\n\n\nResponse\n\n"
                + "\n".join(response)
            )
        elif view_id == BODY_VIEW:
            text = body_text(result.body, result.write_out.get("content_type"))
            if result.body_truncated:
                text += "\n\n[Body cut at 1 MB for display]"
            if not result.body:
                text = "No body"
        else:
            text = result.verbose
        self.viewer.setPlainText(text)

    def shutdown(self) -> None:
        if self._run is not None:
            self._run.cancel()
