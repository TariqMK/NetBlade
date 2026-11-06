"""Port scan page: a built-in TCP scanner that needs nothing installed, and a form for the installed nmap."""

from __future__ import annotations

from dataclasses import astuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from netblade.core import nmap
from netblade.core.portscan import (
    PORT_PRESETS,
    BuiltinScan,
    ScanInputError,
    ScanRow,
    parse_ports,
    parse_targets,
    rows_from_results,
)
from netblade.ui import tasks, theme
from netblade.ui.tasks import run_in_thread
from netblade.ui.widgets import caption, segmented

BUILTIN, NMAP = 0, 1
RESULTS_VIEW, OUTPUT_VIEW = 0, 1
TIMEOUTS = {"Timeout 0.5 s": 0.5, "Timeout 1 s": 1.0, "Timeout 2 s": 2.0}


class RowModel(QAbstractTableModel):
    COLUMNS = ("Host", "Name", "Port", "State", "Service", "Details")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[ScanRow] = []

    def set_rows(self, rows: list[ScanRow]) -> None:
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
        column = index.column()
        values = [row.host, row.name, row.port, row.state, row.service, row.detail]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return values[column]
        if role == Qt.ItemDataRole.FontRole and column in (0, 2):
            return theme.mono_font()
        if role == Qt.ItemDataRole.ForegroundRole and column == 3:
            t = theme.current()
            if row.state == "open":
                return QColor(t.positive)
            if row.state.startswith("filtered") or row.state == "closed":
                return QColor(t.text_muted)
            if row.state not in ("Up", "open"):
                return QColor(t.warning)
        return None


class _Progress(QObject):
    changed = Signal(int)


class ScanPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._job = None  # BuiltinScan or nmap.NmapRun while a scan runs.
        self._request_id = 0
        self._nmap_path: str | None = None
        self._progress = _Progress()
        self._progress.changed.connect(self._on_progress)

        mode_switch, self.mode_group = segmented(["Built-in scanner", "nmap"], self)
        self.mode_group.idClicked.connect(self._mode_changed)
        self.permission_note = caption(
            "Scan only networks you own or have permission to test"
        )
        top_row = QHBoxLayout()
        top_row.addWidget(mode_switch)
        top_row.addStretch()
        top_row.addWidget(self.permission_note)

        self.targets = QLineEdit()
        self.targets.setPlaceholderText(
            "192.168.1.0/24, 192.168.1.10-50 or server.example.com"
        )
        self.targets.setAccessibleName("Targets")
        self.targets.setFont(theme.mono_font(theme.BASE_PX))
        self.targets.returnPressed.connect(self.toggle)
        self.scan_button = QPushButton("Scan")
        self.scan_button.setObjectName("primary")
        self.scan_button.setMinimumWidth(88)
        self.scan_button.clicked.connect(self.toggle)
        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_row.addWidget(self.targets, 1)
        target_row.addWidget(self.scan_button)

        self.options = QStackedWidget()
        self.options.addWidget(self._builtin_options())
        self.options.addWidget(self._nmap_options())

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()

        # Results
        self.summary = caption("")
        self.summary.setWordWrap(True)
        view_switch, self.view_group = segmented(["Results", "nmap output"], self)
        self.view_group.idClicked.connect(lambda view: self.views.setCurrentIndex(view))
        self.view_switch = view_switch
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_results)
        view_row = QHBoxLayout()
        view_row.addWidget(view_switch)
        view_row.addWidget(self.summary, 1)
        view_row.addWidget(copy_button)
        self.model = RowModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName("Scan results")
        header = self.table.horizontalHeader()
        header.setFont(theme.ui_font(theme.SMALL_PX))
        header.setHighlightSections(False)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((136, 200, 88, 104, 120)):
            self.table.setColumnWidth(column, width)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_results)
        self.output = QPlainTextEdit()
        self.output.setObjectName("raw")
        self.output.setReadOnly(True)
        self.output.setFont(theme.mono_font())
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setAccessibleName("nmap output")
        self.views = QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.output)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addLayout(view_row)
        results_layout.addWidget(self.views, 1)
        self.empty = caption("Enter targets, choose options, then select Scan.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(results)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(top_row)
        layout.addLayout(target_row)
        layout.addWidget(self.options)
        layout.addWidget(self.error)
        layout.addWidget(self.body, 1)
        self._mode_changed(BUILTIN)

    # Option panels

    def _builtin_options(self) -> QWidget:
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.b_ports = QComboBox()
        self.b_ports.addItems(list(PORT_PRESETS))
        self.b_ports.setAccessibleName("Ports")
        self.b_custom = QLineEdit()
        self.b_custom.setPlaceholderText("22,80,443,8000-8100")
        self.b_custom.setAccessibleName("Custom ports")
        self.b_ports.currentTextChanged.connect(
            lambda text: self.b_custom.setVisible(text == "Custom ports")
        )
        self.b_custom.hide()
        self.b_timeout = QComboBox()
        self.b_timeout.addItems(list(TIMEOUTS))
        self.b_timeout.setCurrentIndex(1)
        self.b_timeout.setAccessibleName("Timeout")
        self.b_ping = QCheckBox("Ping hosts")
        self.b_ping.setChecked(True)
        self.b_ping.setToolTip(
            "Find hosts that answer ping even when no scanned port is open"
        )
        self.b_show_down = QCheckBox("Show hosts with no reply")
        row.addWidget(self.b_ports)
        row.addWidget(self.b_custom, 1)
        row.addWidget(self.b_timeout)
        row.addWidget(self.b_ping)
        row.addWidget(self.b_show_down)
        row.addStretch()
        return panel

    def _nmap_options(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        self.nmap_status = QLabel()
        self.nmap_status.setProperty("role", "secondary")
        self.nmap_status.setOpenExternalLinks(True)
        self.nmap_status.setTextFormat(Qt.TextFormat.RichText)
        self.nmap_status.setWordWrap(True)
        check_again = QPushButton("Check again")
        check_again.clicked.connect(self.detect_nmap)
        status_row = QHBoxLayout()
        status_row.addWidget(self.nmap_status, 1)
        status_row.addWidget(check_again)

        self.n_type = QComboBox()
        self.n_type.addItems(list(nmap.SCAN_TYPES))
        self.n_type.setAccessibleName("Scan type")
        self.n_ports = QComboBox()
        self.n_ports.addItems(list(nmap.PORT_CHOICES))
        self.n_ports.setAccessibleName("Ports")
        self.n_custom = QLineEdit()
        self.n_custom.setPlaceholderText("22,80,443,8000-8100")
        self.n_custom.setAccessibleName("Custom ports")
        self.n_custom.hide()
        self.n_ports.currentTextChanged.connect(
            lambda text: self.n_custom.setVisible(text == "Custom ports")
        )
        self.n_timing = QComboBox()
        self.n_timing.addItems(list(nmap.TIMINGS))
        self.n_timing.setCurrentText(nmap.DEFAULT_TIMING)
        self.n_timing.setAccessibleName("Timing")
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(self.n_type)
        row1.addWidget(self.n_ports)
        row1.addWidget(self.n_custom, 1)
        row1.addWidget(self.n_timing)
        row1.addStretch()

        self.n_version = QCheckBox("Service versions (-sV)")
        self.n_scripts = QCheckBox("Default scripts (-sC)")
        self.n_pn = QCheckBox("Skip host discovery (-Pn)")
        self.n_pn.setToolTip("Scan every target even if it doesn't answer ping")
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        for box in (self.n_version, self.n_scripts, self.n_pn):
            row2.addWidget(box)
        row2.addStretch()

        self.n_extra = QLineEdit()
        self.n_extra.setPlaceholderText(
            "More options: any other nmap options, for example --open"
        )
        self.n_extra.setAccessibleName("More options")
        self.n_extra.returnPressed.connect(self.toggle)
        suggestions = QPushButton("Suggestions")
        menu = QMenu(suggestions)
        for text, description in nmap.SUGGESTED_OPTIONS:
            action = QAction(f"{text}    {description}", menu)
            action.triggered.connect(
                lambda _checked=False, value=text: self._add_option(value)
            )
            menu.addAction(action)
        suggestions.setMenu(menu)
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(self.n_extra, 1)
        row3.addWidget(suggestions)

        self.command = QLabel()
        self.command.setFont(theme.mono_font())
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.command.setAccessibleName("nmap command")
        copy_command = QPushButton("Copy command")
        copy_command.clicked.connect(
            lambda: QApplication.clipboard().setText(self.command.text())
        )
        command_card = QFrame()
        command_card.setObjectName("card")
        command_layout = QHBoxLayout(command_card)
        command_layout.setContentsMargins(16, 8, 8, 8)
        command_layout.addWidget(self.command, 1)
        command_layout.addWidget(copy_command, 0, Qt.AlignmentFlag.AlignTop)

        column.addLayout(status_row)
        column.addLayout(row1)
        column.addLayout(row2)
        column.addLayout(row3)
        column.addWidget(command_card)

        for signal in (
            self.targets.textChanged,
            self.n_type.currentTextChanged,
            self.n_ports.currentTextChanged,
            self.n_custom.textChanged,
            self.n_timing.currentTextChanged,
            self.n_version.toggled,
            self.n_scripts.toggled,
            self.n_pn.toggled,
            self.n_extra.textChanged,
        ):
            signal.connect(self._update_command)
        return panel

    def _add_option(self, value: str) -> None:
        self.n_extra.setText(f"{self.n_extra.text().strip()} {value}".strip())
        self.n_extra.setFocus()

    def _nmap_request(self) -> nmap.NmapRequest:
        return nmap.NmapRequest(
            targets=self.targets.text(),
            scan_type=self.n_type.currentText(),
            ports=self.n_ports.currentText(),
            custom_ports=self.n_custom.text(),
            timing=self.n_timing.currentText(),
            version_detection=self.n_version.isChecked(),
            default_scripts=self.n_scripts.isChecked(),
            skip_discovery=self.n_pn.isChecked(),
            extra_args=self.n_extra.text(),
        )

    def _update_command(self, *_args) -> None:
        request = self._nmap_request()
        if not request.targets.strip():
            request.targets = "192.168.1.0/24"
        try:
            self.command.setText(nmap.display_command(nmap.build_args(request)))
        except ScanInputError as error:
            self.command.setText(str(error))

    def detect_nmap(self) -> None:
        self._nmap_path = nmap.find_nmap()
        if self._nmap_path is None:
            self.nmap_status.setText(
                f'nmap is not installed. Install it from <a href="{nmap.DOWNLOAD_URL}">nmap.org</a> '
                "(on Windows the installer includes Npcap), then select Check again. "
                "The built-in scanner works without it."
            )
            return
        path = self._nmap_path

        def found(version: str) -> None:
            self.nmap_status.setText(f"{version or 'nmap'} found at {path}")

        run_in_thread(lambda: nmap.nmap_version(path), found, lambda _error: found(""))

    def _mode_changed(self, mode: int) -> None:
        self.options.setCurrentIndex(mode)
        # A stacked widget is as tall as its tallest page; ignore the hidden page so no gap is left.
        for index in range(self.options.count()):
            policy = (
                QSizePolicy.Policy.Preferred
                if index == mode
                else QSizePolicy.Policy.Ignored
            )
            self.options.widget(index).setSizePolicy(policy, policy)
        self.options.adjustSize()
        self.view_switch.setVisible(mode == NMAP)
        if mode != NMAP:
            self.view_group.button(RESULTS_VIEW).setChecked(True)
            self.views.setCurrentIndex(RESULTS_VIEW)
        else:
            self._update_command()
            self.detect_nmap()
        self._set_error("")

    # Running

    def _set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def toggle(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.summary.setText("Cancelling…")
        elif self.mode_group.checkedId() == NMAP:
            self.start_nmap()
        else:
            self.start_builtin()

    def _begin(self, text: str) -> int:
        self._set_error("")
        self._request_id += 1
        self.scan_button.setText("Cancel")
        self.model.set_rows([])
        self.output.setPlainText("")
        self.summary.setText(text)
        self.body.setCurrentIndex(1)
        return self._request_id

    def _finish(self) -> None:
        self._job = None
        self.scan_button.setText("Scan")

    def start_builtin(self) -> None:
        try:
            targets = parse_targets(self.targets.text())
            preset = PORT_PRESETS[self.b_ports.currentText()]
            ports = preset if preset is not None else parse_ports(self.b_custom.text())
        except ScanInputError as error:
            self._set_error(str(error))
            return
        scan = BuiltinScan(
            targets,
            ports,
            TIMEOUTS[self.b_timeout.currentText()],
            self.b_ping.isChecked(),
        )
        self._job = scan
        host_word = "host" if len(targets) == 1 else "hosts"
        request_id = self._begin(
            f"Scanning {len(targets):,} {host_word}, {len(ports):,} ports each…"
        )
        self._total = scan.total_checks
        show_down = self.b_show_down.isChecked()

        def done(outcome) -> None:
            if request_id != self._request_id:
                return
            self._finish()
            results, seconds = outcome
            self.model.set_rows(rows_from_results(results, len(ports), show_down))
            up = sum(1 for r in results if r.up)
            open_ports = sum(len(r.open_ports) for r in results)
            prefix = "Cancelled. " if scan.cancelled else ""
            self.summary.setText(
                f"{prefix}{up} of {len(results):,} hosts up · {open_ports} open ports · {seconds:.1f} s"
            )

        def failed(error: Exception) -> None:
            if request_id == self._request_id:
                self._finish()
                self._set_error(f"The scan stopped: {error}")

        run_in_thread(
            lambda: scan.run(lambda n: tasks.report(self._progress.changed, n)),
            done,
            failed,
        )

    def _on_progress(self, done: int) -> None:
        if (
            self._job is not None
            and isinstance(self._job, BuiltinScan)
            and not self._job.cancelled
        ):
            self.summary.setText(f"Scanning… {done:,} of {self._total:,} checks done")

    def start_nmap(self) -> None:
        if self._nmap_path is None:
            self.detect_nmap()
            if self._nmap_path is None:
                self._set_error(
                    "nmap is not installed. Install it from nmap.org, or use the built-in scanner."
                )
                return
        try:
            args = nmap.build_args(self._nmap_request())
        except ScanInputError as error:
            self._set_error(str(error))
            return
        run = nmap.NmapRun(self._nmap_path, args)
        self._job = run
        request_id = self._begin(
            "nmap is scanning… Large networks and service detection can take minutes."
        )

        def done(result: nmap.NmapResult) -> None:
            if request_id != self._request_id:
                return
            self._finish()
            self.output.setPlainText(result.output)
            rows, summary = nmap.parse_xml(result.xml)
            self.model.set_rows(rows)
            if result.cancelled:
                self.summary.setText("Cancelled")
            elif result.exit_code != 0 and not rows:
                self.summary.setText("nmap reported an error. See nmap output.")
                self.view_group.button(OUTPUT_VIEW).setChecked(True)
                self.views.setCurrentIndex(OUTPUT_VIEW)
            else:
                self.summary.setText(summary or f"{len(rows)} rows")

        def failed(error: Exception) -> None:
            if request_id == self._request_id:
                self._finish()
                self._set_error(f"Can't run nmap: {error}")

        run_in_thread(run.run, done, failed)

    def copy_results(self) -> None:
        if self.views.currentIndex() == OUTPUT_VIEW:
            QApplication.clipboard().setText(self.output.toPlainText())
            return
        selected = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        rows = [self.model.rows[i] for i in selected] or self.model.rows
        lines = ["\t".join(RowModel.COLUMNS)]
        lines += ["\t".join(astuple(r)) for r in rows]
        QApplication.clipboard().setText("\n".join(lines))

    def shutdown(self) -> None:
        if self._job is not None:
            self._job.cancel()
