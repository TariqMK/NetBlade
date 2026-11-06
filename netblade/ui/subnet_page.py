"""Subnet calculator page: details, binary view and subnet split for an IPv4 or IPv6 network."""

from __future__ import annotations

import html
import ipaddress

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QComboBox,
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

from netblade.core.subnet import (
    MAX_SPLIT_ROWS,
    SplitRow,
    SubnetInputError,
    binary_groups,
    details,
    format_count,
    parse,
    split,
    split_options,
    summarise,
)
from netblade.ui import theme
from netblade.ui.widgets import segmented


class SplitModel(QAbstractTableModel):
    COLUMNS = ("Subnet", "First usable", "Last usable", "Broadcast", "Hosts")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[SplitRow] = []

    def set_rows(self, rows: list[SplitRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

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
        row = self.rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            values = [row.network, row.first, row.last, row.broadcast, row.hosts]
            return values[index.column()]
        if role == Qt.ItemDataRole.FontRole:
            return theme.mono_font()
        return None


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    return card, layout


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.ui_font(14, QFont.Weight.Medium))
    return label


class SubnetPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._interface = None

        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "192.168.1.10/24, 10.0.0.1 255.255.255.0 or 2001:db8::1/64"
        )
        self.input.setAccessibleName("Address and prefix")
        self.input.setFont(theme.mono_font(theme.BASE_PX))
        self.input.textChanged.connect(self.calculate)
        self.prefix = QComboBox()
        self.prefix.setAccessibleName("Prefix length")
        self.prefix.setMinimumWidth(200)
        self.prefix.activated.connect(self._prefix_chosen)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.input, 1)
        controls.addWidget(self.prefix)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        # Details card
        details_card, details_layout = _card()
        details_layout.addWidget(_heading("Details"))
        self.details_grid = QGridLayout()
        self.details_grid.setHorizontalSpacing(24)
        self.details_grid.setVerticalSpacing(6)
        self.details_grid.setColumnStretch(1, 1)
        details_layout.addLayout(self.details_grid)
        details_layout.addStretch()

        # Binary card
        binary_card, binary_layout = _card()
        binary_layout.addWidget(_heading("Binary"))
        self.binary = QLabel()
        self.binary.setTextFormat(Qt.TextFormat.RichText)
        self.binary.setFont(theme.mono_font())
        self.binary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.binary.setAccessibleName("Binary view")
        binary_layout.addWidget(self.binary)
        binary_layout.addStretch()

        self.cards = cards = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        cards.setSpacing(16)
        cards.addWidget(details_card, 1)
        cards.addWidget(binary_card, 1)

        # Split
        self.split_choice = QComboBox()
        self.split_choice.setAccessibleName("Split into")
        self.split_choice.currentIndexChanged.connect(self._split_changed)
        self.split_note = QLabel()
        self.split_note.setProperty("role", "secondary")
        split_header = QHBoxLayout()
        split_header.setSpacing(8)
        split_header.addWidget(_heading("Split into"))
        split_header.addWidget(self.split_choice)
        split_header.addWidget(self.split_note, 1)
        self.split_model = SplitModel()
        self.split_table = QTableView()
        self.split_table.setModel(self.split_model)
        self.split_table.setShowGrid(False)
        self.split_table.verticalHeader().hide()
        self.split_table.verticalHeader().setDefaultSectionSize(32)
        self.split_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.split_table.setMinimumHeight(240)
        self.split_table.setAccessibleName("Subnets")
        header = self.split_table.horizontalHeader()
        header.setFont(theme.ui_font(theme.SMALL_PX))
        header.setHighlightSections(False)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(16)
        results_layout.addLayout(cards)
        results_layout.addLayout(split_header)
        results_layout.addWidget(self.split_table, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(results)

        self.empty = QLabel(
            "Enter an IPv4 or IPv6 address with a prefix length or mask."
        )
        self.empty.setProperty("role", "secondary")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(scroll)

        calculate = QWidget()
        calculate_layout = QVBoxLayout(calculate)
        calculate_layout.setContentsMargins(0, 0, 0, 0)
        calculate_layout.setSpacing(16)
        calculate_layout.addLayout(controls)
        calculate_layout.addWidget(self.error)
        calculate_layout.addWidget(self.body, 1)

        mode_switch, self.mode_group = segmented(
            ["Calculate", "Summarise routes"], self
        )
        self.modes = QStackedWidget()
        self.modes.addWidget(calculate)
        self.modes.addWidget(self._summarise_panel())
        self.mode_group.idClicked.connect(self.modes.setCurrentIndex)
        mode_row = QHBoxLayout()
        mode_row.addWidget(mode_switch)
        mode_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(mode_row)
        layout.addWidget(self.modes, 1)
        self._fill_prefixes(4)

    # Summarise routes

    def _summarise_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.routes = QPlainTextEdit()
        self.routes.setObjectName("raw")
        self.routes.setPlaceholderText(
            "Paste networks or addresses, one per line or separated by commas.\n"
            "10.1.0.0/24\n10.1.1.0/24\n10.1.2.0/24\n10.1.3.0/24"
        )
        self.routes.setFont(theme.mono_font())
        self.routes.setFixedHeight(140)
        self.routes.setAccessibleName("Networks to summarise")
        self.routes.textChanged.connect(self.summarise_routes)
        self.summary_notes = QLabel()
        self.summary_notes.setProperty("role", "secondary")
        self.summary_notes.setWordWrap(True)
        self.summary_text = QPlainTextEdit()
        self.summary_text.setObjectName("raw")
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(theme.mono_font())
        self.summary_text.setAccessibleName("Summary")
        copy_button = QPushButton("Copy summary")
        copy_button.clicked.connect(self._copy_summary)
        header = QHBoxLayout()
        header.addWidget(_heading("Summary"))
        header.addStretch()
        header.addWidget(copy_button)
        layout.addWidget(self.routes)
        layout.addWidget(self.summary_notes)
        layout.addLayout(header)
        layout.addWidget(self.summary_text, 1)
        self._summary_networks: list[str] = []
        return panel

    def summarise_routes(self) -> None:
        summaries, problems = summarise(self.routes.toPlainText())
        lines: list[str] = []
        self._summary_networks = []
        for summary in summaries:
            label = "IPv4" if summary.version == 4 else "IPv6"
            count = len(summary.networks)
            lines.append(
                f"{label}: exact summary, {count} route{'s' if count != 1 else ''}"
            )
            lines += [f"  {network.compressed}" for network in summary.networks]
            self._summary_networks += [
                network.compressed for network in summary.networks
            ]
            lines.append("")
            lines.append(f"{label}: single covering route")
            lines.append(f"  {summary.supernet.compressed}")
            if summary.extra_addresses:
                share = summary.extra_addresses / summary.supernet.num_addresses
                lines.append(
                    f"  Also covers {format_count(summary.extra_addresses)} addresses that are not in your list "
                    f"({share:.0%} of the route)."
                )
                if share > 0.5:
                    lines.append(
                        "  Caution: most of this route is outside your list. Use the exact summary instead."
                    )
            else:
                lines.append("  Covers exactly your list.")
            lines.append("")
        self.summary_text.setPlainText("\n".join(lines).rstrip())
        self.summary_notes.setText(" ".join(problems))
        self.summary_notes.setVisible(bool(problems))

    def _copy_summary(self) -> None:
        QApplication.clipboard().setText("\n".join(self._summary_networks))

    # Input

    def _fill_prefixes(self, version: int) -> None:
        if self.prefix.property("version") == version:
            return
        self.prefix.setProperty("version", version)
        self.prefix.clear()
        if version == 4:
            for length in range(32, -1, -1):
                mask = ipaddress.IPv4Network(f"0.0.0.0/{length}").netmask
                self.prefix.addItem(f"/{length}  {mask}", length)
        else:
            for length in range(128, -1, -1):
                count = 2 ** (128 - length)
                size = f"{count:,}" if count <= 2**32 else f"2^{128 - length}"
                self.prefix.addItem(f"/{length}  {size} addresses", length)

    def _prefix_chosen(self, index: int) -> None:
        length = self.prefix.itemData(index)
        address = self.input.text().strip().replace("/", " ").split()
        if address:
            self.input.setText(f"{address[0]}/{length}")

    def calculate(self) -> None:
        text = self.input.text()
        if not text.strip():
            self._show_error("")
            self.body.setCurrentIndex(0)
            return
        try:
            interface = parse(text)
        except SubnetInputError as error:
            self._show_error(str(error))
            self.body.setCurrentIndex(
                0
            )  # Never show results for other input under an error.
            return
        self._show_error("")
        self._interface = interface
        self._fill_prefixes(interface.version)
        self.prefix.setCurrentIndex(self.prefix.findData(interface.network.prefixlen))
        # IPv6 values are too wide for side-by-side cards, so stack them.
        vertical = interface.version == 6
        self.cards.setDirection(
            QBoxLayout.Direction.TopToBottom
            if vertical
            else QBoxLayout.Direction.LeftToRight
        )
        self._show_details(interface)
        self._show_binary(interface)
        self._fill_split_choices(interface.network)
        self.body.setCurrentIndex(1)

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # Results

    def _show_details(self, interface) -> None:
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater alone leaves it visible until the next event loop pass.
                item.widget().deleteLater()
        for row_index, row in enumerate(details(interface)):
            label = QLabel(row.label)
            label.setProperty("role", "secondary")
            value = QLabel(row.value)
            value.setFont(theme.mono_font())
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.details_grid.addWidget(label, row_index, 0, Qt.AlignmentFlag.AlignTop)
            self.details_grid.addWidget(value, row_index, 1)

    def _show_binary(self, interface) -> None:
        t = theme.current()
        prefix = interface.network.prefixlen
        rows = [
            ("Address", interface.ip),
            ("Network", interface.network.network_address),
        ]
        if interface.version == 4:
            rows.insert(1, ("Netmask", interface.network.netmask))
        per_line = 4  # IPv4: one line of 4 octets. IPv6: two lines of 4 groups.
        lines = []
        for label, address in rows:
            groups = binary_groups(address)
            size = len(groups[0])
            marked = []
            for g_index, group in enumerate(groups):
                start = g_index * size
                net = max(0, min(size, prefix - start))
                marked.append(
                    f'<span style="color:{t.accent}">{group[:net]}</span>'
                    f'<span style="color:{t.text_muted}">{group[net:]}</span>'
                )
            separator = "." if interface.version == 4 else ":"
            body = "<br>".join(
                separator.join(marked[i : i + per_line])
                for i in range(0, len(marked), per_line)
            )
            lines.append(
                f'<div style="color:{t.text_secondary}; margin-top:6px">{html.escape(label)}</div><div>{body}</div>'
            )
        legend = (
            f'<div style="margin-top:12px"><span style="color:{t.accent}">■ Network bits ({prefix})</span>'
            f'&nbsp;&nbsp;<span style="color:{t.text_muted}">■ Host bits ({interface.max_prefixlen - prefix})</span></div>'
        )
        self.binary.setText("".join(lines) + legend)
        self.binary.setAccessibleDescription(
            f"{prefix} network bits and {interface.max_prefixlen - prefix} host bits"
        )

    def _fill_split_choices(self, network) -> None:
        self.split_choice.blockSignals(True)
        self.split_choice.clear()
        for length in split_options(network):
            count = 2 ** (length - network.prefixlen)
            size = 2 ** (network.max_prefixlen - length)
            self.split_choice.addItem(
                f"/{length} · {count:,} subnets of {format_count(size)}", length
            )
        self.split_choice.blockSignals(False)
        has_options = self.split_choice.count() > 0
        self.split_choice.setVisible(has_options)
        self.split_table.setVisible(has_options)
        if has_options:
            self.split_choice.setCurrentIndex(min(1, self.split_choice.count() - 1))
            self._split_changed()
        else:
            self.split_note.setText("A single address can't be split")

    def _split_changed(self, *_args) -> None:
        if self._interface is None or self.split_choice.currentIndex() < 0:
            return
        rows, total = split(self._interface.network, self.split_choice.currentData())
        self.split_model.set_rows(rows)
        if total > MAX_SPLIT_ROWS:
            self.split_note.setText(
                f"Showing the first {MAX_SPLIT_ROWS:,} of {total:,} subnets"
            )
        else:
            self.split_note.setText("")

    def apply_theme(self) -> None:
        if self._interface is not None:
            self._show_binary(self._interface)
