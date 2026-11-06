"""Ping and trace page: a continuous traceroute with per-hop loss, latency range bars and a latency chart."""

from __future__ import annotations

import copy
import math
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
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
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from netblade.core.mtr import HISTORY_LENGTH, MAX_HOPS, HopStats, MtrSession
from netblade.core.probe import probe, resolve_ipv4
from netblade.ui import theme

INTERVALS = [
    ("Every 0.5 s", 0.5),
    ("Every 1 s", 1.0),
    ("Every 2 s", 2.0),
    ("Every 5 s", 5.0),
]
DEFAULT_INTERVAL_INDEX = 1


def _reverse_name(address: str) -> str | None:
    try:
        return socket.gethostbyaddr(address)[0]
    except OSError:
        return None


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


class TraceWorker(QObject):
    """Runs an MTR session on a background thread and sends a snapshot after every cycle."""

    resolved = Signal(str)
    updated = Signal(object, object)  # list[HopStats], destination ttl or None
    failed = Signal(str)

    def __init__(self, target: str, interval_s: float) -> None:
        super().__init__()
        self.target = target
        self.interval_s = interval_s
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            ip = resolve_ipv4(self.target)
        except (OSError, UnicodeError):
            self.failed.emit(
                f"Can't find {self.target}. Check the name or IP address and try again."
            )
            return
        self.resolved.emit(ip)

        session = MtrSession(ip)
        names: dict[str, Future] = {}
        with ThreadPoolExecutor(MAX_HOPS) as probes, ThreadPoolExecutor(4) as lookups:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    session.run_cycle(probe, probes.map)
                except OSError as error:
                    self.failed.emit(f"Can't send ICMP probes on this system: {error}")
                    return
                for hop in session.hops.values():
                    if hop.address and hop.address not in names:
                        names[hop.address] = lookups.submit(_reverse_name, hop.address)
                    future = names.get(hop.address) if hop.address else None
                    if future is not None and future.done():
                        hop.hostname = future.result()
                if self._stop.is_set():
                    return
                self.updated.emit(
                    copy.deepcopy(session.visible_hops()), session.destination_ttl
                )
                self._stop.wait(
                    max(0.0, self.interval_s - (time.monotonic() - started))
                )


class HopModel(QAbstractTableModel):
    COLUMNS = ("#", "", "Host", "Loss", "Sent", "Last", "Avg", "Best", "Worst", "Range")
    TTL, ROUTE, HOST, LOSS, SENT, LAST, AVG, BEST, WORST, RANGE = range(10)
    NUMERIC = frozenset({TTL, LOSS, SENT, LAST, AVG, BEST, WORST})
    HopRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self) -> None:
        super().__init__()
        self.hops: list[HopStats] = []
        self.range_max_ms = 10.0

    def set_hops(self, hops: list[HopStats]) -> None:
        if len(hops) != len(self.hops):
            self.beginResetModel()
            self.hops = hops
            self.endResetModel()
        else:
            self.hops = hops
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(hops) - 1, len(self.COLUMNS) - 1)
            )
        worst = max((hop.worst_ms or 0.0 for hop in hops), default=0.0)
        self.range_max_ms = _nice_ceiling(max(worst, 1.0))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.hops)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if section in self.NUMERIC and section != self.TTL:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        hop = self.hops[index.row()]
        column = index.column()
        t = theme.current()
        if role == self.HopRole:
            return hop
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(hop, column)
        if role == Qt.ItemDataRole.FontRole and column in self.NUMERIC:
            return theme.mono_font()
        if (
            role == Qt.ItemDataRole.TextAlignmentRole
            and column in self.NUMERIC
            and column != self.TTL
        ):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if column == self.TTL or not hop.responded:
                return QColor(t.text_muted)
            if column == self.LOSS and hop.loss_percent > 0:
                return QColor(theme.loss_color(hop.loss_percent, True))
        if (
            role == Qt.ItemDataRole.ToolTipRole
            and column == self.RANGE
            and hop.responded
        ):
            return f"Best {_ms(hop.best_ms)} ms · Avg {_ms(hop.avg_ms)} ms · Worst {_ms(hop.worst_ms)} ms"
        return None

    def _text(self, hop: HopStats, column: int) -> str:
        if column == self.TTL:
            return str(hop.ttl)
        if column == self.SENT:
            return str(hop.sent)
        if not hop.responded:
            return "—" if column in self.NUMERIC else ""
        values = {
            self.LOSS: f"{hop.loss_percent:.0f}%",
            self.LAST: _ms(hop.last_ms),
            self.AVG: _ms(hop.avg_ms),
            self.BEST: _ms(hop.best_ms),
            self.WORST: _ms(hop.worst_ms),
        }
        return values.get(column, "")


