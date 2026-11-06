"""TLS certificate page: certificate chain, trust, expiry and accepted TLS versions for a server."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade.core.tlscheck import (
    OLD_PROTOCOLS,
    WARN_DAYS,
    CertInfo,
    TlsCheckError,
    TlsResult,
    chain_role,
    check,
    expiry_text,
    parse_target,
)
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread
from netblade.ui.widgets import caption


def _state_color(cert: CertInfo) -> str:
    t = theme.current()
    if cert.expired() or cert.not_yet_valid():
        return t.negative
    if cert.days_left() <= WARN_DAYS:
        return t.warning
    return t.positive


class ValidityBar(QWidget):
    """The certificate's validity period as a bar, with a marker for today."""

    def __init__(self, cert: CertInfo) -> None:
        super().__init__()
        self.cert = cert
        self.setFixedHeight(28)
        self.setAccessibleName("Validity period")
        self.setAccessibleDescription(
            f"Valid from {cert.not_before:%Y-%m-%d} to {cert.not_after:%Y-%m-%d}. {expiry_text(cert)}"
        )

    def paintEvent(self, event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0, 4, self.width(), 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.hairline_strong)
        painter.drawRoundedRect(track, 3, 3)
        total = (self.cert.not_after - self.cert.not_before).total_seconds() or 1.0
        elapsed = (dt.datetime.now(dt.UTC) - self.cert.not_before).total_seconds()
        fraction = min(max(elapsed / total, 0.0), 1.0)
        painter.setBrush(QColor(_state_color(self.cert)))
        painter.drawRoundedRect(QRectF(0, 4, max(track.width() * fraction, 6), 6), 3, 3)
        painter.setFont(theme.mono_font(11))
        painter.setPen(QColor(t.text_muted))
        painter.drawText(
            QRectF(0, 12, 120, 16),
            Qt.AlignmentFlag.AlignLeft,
            f"{self.cert.not_before:%Y-%m-%d}",
        )
        painter.drawText(
            QRectF(self.width() - 120, 12, 120, 16),
            Qt.AlignmentFlag.AlignRight,
            f"{self.cert.not_after:%Y-%m-%d}",
        )
        painter.end()


def cert_card(index: int, cert: CertInfo, length: int, host: str) -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(6)

    header = QHBoxLayout()
    role = caption(chain_role(index, cert, length))
    title = QLabel(cert.subject_cn or cert.subject)
    title.setFont(theme.ui_font(14, QFont.Weight.Medium))
    title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    expiry = QLabel(expiry_text(cert))
    expiry.setStyleSheet(f"color: {_state_color(cert)};")
    header.addWidget(title)
    header.addStretch()
    header.addWidget(expiry)
    layout.addWidget(role)
    layout.addLayout(header)
    layout.addWidget(ValidityBar(cert))

    grid = QGridLayout()
    grid.setHorizontalSpacing(24)
    grid.setVerticalSpacing(6)
    grid.setColumnStretch(1, 1)
    rows = [("Issued by", cert.issuer_cn or cert.issuer)]
    if index == 0 and cert.names:
        rows.append(("Names", ", ".join(cert.names)))
    rows += [
        ("Key", cert.key),
        ("Signature", cert.signature),
        ("Serial", cert.serial),
        ("SHA-256", cert.sha256),
    ]
    for row, (label, value) in enumerate(rows):
        name = caption(label)
        text = QLabel(value)
        text.setFont(theme.mono_font())
        text.setWordWrap(label == "Names")
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(text, row, 1)
    layout.addLayout(grid)
    return card


class TlsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._request_id = 0

        self.target = QLineEdit()
        self.target.setPlaceholderText(
            "example.com, example.com:8443 or https://example.com"
        )
        self.target.setAccessibleName("Server")
        self.target.returnPressed.connect(self.run_check)
        self.check_button = QPushButton("Check")
        self.check_button.setObjectName("primary")
        self.check_button.setMinimumWidth(88)
        self.check_button.clicked.connect(self.run_check)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.target, 1)
        controls.addWidget(self.check_button)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        self.verdict = QLabel()
        self.verdict.setFont(theme.ui_font(16, QFont.Weight.Medium))
        self.verdict.setWordWrap(True)
        self.detail = caption("")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.meta = caption("")
        self.meta.setWordWrap(True)
        self.protocols = QLabel()
        self.protocols.setTextFormat(Qt.TextFormat.RichText)
        self.protocols.setWordWrap(True)

        self.chain_box = QVBoxLayout()
        self.chain_box.setSpacing(12)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(8)
        results_layout.addWidget(self.verdict)
        results_layout.addWidget(self.detail)
        results_layout.addWidget(self.meta)
        results_layout.addWidget(self.protocols)
        results_layout.addSpacing(8)
        results_layout.addLayout(self.chain_box)
        results_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(results)

        self.empty = caption(
            "Enter a server, then select Check to see its certificate and TLS settings."
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(scroll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(controls)
        layout.addWidget(self.error)
        layout.addWidget(self.body, 1)

    def _set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def run_check(self) -> None:
        try:
            host, port = parse_target(self.target.text())
        except TlsCheckError as error:
            self._set_error(str(error))
            return
        self._set_error("")
        self._request_id += 1
        request_id = self._request_id
        self.check_button.setEnabled(False)
        self.empty.setText(f"Checking {host}:{port}…")
        self.body.setCurrentIndex(0)

        def done(result: TlsResult) -> None:
            if request_id == self._request_id:
                self.check_button.setEnabled(True)
                self.show_result(result)

        def failed(error: Exception) -> None:
            if request_id == self._request_id:
                self.check_button.setEnabled(True)
                self.empty.setText(
                    "Enter a server, then select Check to see its certificate and TLS settings."
                )
                self._set_error(
                    str(error)
                    if isinstance(error, TlsCheckError)
                    else f"The check failed: {error}"
                )

        run_in_thread(lambda: check(host, port), done, failed)

    def show_result(self, result: TlsResult) -> None:
        t = theme.current()
        leaf = result.leaf
        problems = []
        if leaf.expired():
            problems.append(f"Certificate {expiry_text(leaf).lower()}")
        elif leaf.not_yet_valid():
            problems.append(expiry_text(leaf))
        if not result.name_matches:
            problems.append(f"Certificate is not for {result.host}")
        if leaf.self_signed:
            problems.append("Self-signed certificate")
        if not result.trusted and not problems:
            problems.append("Not trusted by this computer")

        if problems:
            verdict, color = " · ".join(problems), t.negative
        elif leaf.days_left() <= WARN_DAYS:
            verdict, color = f"Trusted · expires soon: {expiry_text(leaf)}", t.warning
        else:
            verdict, color = f"Trusted · {expiry_text(leaf)}", t.positive
        self.verdict.setText(verdict)
        self.verdict.setStyleSheet(f"color: {color};")
        self.detail.setText(
            f"This computer says: {result.trust_error}" if result.trust_error else ""
        )
        self.detail.setVisible(bool(result.trust_error))
        self.meta.setText(
            f"{result.tls_version} · {result.cipher} · {result.address}:{result.port} · {result.elapsed_ms:.0f} ms"
        )

        parts = []
        for label, accepted in result.protocols.items():
            if accepted is None:
                parts.append(
                    f'<span style="color:{t.text_muted}">{label} can\'t be tested here</span>'
                )
            elif accepted:
                tone = t.warning if label in OLD_PROTOCOLS else t.text
                note = " (outdated)" if label in OLD_PROTOCOLS else ""
                parts.append(
                    f'<span style="color:{tone}">{label} accepted{note}</span>'
                )
            else:
                parts.append(
                    f'<span style="color:{t.text_muted}">{label} refused</span>'
                )
        self.protocols.setText(" &nbsp;·&nbsp; ".join(parts))

        while self.chain_box.count():
            item = self.chain_box.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater alone leaves it visible until the next event loop pass.
                item.widget().deleteLater()
        for index, cert in enumerate(result.chain):
            self.chain_box.addWidget(
                cert_card(index, cert, len(result.chain), result.host)
            )
        self.body.setCurrentIndex(1)
