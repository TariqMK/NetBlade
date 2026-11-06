"""DNS lookup page: query one record type against the system resolver or a chosen nameserver."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from netblade.core.dns_lookup import (
    PRESET_NAMESERVERS,
    RECORD_TYPES,
    SYSTEM_DEFAULT,
    DnsRecord,
    LookupFailed,
    LookupResult,
    lookup,
    parse_nameserver,
    preset_label,
)
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread


class RecordModel(QAbstractTableModel):
    COLUMNS = ("Name", "Type", "TTL", "Value")
    NAME, TYPE, TTL, VALUE = range(4)

    def __init__(self) -> None:
        super().__init__()
        self.records: list[DnsRecord] = []

    def set_records(self, records: list[DnsRecord]) -> None:
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.COLUMNS[section]
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.TextAlignmentRole
        ):
            align = (
                Qt.AlignmentFlag.AlignRight
                if section == self.TTL
                else Qt.AlignmentFlag.AlignLeft
            )
            return int(align | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        record = self.records[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return [record.name, record.type, str(record.ttl), record.value][column]
        if role == Qt.ItemDataRole.FontRole and column in (self.TTL, self.VALUE):
            return theme.mono_font()
        if role == Qt.ItemDataRole.ForegroundRole and column == self.TYPE:
            return QColor(theme.current().text_secondary)
        if role == Qt.ItemDataRole.TextAlignmentRole and column == self.TTL:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class DnsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._request = 0

        self.name = QLineEdit()
        self.name.setPlaceholderText("example.com")
        self.name.setAccessibleName("Name to look up")
        self.name.returnPressed.connect(self.run_lookup)

        self.record_type = QComboBox()
        self.record_type.setAccessibleName("Record type")
        self.record_type.addItems(RECORD_TYPES)

        self.nameserver = QComboBox()
        self.nameserver.setEditable(True)
        self.nameserver.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.nameserver.setAccessibleName("Nameserver")
        self.nameserver.addItem(SYSTEM_DEFAULT)
        for ip, label in PRESET_NAMESERVERS:
            self.nameserver.addItem(preset_label(ip, label))
        self.nameserver.lineEdit().setPlaceholderText("Nameserver IP address")
        self.nameserver.lineEdit().returnPressed.connect(self.run_lookup)
        self.nameserver.lineEdit().textEdited.connect(
            lambda _text: self.error.setText("")
        )
        self.nameserver.setMinimumWidth(200)

        self.lookup_button = QPushButton("Look up")
        self.lookup_button.setObjectName("primary")
        self.lookup_button.clicked.connect(self.run_lookup)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.name, 1)
        controls.addWidget(self.record_type)
        controls.addWidget(self.nameserver)
        controls.addWidget(self.lookup_button)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)

        self.summary = QLabel()
        self.summary.setProperty("role", "secondary")
        self.model = RecordModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setAccessibleName("DNS records")
        header = self.table.horizontalHeader()
        header.setFont(theme.ui_font(theme.SMALL_PX))
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(RecordModel.NAME, 240)
        self.table.setColumnWidth(RecordModel.TYPE, 72)
        self.table.setColumnWidth(RecordModel.TTL, 72)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_selection)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addWidget(self.summary)
        results_layout.addWidget(self.table, 1)

        self.empty = QLabel(
            "Enter a name, choose a record type and nameserver, then select Look up."
        )
        self.empty.setProperty("role", "secondary")
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
        self.error.hide()

    def _set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def run_lookup(self) -> None:
        name = self.name.text().strip()
        record_type = self.record_type.currentText()
        if not name:
            self._set_error("Enter a name to look up.")
            self.name.setFocus()
            return
        try:
            nameserver = parse_nameserver(self.nameserver.currentText())
        except LookupFailed as error:
            self._set_error(str(error))
            self.nameserver.setFocus()
            return
        self._set_error("")
        self._request += 1
        request = self._request
        server_text = nameserver or "the system nameserver"
        self.summary.setText(f"Asking {server_text} for {record_type} records…")
        self.model.set_records([])
        self.body.setCurrentIndex(1)
        self.lookup_button.setEnabled(False)

        def done(result: LookupResult) -> None:
            if request != self._request:
                return
            self.lookup_button.setEnabled(True)
            self.model.set_records(result.records)
            if not result.records:
                self.summary.setText(
                    f"No {record_type} records for {name} from {result.nameserver}"
                )
                return
            count = len(result.records)
            word = "record" if count == 1 else "records"
            self.summary.setText(
                f"{count} {word} from {result.nameserver} in {result.elapsed_ms:.0f} ms"
            )

        def failed(error: Exception) -> None:
            if request != self._request:
                return
            self.lookup_button.setEnabled(True)
            message = (
                str(error)
                if isinstance(error, LookupFailed)
                else f"Lookup failed: {error}"
            )
            self.summary.setText("")
            self._set_error(message)
            self.body.setCurrentIndex(0)

        run_in_thread(lambda: lookup(name, record_type, nameserver), done, failed)

    def copy_selection(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()}
        )
        lines = [
            "\t".join([r.name, r.type, str(r.ttl), r.value])
            for r in (self.model.records[row] for row in rows)
        ]
        if lines:
            QApplication.clipboard().setText("\n".join(lines))