def _nice_ceiling(value: float) -> float:
    """Round up to 1, 2 or 5 times a power of ten, for chart and range-bar scales."""
    exponent = math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        candidate = step * 10**exponent
        if candidate >= value:
            return float(candidate)
    return float(10 ** (exponent + 1))


class HopDelegate(QStyledItemDelegate):
    """Draws the route line, the two-tone host cell and the latency range bar."""

    DOT = 9

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        column = index.column()
        if column not in (HopModel.ROUTE, HopModel.HOST, HopModel.RANGE):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        hop: HopStats = index.data(HopModel.HopRole)
        model: HopModel = index.model()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if column == HopModel.ROUTE:
            self._paint_route(painter, option.rect, hop, index.row(), model.rowCount())
        elif column == HopModel.HOST:
            self._paint_host(painter, option.rect, hop)
        else:
            self._paint_range(painter, option.rect, hop, model.range_max_ms)
        painter.restore()

    def _paint_route(
        self, painter: QPainter, rect, hop: HopStats, row: int, rows: int
    ) -> None:
        t = theme.current()
        center = QPointF(rect.center().x() + 0.5, rect.center().y() + 0.5)
        painter.setPen(QPen(t.hairline_strong, 1))
        top = rect.top() if row > 0 else center.y()
        bottom = rect.bottom() + 1 if row < rows - 1 else center.y()
        painter.drawLine(QPointF(center.x(), top), QPointF(center.x(), bottom))
        painter.setPen(QPen(QColor(t.page), 2))
        painter.setBrush(QColor(theme.loss_color(hop.loss_percent, hop.responded)))
        painter.drawEllipse(center, self.DOT / 2, self.DOT / 2)

    def _paint_host(self, painter: QPainter, rect, hop: HopStats) -> None:
        t = theme.current()
        area = rect.adjusted(8, 0, -8, 0)
        if not hop.responded:
            painter.setFont(theme.ui_font())
            painter.setPen(QColor(t.text_muted))
            painter.drawText(
                area,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "No reply",
            )
            return
        name = hop.hostname if hop.hostname and hop.hostname != hop.address else None
        x = area.left()
        if name:
            font = theme.ui_font()
            painter.setFont(font)
            painter.setPen(QColor(t.text))
            elided = painter.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, int(area.width() * 0.6)
            )
            painter.drawText(
                area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided
            )
            x += painter.fontMetrics().horizontalAdvance(elided) + 8
        painter.setFont(theme.mono_font())
        painter.setPen(QColor(t.text_muted if name else t.text))
        ip_rect = area.adjusted(x - area.left(), 0, 0, 0)
        painter.drawText(
            ip_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            hop.address or "",
        )

    def _paint_range(
        self, painter: QPainter, rect, hop: HopStats, scale_ms: float
    ) -> None:
        if not hop.responded:
            return
        t = theme.current()
        track = QRectF(rect.left() + 8, rect.center().y() - 2, rect.width() - 16, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.hairline)
        painter.drawRoundedRect(track, 3, 3)

        def x_for(value: float) -> float:
            return track.left() + track.width() * min(value / scale_ms, 1.0)

        start, end = x_for(hop.best_ms or 0.0), x_for(hop.worst_ms or 0.0)
        painter.setBrush(QColor(t.text_muted))
        painter.drawRoundedRect(
            QRectF(start, track.top(), max(end - start, 6), track.height()), 3, 3
        )
        avg_x = x_for(hop.avg_ms or 0.0)
        painter.setBrush(QColor(t.text))
        painter.drawRoundedRect(
            QRectF(avg_x - 1, track.top() - 2, 2, track.height() + 4), 1, 1
        )

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), 32)


