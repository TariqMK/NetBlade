"""MAC lookup page: vendor of one or many MAC addresses, from the IEEE registry kept on this computer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QStandardPaths, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from netblade.core.oui import (
    MacResult,
    OuiDatabase,
    OuiError,
    describe,
    download,
    load,
    parse_mac,
    split_input,
)
from netblade.ui import theme
from netblade.ui.tasks import run_in_thread
from netblade.ui.widgets import caption


def data_folder() -> Path:
    """Per-user folder for the IEEE lists. No admin rights needed."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    return Path(base or Path.home() / ".netblade") / "oui"


class ResultModel(QAbstractTableModel):
    COLUMNS = ("MAC address", "Vendor", "Notes", "Vendor address")

    def __init__(self) -> None:
        super().__init__()
        self.results: list[MacResult] = []

    def set_results(self, results: list[MacResult]) -> None:
        self.beginResetModel()
        self.results = results
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.results)

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
        r = self.results[index.row()]
        values = [r.mac or r.text, r.vendor or "—", r.notes, r.address]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return values[index.column()]
        if role == Qt.ItemDataRole.FontRole and index.column() == 0:
            return theme.mono_font()
        if role == Qt.ItemDataRole.ForegroundRole:
            t = theme.current()
            if not r.ok:
                return QColor(t.negative)
            if index.column() == 1 and not r.vendor:
                return QColor(t.text_muted)
            if index.column() in (2, 3):
                return QColor(t.text_secondary)
        return None


class MacPage(QWidget):
    def __init__(self, folder: Path | None = None) -> None:
        super().__init__()
        self.setObjectName("page")
        self.folder = folder or data_folder()
        self.database: OuiDatabase | None = None
        self._loaded = False

        self.input = QPlainTextEdit()
        self.input.setObjectName("raw")
        self.input.setPlaceholderText(
            "Paste one or more MAC addresses, one per line or separated by commas.\n"
            "Any format works: 00:15:5d:e4:24:53, 00-15-5D-E4-24-53, 0015.5de4.2453 or a prefix such as 00155D"
        )
        self.input.setFont(theme.mono_font())
        self.input.setFixedHeight(88)
        self.input.setAccessibleName("MAC addresses")
        QShortcut(QKeySequence("Ctrl+Return"), self.input, self.run_lookup)
        self.lookup_button = QPushButton("Look up")
        self.lookup_button.setObjectName("primary")
        self.lookup_button.setToolTip("Look up the vendors (Ctrl+Enter)")
        self.lookup_button.clicked.connect(self.run_lookup)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.input, 1)
        controls.addWidget(self.lookup_button, 0, Qt.AlignmentFlag.AlignTop)

        self.db_status = caption("")
        self.db_status.setWordWrap(True)
        self.download_button = QPushButton("Download vendor list")
        self.download_button.clicked.connect(self.download_database)
        db_row = QHBoxLayout()
        db_row.addWidget(self.db_status, 1)
        db_row.addWidget(self.download_button)

        self.formats = QLabel()
        self.formats.setFont(theme.mono_font())
        self.formats.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.formats.setProperty("role", "secondary")
        self.formats.hide()

        self.model = ResultModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName("Vendors")
        header = self.table.horizontalHeader()
        header.setFont(theme.ui_font(theme.SMALL_PX))
        header.setHighlightSections(False)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((170, 260, 300)):
            self.table.setColumnWidth(column, width)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_rows)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_rows)
        self.summary = caption("")
        table_header = QHBoxLayout()
        table_header.addWidget(self.summary, 1)
        table_header.addWidget(copy_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(controls)
        layout.addLayout(db_row)
        layout.addWidget(self.formats)
        layout.addLayout(table_header)
        layout.addWidget(self.table, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.load_database()

    # Vendor list

    def load_database(self) -> None:
        self.db_status.setText("Reading the vendor list…")

        def loaded(database: OuiDatabase | None) -> None:
            self.database = database
            self._show_database_status()

        run_in_thread(
            lambda: load(self.folder), loaded, lambda error: self._database_error(error)
        )

    def _show_database_status(self) -> None:
        if self.database is None:
            self.db_status.setText(
                "Vendor names need the IEEE vendor list (about 5 MB). Select Download vendor list once; "
                "after that, lookups work offline."
            )
            self.download_button.setText("Download vendor list")
        else:
            date = (
                f", downloaded {self.database.downloaded}"
                if self.database.downloaded
                else ""
            )
            self.db_status.setText(
                f"IEEE vendor list: {len(self.database):,} vendor blocks{date}"
            )
            self.download_button.setText("Update vendor list")

    def _database_error(self, error: Exception) -> None:
        self.download_button.setEnabled(True)
        self.db_status.setText(
            str(error)
            if isinstance(error, OuiError)
            else f"Can't read the vendor list: {error}"
        )

    def download_database(self) -> None:
        self.download_button.setEnabled(False)
        self.db_status.setText("Downloading the IEEE vendor list…")

        def done(database: OuiDatabase) -> None:
            self.download_button.setEnabled(True)
            self.database = database
            self._show_database_status()
            if self.model.results:
                self.run_lookup()

        run_in_thread(lambda: download(self.folder), done, self._database_error)

    # Lookup

    def run_lookup(self) -> None:
        items = split_input(self.input.toPlainText())
        results = [describe(item, self.database) for item in items]
        self.model.set_results(results)
        found = sum(1 for r in results if r.vendor)
        if not items:
            self.summary.setText("Enter at least one MAC address.")
        else:
            word = "address" if len(items) == 1 else "addresses"
            self.summary.setText(f"{len(items)} {word} · {found} with a vendor")
        self.formats.hide()
        if len(items) == 1 and results[0].ok:
            formats = parse_mac(items[0]).formats()
            self.formats.setText(
                "   ".join(f"{name}: {value}" for name, value in formats.items())
            )
            self.formats.show()

    def copy_rows(self) -> None:
        selected = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        rows = [self.model.results[i] for i in selected] or self.model.results
        lines = ["\t".join(ResultModel.COLUMNS)]
        lines += [
            "\t".join([r.mac or r.text, r.vendor, r.notes, r.address]) for r in rows
        ]
        QApplication.clipboard().setText("\n".join(lines))
