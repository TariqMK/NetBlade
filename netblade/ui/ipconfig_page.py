"""IP config page: runs `ipconfig /all` (or the macOS/Linux equivalent) and shows adapters or the raw output."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade.core.ipconfig import Adapter, IpConfig, parse_ipconfig_all, run_all
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread

ADAPTERS_VIEW, RAW_VIEW = 0, 1
CARD_MIN_WIDTH = 360

# (label shown, ipconfig keys whose values are listed)
CARD_ROWS = [
    ("IPv4 address", ["IPv4 Address", "IP Address"]),
    ("Subnet mask", ["Subnet Mask"]),
    ("Default gateway", ["Default Gateway"]),
    ("DNS servers", ["DNS Servers"]),
    ("DHCP server", ["DHCP Server"]),
    ("MAC address", ["Physical Address"]),
    (
        "IPv6 address",
        ["IPv6 Address", "Temporary IPv6 Address", "Link-local IPv6 Address"],
    ),
    ("DNS suffix", ["Connection-specific DNS Suffix"]),
]


def _label(text: str, role: str | None = None, font: QFont | None = None) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    if font:
        label.setFont(font)
    return label


def adapter_card(adapter: Adapter) -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(4)

    title_row = QHBoxLayout()
    title_row.setSpacing(8)
    title_row.addWidget(
        _label(adapter.name, font=theme.ui_font(14, QFont.Weight.Medium))
    )
    status = "Connected" if adapter.connected else "Disconnected"
    title_row.addStretch()
    title_row.addWidget(_label(status, "secondary" if adapter.connected else "muted"))
    layout.addLayout(title_row)
    description = adapter.first("Description") or adapter.kind
    subtitle = _label(description, "muted")
    subtitle.setWordWrap(True)
    layout.addWidget(subtitle)

    grid = QGridLayout()
    grid.setContentsMargins(0, 12, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(6)
    row = 0
    dhcp = adapter.first("DHCP Enabled")
    for label, keys in CARD_ROWS:
        values = [value for key in keys for value in adapter.get(key) if value]
        if not values:
            continue
        name = _label(label, "secondary")
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        value = _label("\n".join(values), font=theme.mono_font())
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(name, row, 0)
        grid.addWidget(value, row, 1)
        row += 1
    if dhcp:
        grid.addWidget(_label("DHCP", "secondary"), row, 0)
        grid.addWidget(
            _label("Enabled" if dhcp.lower() == "yes" else "Disabled"), row, 1
        )
        row += 1
    grid.setColumnStretch(1, 1)
    layout.addLayout(grid)
    layout.addStretch()  # Keep content at the top when a neighbouring card in the row is taller.
    return card


class IpConfigPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._config: IpConfig | None = None
        self._loaded = False
        self.structured = sys.platform == "win32"

        segmented = QWidget()
        segmented.setObjectName("segmented")
        segmented.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        seg_layout = QHBoxLayout(segmented)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)
        self.view_group = QButtonGroup(self)
        for view_id, text in ((ADAPTERS_VIEW, "Adapters"), (RAW_VIEW, "Raw output")):
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            self.view_group.addButton(button, view_id)
            seg_layout.addWidget(button)
        self.view_group.button(ADAPTERS_VIEW).setChecked(True)
        self.view_group.idClicked.connect(self._set_view)
        segmented.setVisible(self.structured)

        self.show_disconnected = QCheckBox("Show disconnected adapters")
        self.show_disconnected.toggled.connect(self._render_cards)
        self.show_disconnected.setVisible(self.structured)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_output)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addWidget(segmented)
        controls.addWidget(self.show_disconnected)
        controls.addStretch()
        controls.addWidget(self.copy_button)
        controls.addWidget(self.refresh_button)

        self.host = _label("", "secondary")
        self.status = _label("", "muted")

        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(16)
        self.cards_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.cards_host)

        self.raw = QPlainTextEdit()
        self.raw.setObjectName("raw")
        self.raw.setReadOnly(True)
        self.raw.setFont(theme.mono_font())
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw.setAccessibleName("Raw output")

        self.views = QStackedWidget()
        self.views.addWidget(scroll)
        self.views.addWidget(self.raw)
        self.views.setCurrentIndex(ADAPTERS_VIEW if self.structured else RAW_VIEW)

        header = QHBoxLayout()
        header.addWidget(self.host)
        header.addStretch()
        header.addWidget(self.status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(controls)
        layout.addLayout(header)
        layout.addWidget(self.views, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._config is not None and self._columns() != getattr(
            self, "_rendered_columns", None
        ):
            self._render_cards()

    def _columns(self) -> int:
        return max(1, self.views.width() // CARD_MIN_WIDTH)

    def _set_view(self, view_id: int) -> None:
        self.views.setCurrentIndex(view_id)
        self.show_disconnected.setVisible(self.structured and view_id == ADAPTERS_VIEW)

    def refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.status.setText("Reading configuration…")
        run_in_thread(run_all, self._on_output, self._on_error)

    def _on_error(self, error: Exception) -> None:
        self.refresh_button.setEnabled(True)
        self.status.setText(f"Can't read the configuration: {error}")

    def _on_output(self, output: str) -> None:
        self.refresh_button.setEnabled(True)
        self.raw.setPlainText(output)
        self.status.setText("")
        if not self.structured:
            return
        self._config = parse_ipconfig_all(output)
        host = self._config.host.get("Host Name", [""])
        self.host.setText(f"Host name  {host[0]}" if host and host[0] else "")
        if not self._config.adapters:
            # Non-English ipconfig output can't be parsed into adapters; the raw text is still complete.
            self.status.setText(
                "Adapter view needs English ipconfig output. Showing raw output."
            )
            self.view_group.button(RAW_VIEW).setChecked(True)
            self._set_view(RAW_VIEW)
        self._render_cards()

    def _render_cards(self) -> None:
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self._config is None:
            return
        adapters = [
            a
            for a in self._config.adapters
            if a.connected or self.show_disconnected.isChecked()
        ]
        hidden = len(self._config.adapters) - len(adapters)
        columns = self._columns()
        self._rendered_columns = columns
        for i, adapter in enumerate(adapters):
            self.cards_grid.addWidget(adapter_card(adapter), i // columns, i % columns)
        for column in range(max(columns, self.cards_grid.columnCount())):
            self.cards_grid.setColumnStretch(column, 1 if column < columns else 0)
        if not adapters:
            self.cards_grid.addWidget(
                _label("No connected adapters.", "secondary"), 0, 0
            )
        if hidden and not self.show_disconnected.isChecked():
            word = "adapter" if hidden == 1 else "adapters"
            self.status.setText(f"{hidden} disconnected {word} hidden")
        elif self.status.text().endswith("hidden"):
            self.status.setText("")

    def copy_output(self) -> None:
        QApplication.clipboard().setText(self.raw.toPlainText())
        self.status.setText("Copied the raw output")