class LatencyChart(QWidget):
    """Line chart of the last 60 probes for one hop. Lost probes show as ticks on the baseline."""

    LEFT, RIGHT, TOP, BOTTOM = 40, 12, 12, 24

    def __init__(self) -> None:
        super().__init__()
        self.values: list[float | None] = []
        self.setMinimumHeight(140)

    def set_values(self, values: list[float | None], description: str) -> None:
        self.values = values
        self.setAccessibleDescription(description)
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = QRectF(
            self.LEFT,
            self.TOP,
            self.width() - self.LEFT - self.RIGHT,
            self.height() - self.TOP - self.BOTTOM,
        )
        answered = [v for v in self.values if v is not None]
        top_ms = _nice_ceiling(max(max(answered, default=0.0) * 1.15, 5.0))

        painter.setFont(theme.mono_font(11))
        for fraction in (0.0, 0.5, 1.0):
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(t.hairline, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(t.text_muted))
            label = f"{top_ms * fraction:g}"
            painter.drawText(
                QRectF(0, y - 8, self.LEFT - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 6, 120, 16),
            Qt.AlignmentFlag.AlignLeft,
            f"-{HISTORY_LENGTH} probes",
        )
        painter.drawText(
            QRectF(plot.right() - 120, plot.bottom() + 6, 120, 16),
            Qt.AlignmentFlag.AlignRight,
            "now",
        )

        if not self.values:
            painter.end()
            return

        step = plot.width() / (HISTORY_LENGTH - 1)
        offset = HISTORY_LENGTH - len(self.values)
        path = QPainterPath()
        drawing = False
        last_point = None
        for i, value in enumerate(self.values):
            x = plot.left() + (offset + i) * step
            if value is None:
                drawing = False
                painter.setPen(QPen(QColor(t.negative), 2))
                painter.drawLine(
                    QPointF(x, plot.bottom() - 6), QPointF(x, plot.bottom())
                )
                continue
            point = QPointF(x, plot.bottom() - plot.height() * min(value / top_ms, 1.0))
            if drawing:
                path.lineTo(point)
            else:
                path.moveTo(point)
                drawing = True
            last_point = point

        pen = QPen(QColor(t.accent), 1.5)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        if last_point is not None and self.values[-1] is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawEllipse(last_point, 3, 3)
        painter.end()


class PingTracePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self._worker: TraceWorker | None = None
        self._selected_ttl: int | None = None
        self._destination_ttl: int | None = None
        self._ip = ""

        self.target = QLineEdit()
        self.target.setPlaceholderText("Host name or IP address, for example 1.1.1.1")
        self.target.setAccessibleName("Target host")
        self.target.returnPressed.connect(self.toggle)
        self.interval = QComboBox()
        self.interval.setAccessibleName("Probe interval")
        for label, _ in INTERVALS:
            self.interval.addItem(label)
        self.interval.setCurrentIndex(DEFAULT_INTERVAL_INDEX)
        self.interval.currentIndexChanged.connect(self._interval_changed)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(88)
        self.start_button.clicked.connect(self.toggle)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.target, 1)
        controls.addWidget(self.interval)
        controls.addWidget(self.start_button)

        self.title = QLabel()
        self.title.setFont(theme.ui_font(15, QFont.Weight.Medium))
        self.summary = QLabel()
        self.summary.setProperty("role", "secondary")
        heading = QHBoxLayout()
        heading.setSpacing(16)
        heading.addWidget(self.title)
        heading.addWidget(self.summary, 1)

        self.chart_caption = QLabel()
        self.chart_caption.setProperty("role", "muted")
        self.chart = LatencyChart()
        chart_panel = QWidget()
        chart_panel.setObjectName("chartPanel")
        chart_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(16, 12, 16, 8)
        chart_layout.setSpacing(4)
        chart_layout.addWidget(self.chart_caption)
        chart_layout.addWidget(self.chart)

        self.model = HopModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(HopDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setAccessibleName("Hops")
        header = self.table.horizontalHeader()
        header.setFont(theme.ui_font(theme.SMALL_PX))
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(HopModel.HOST, QHeaderView.ResizeMode.Stretch)
        widths = {
            HopModel.TTL: 40,
            HopModel.ROUTE: 24,
            HopModel.LOSS: 64,
            HopModel.SENT: 56,
            HopModel.RANGE: 136,
        }
        for column in (HopModel.LAST, HopModel.AVG, HopModel.BEST, HopModel.WORST):
            widths[column] = 64
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(16)
        results_layout.addLayout(heading)
        results_layout.addWidget(chart_panel)
        results_layout.addWidget(self.table, 1)

        self.empty = QLabel(
            "Enter a host name or IP address, then select Start to trace the route."
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
        layout.addWidget(self.body, 1)

    @property
    def running(self) -> bool:
        return self._worker is not None

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        target = self.target.text().strip()
        if not target:
            self._show_message("Enter a host name or IP address to trace.", error=True)
            return
        self.stop()
        self._selected_ttl = None
        self._destination_ttl = None
        self._ip = ""
        self.model.set_hops([])
        self.chart.set_values([], "")
        self.title.setText(target)
        self.summary.setText("Resolving…")
        self.chart_caption.setText("")
        self.body.setCurrentIndex(1)

        worker = TraceWorker(target, INTERVALS[self.interval.currentIndex()][1])
        worker.resolved.connect(lambda ip, w=worker: self._on_resolved(w, ip))
        worker.updated.connect(
            lambda hops, dest, w=worker: self._on_updated(w, hops, dest)
        )
        worker.failed.connect(lambda message, w=worker: self._on_failed(w, message))
        self._worker = worker
        self.target.setEnabled(False)
        self.start_button.setText("Stop")
        worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        self.target.setEnabled(True)
        self.start_button.setText("Start")

    def _interval_changed(self, index: int) -> None:
        if self._worker is not None:
            self._worker.interval_s = INTERVALS[index][1]

    def _show_message(self, text: str, error: bool) -> None:
        self.empty.setText(text)
        self.empty.setProperty("role", "error" if error else "secondary")
        self.empty.style().unpolish(self.empty)
        self.empty.style().polish(self.empty)
        self.body.setCurrentIndex(0)

    def _on_resolved(self, worker: TraceWorker, ip: str) -> None:
        if worker is not self._worker:
            return
        self._ip = ip
        self.summary.setText(f"{ip} · tracing…")

    def _on_failed(self, worker: TraceWorker, message: str) -> None:
        if worker is not self._worker:
            return
        self.stop()
        self._show_message(message, error=True)

    def _on_updated(
        self, worker: TraceWorker, hops: list[HopStats], destination_ttl: int | None
    ) -> None:
        if worker is not self._worker:
            return
        self._destination_ttl = destination_ttl
        selected_row = self.table.currentIndex().row()
        self.model.set_hops(hops)
        if 0 <= selected_row < len(hops):
            self.table.selectRow(selected_row)
        self._update_summary(hops, destination_ttl)
        self._update_chart()

    def _update_summary(
        self, hops: list[HopStats], destination_ttl: int | None
    ) -> None:
        if destination_ttl is None:
            self.summary.setText(
                f"{self._ip} · {len(hops)} hops so far · destination not reached yet"
            )
            return
        destination = hops[-1]
        avg = destination.avg_ms
        avg_text = "no replies" if avg is None else f"avg {avg:.1f} ms"
        hop_word = "hop" if destination_ttl == 1 else "hops"
        self.summary.setText(
            f"{self._ip} · {destination_ttl} {hop_word} · {destination.loss_percent:.0f}% loss at destination · {avg_text}"
        )

    def _row_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid() and current.row() < len(self.model.hops):
            self._selected_ttl = self.model.hops[current.row()].ttl
        self._update_chart()

    def _update_chart(self) -> None:
        hops = self.model.hops
        if not hops:
            return
        ttl = self._selected_ttl or self._destination_ttl or hops[-1].ttl
        hop = next((h for h in hops if h.ttl == ttl), hops[-1])
        name = hop.hostname or hop.address or f"hop {hop.ttl}"
        which = "destination" if hop.ttl == self._destination_ttl else f"hop {hop.ttl}"
        self.chart_caption.setText(f"Latency to {name} ({which}), ms")
        self.chart.set_values(
            list(hop.history),
            f"Latency to {name}: average {_ms(hop.avg_ms)} ms, {hop.loss_percent:.0f}% loss",
        )

    def shutdown(self) -> None:
        self.stop()
