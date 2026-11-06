"""Whois / IP owner page: who owns an IP address, network, AS number or domain, using RDAP."""

from __future__ import annotations

import datetime as dt
import json

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from netblade.core.rdap import (
    Contact,
    RdapError,
    RdapResult,
    lookup,
    parse_query,
    public_ip,
)
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread
from netblade.ui.widgets import caption, segmented

SUMMARY_VIEW, RAW_VIEW = 0, 1


class ContactModel(QAbstractTableModel):
    COLUMNS = ("Role", "Name", "Organisation", "E-mail", "Phone")

    def __init__(self) -> None:
        super().__init__()
        self.contacts: list[Contact] = []

    def set_contacts(self, contacts: list[Contact]) -> None:
        self.beginResetModel()
        self.contacts = contacts
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.contacts)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c = self.contacts[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return [
                ", ".join(c.roles).capitalize(),
                c.name,
                c.organisation,
                c.email,
                c.phone,
            ][index.column()]
        if role == Qt.ItemDataRole.FontRole and index.column() in (3, 4):
            return theme.mono_font()
        return None


class WhoisPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._request_id = 0
        self._result: RdapResult | None = None

        self.query = QLineEdit()
        self.query.setPlaceholderText(
            "8.8.8.8, 2001:db8::1, 185.199.108.0/22, example.com or AS13335"
        )
        self.query.setAccessibleName("IP address, domain or AS number")
        self.query.returnPressed.connect(self.run_lookup)
        self.lookup_button = QPushButton("Look up")
        self.lookup_button.setObjectName("primary")
        self.lookup_button.clicked.connect(self.run_lookup)
        my_ip = QPushButton("My public IP")
        my_ip.setToolTip(
            "Find this network's public IP address (from ipify.org) and look it up"
        )
        my_ip.clicked.connect(self.lookup_public_ip)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.query, 1)
        controls.addWidget(my_ip)
        controls.addWidget(self.lookup_button)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        view_switch, self.view_group = segmented(["Summary", "Raw RDAP"], self)
        self.view_group.idClicked.connect(lambda view: self.views.setCurrentIndex(view))
        self.title = QLabel()
        self.title.setFont(theme.ui_font(16, QFont.Weight.Medium))
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_view)
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(view_switch)
        header.addWidget(copy_button)

        # Summary: a details card and the contacts table.
        self.details_card = QFrame()
        self.details_card.setObjectName("card")
        card_layout = QVBoxLayout(self.details_card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        self.details_grid = QGridLayout()
        self.details_grid.setHorizontalSpacing(24)
        self.details_grid.setVerticalSpacing(6)
        self.details_grid.setColumnStretch(1, 1)
        card_layout.addLayout(self.details_grid)
        self.contacts_title = caption("Contacts")
        self.contact_model = ContactModel()
        self.contacts = QTableView()
        self.contacts.setModel(self.contact_model)
        self.contacts.setShowGrid(False)
        self.contacts.setWordWrap(False)
        self.contacts.verticalHeader().hide()
        self.contacts.verticalHeader().setDefaultSectionSize(32)
        self.contacts.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.contacts.setMinimumHeight(200)
        self.contacts.setAccessibleName("Contacts")
        contacts_header = self.contacts.horizontalHeader()
        contacts_header.setFont(theme.ui_font(theme.SMALL_PX))
        contacts_header.setHighlightSections(False)
        contacts_header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        contacts_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(12)
        summary_layout.addWidget(self.details_card)
        summary_layout.addWidget(self.contacts_title)
        summary_layout.addWidget(self.contacts, 1)
        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.Shape.NoFrame)
        summary_scroll.setWidget(summary)

        self.raw = QPlainTextEdit()
        self.raw.setObjectName("raw")
        self.raw.setReadOnly(True)
        self.raw.setFont(theme.mono_font())
        self.raw.setAccessibleName("Raw RDAP answer")
        self.views = QStackedWidget()
        self.views.addWidget(summary_scroll)
        self.views.addWidget(self.raw)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addLayout(header)
        results_layout.addWidget(self.views, 1)

        self.empty = caption(
            "Enter an IP address, network, domain or AS number, then select Look up. "
            "NetBlade asks the registry that holds the record."
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(results)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(controls)
        layout.addWidget(self.error)
        layout.addWidget(self.body, 1)

    def _set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def _busy(self, text: str) -> int:
        self._set_error("")
        self._request_id += 1
        self.lookup_button.setEnabled(False)
        self.empty.setText(text)
        self.body.setCurrentIndex(0)
        return self._request_id

    def _failed(self, request_id: int, error: Exception) -> None:
        if request_id != self._request_id:
            return
        self.lookup_button.setEnabled(True)
        self.empty.setText(
            "Enter an IP address, network, domain or AS number, then select Look up."
        )
        self._set_error(
            str(error)
            if isinstance(error, RdapError)
            else f"The lookup failed: {error}"
        )

    def run_lookup(self) -> None:
        try:
            query = parse_query(self.query.text())
        except RdapError as error:
            self._set_error(str(error))
            return
        request_id = self._busy(f"Looking up {query.display}…")

        def done(result: RdapResult) -> None:
            if request_id == self._request_id:
                self.lookup_button.setEnabled(True)
                self.show_result(result)

        run_in_thread(
            lambda: lookup(query), done, lambda error: self._failed(request_id, error)
        )

    def lookup_public_ip(self) -> None:
        request_id = self._busy("Finding this network's public IP address…")

        def found(address: str) -> None:
            if request_id == self._request_id:
                self.lookup_button.setEnabled(True)
                self.query.setText(address)
                self.run_lookup()

        run_in_thread(public_ip, found, lambda error: self._failed(request_id, error))

    def show_result(self, result: RdapResult) -> None:
        self._result = result
        t = theme.current()
        self.title.setText(result.query.display)
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for row, (label, value) in enumerate(result.rows):
            name = caption(label)
            text = QLabel(value)
            text.setFont(theme.mono_font())
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if label == "Expires" and result.expires is not None:
                days = (result.expires - dt.datetime.now(dt.UTC)).days
                text.setText(
                    f"{value} ({days} days left)" if days >= 0 else f"{value} (expired)"
                )
                if days < 30:
                    text.setStyleSheet(
                        f"color: {t.negative if days < 0 else t.warning};"
                    )
            self.details_grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            self.details_grid.addWidget(text, row, 1)
        self.contact_model.set_contacts(result.contacts)
        has_contacts = bool(result.contacts)
        self.contacts_title.setVisible(has_contacts)
        self.contacts.setVisible(has_contacts)
        self.raw.setPlainText(json.dumps(result.raw, indent=2, ensure_ascii=False))
        self.body.setCurrentIndex(1)

    def copy_view(self) -> None:
        if self._result is None:
            return
        if self.views.currentIndex() == RAW_VIEW:
            QApplication.clipboard().setText(self.raw.toPlainText())
        else:
            QApplication.clipboard().setText(
                "\n".join(f"{label}: {value}" for label, value in self._result.rows)
            )
