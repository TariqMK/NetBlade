"""Multi-ping page: ping up to 10 hosts continuously and show each as a green, amber or red tile."""

from __future__ import annotations

import math
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from netblade.core.multiping import (
    MAX_HOSTS,
    HostState,
    PingTarget,
    change_line,
    parse_hosts,
    status_line,
)
from netblade.core.probe import ProbeKind, probe, resolve_ipv4
from netblade.ui import icons, theme
from netblade.ui.tasks import run_in_thread

INTERVALS = [
    ("Every 0.5 s", 0.5),
    ("Every 1 s", 1.0),
    ("Every 2 s", 2.0),
    ("Every 5 s", 5.0),
]
DEFAULT_INTERVAL_INDEX = 1
TILE_MIN_WIDTH = 240
TILE_MIN_HEIGHT = 152

# Status fills, the same in light and dark. Text contrast: white on green 5.4:1,
# dark on amber 7.6:1, white on red 5.9:1. Secondary text stays above 4.5:1.
STATE_COLORS = {
    HostState.UP: ("#1d7a45", "#ffffff", "#e8f3ec"),
    HostState.UNSTABLE: ("#e0a82e", "#2a1f00", "#4a3800"),
    HostState.DOWN: ("#b8322f", "#ffffff", "#fbe9e8"),
}


def _rtt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f} ms" if value < 10 else f"{value:.0f} ms"


class MultiPingWorker(QObject):
    """Pings every active target once per interval, in parallel. Results go to the GUI thread."""

    results = Signal(object)  # list of (target, ip, error, rtt_ms)

    def __init__(self, targets_fn, interval_s: float) -> None:
        super().__init__()
        self.targets_fn = targets_fn
        self.interval_s = interval_s
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _ping(target: PingTarget) -> tuple:
        ip = target.ip
        if ip is None:
            try:
                ip = resolve_ipv4(target.host)
            except (OSError, UnicodeError):
                return target, None, "Can't resolve name", None
        try:
            result = probe(ip, 64)
        except OSError as error:
            return target, ip, f"Can't send ping: {error}", None
        if result.kind is ProbeKind.REPLY:
            return target, ip, None, result.rtt_ms
        if result.kind is ProbeKind.UNREACHABLE:
            return target, ip, f"Unreachable, reported by {result.address}", None
        return target, ip, None, None

    def _run(self) -> None:
        with ThreadPoolExecutor(MAX_HOSTS) as pool:
            while not self._stop.is_set():
                started = time.monotonic()
                active = [t for t in self.targets_fn() if not t.paused]
                if active:
                    results = list(pool.map(self._ping, active))
                    if self._stop.is_set():
                        return
                    self.results.emit(results)
                self._stop.wait(
                    max(0.0, self.interval_s - (time.monotonic() - started))
                )


class HostTile(QWidget):
    """One host: a tile filled with its status colour, with Pause and Remove buttons."""

    PAD = 14

    def __init__(self, target: PingTarget, on_pause, on_remove) -> None:
        super().__init__()
        self.target = target
        self.setMinimumSize(TILE_MIN_WIDTH, TILE_MIN_HEIGHT)
        self.setAccessibleName(target.host)
        self.pause_button = QToolButton(self)
        self.pause_button.clicked.connect(lambda: on_pause(self.target))
        self.remove_button = QToolButton(self)
        self.remove_button.setToolTip("Remove host")
        self.remove_button.setAccessibleName(f"Remove {target.host}")
        self.remove_button.clicked.connect(lambda: on_remove(self.target))
        for button in (self.pause_button, self.remove_button):
            button.setObjectName("tileButton")
            button.setIconSize(QSize(14, 14))
            button.setFixedSize(26, 26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh()

    def _colors(self) -> tuple[QColor | None, str, str]:
        if self.target.paused or self.target.state not in STATE_COLORS:
            t = theme.current()
            return None, t.text, t.text_secondary
        fill, text, secondary = STATE_COLORS[self.target.state]
        return QColor(fill), text, secondary

    def refresh(self) -> None:
        _fill, text, _secondary = self._colors()
        paused = self.target.paused
        self.pause_button.setIcon(icons.icon("play" if paused else "pause", text))
        self.pause_button.setToolTip("Resume pinging" if paused else "Pause pinging")
        self.pause_button.setAccessibleName(
            f"{'Resume' if paused else 'Pause'} {self.target.host}"
        )
        self.remove_button.setIcon(icons.icon("x", text))
        self.setAccessibleDescription(
            f"{status_line(self.target)}. Last reply {_rtt(self.target.last_ms)}"
        )
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        top = self.PAD - 4
        self.remove_button.move(self.width() - self.PAD - 22, top)
        self.pause_button.move(self.width() - self.PAD - 52, top)

    def paintEvent(self, event) -> None:
        t = theme.current()
        target = self.target
        fill, text, secondary = self._colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if fill is None:
            painter.setPen(QPen(t.hairline_strong, 1))
            painter.setBrush(QColor(t.card))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
        painter.drawRoundedRect(rect, 12, 12)

        pad = self.PAD
        width = self.width() - 2 * pad
        painter.setPen(QColor(text))
        painter.setFont(theme.ui_font(14, QFont.Weight.Medium))
        name = painter.fontMetrics().elidedText(
            target.host, Qt.TextElideMode.ElideRight, width - 64
        )
        painter.drawText(
            QRectF(pad, pad, width - 64, 20), Qt.AlignmentFlag.AlignVCenter, name
        )

        big = max(22, min(44, int(self.height() * 0.16)))
        painter.setFont(theme.mono_font(big))
        rtt = _rtt(target.last_ms if target.consecutive_lost == 0 else None)
        y = pad + 26
        painter.drawText(
            QRectF(pad, y, width, big + 8), Qt.AlignmentFlag.AlignVCenter, rtt
        )
        y += big + 12

        painter.setPen(QColor(secondary))
        painter.setFont(theme.mono_font(theme.SMALL_PX))
        detail = (
            target.name if target.name and target.name != target.host else target.ip
        )
        lines = [status_line(target)]
        if detail and detail != target.host:
            lines[0] = f"{detail} · {lines[0]}"
        if target.sent:
            avg = target.avg_ms
            stats = f"{target.loss_percent:.0f}% loss · avg {_rtt(avg) if avg is not None else '—'} · {target.sent:,} sent"
            if target.state is HostState.DOWN and target.last_reply_at:
                stats = f"{target.loss_percent:.0f}% loss · last reply {time.strftime('%H:%M:%S', time.localtime(target.last_reply_at))}"
            lines.append(stats)
        metrics = painter.fontMetrics()
        for line in lines:
            painter.drawText(
                QRectF(pad, y, width, 16),
                Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(line, Qt.TextElideMode.ElideRight, width),
            )
            y += 18

        self._paint_history(
            painter, QRectF(pad, self.height() - pad - 20, width, 20), QColor(text)
        )
        painter.end()

    def _paint_history(self, painter: QPainter, area: QRectF, color: QColor) -> None:
        history = list(self.target.history)
        if not history:
            return
        slots = self.target.history.maxlen
        gap = 2.0
        bar = max((area.width() - gap * (slots - 1)) / slots, 2.0)
        top = max(max((v for v in history if v is not None), default=0.0), 5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        start = slots - len(history)
        for i, value in enumerate(history):
            x = area.left() + (start + i) * (bar + gap)
            if value is None:
                color.setAlphaF(1.0)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, area.bottom() - 3, bar, 3), 1, 1)
            else:
                height = max(area.height() * min(value / top, 1.0), 2.0)
                color.setAlphaF(0.55)
                painter.setBrush(color)
                painter.drawRoundedRect(
                    QRectF(x, area.bottom() - height, bar, height), 1, 1
                )


class MultiPingPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.targets: list[PingTarget] = []
        self.tiles: dict[int, HostTile] = {}
        self._worker: MultiPingWorker | None = None

        self.add_field = QLineEdit()
        self.add_field.setPlaceholderText("Host name or IP address, or paste a list")
        self.add_field.setAccessibleName("Hosts to add")
        self.add_field.returnPressed.connect(self.add_from_field)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self.add_from_field)
        self.interval = QComboBox()
        self.interval.setAccessibleName("Ping interval")
        for label, _ in INTERVALS:
            self.interval.addItem(label)
        self.interval.setCurrentIndex(DEFAULT_INTERVAL_INDEX)
        self.interval.currentIndexChanged.connect(self._interval_changed)
        self.start_button = QPushButton("Start all")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(96)
        self.start_button.clicked.connect(self.toggle)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.add_field, 1)
        controls.addWidget(add_button)
        controls.addWidget(self.interval)
        controls.addWidget(self.start_button)

        self.message = QLabel()
        self.message.setProperty("role", "secondary")

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.empty = QLabel(
            f"Add up to {MAX_HOSTS} hosts, then select Start all to ping them continuously."
        )
        self.empty.setProperty("role", "secondary")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        # Tiles fill the space when they fit and scroll when they don't, so none is ever clipped.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.grid_host)
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.scroll)

        log_title = QLabel("Status changes")
        log_title.setProperty("role", "secondary")
        copy_log = QPushButton("Copy")
        copy_log.clicked.connect(
            lambda: QApplication.clipboard().setText(self.log.toPlainText())
        )
        clear_log = QPushButton("Clear")
        clear_log.clicked.connect(lambda: self.log.clear())
        log_header = QHBoxLayout()
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(copy_log)
        log_header.addWidget(clear_log)
        self.log = QPlainTextEdit()
        self.log.setObjectName("raw")
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font())
        self.log.setMaximumBlockCount(1000)
        self.log.setFixedHeight(104)
        self.log.setAccessibleName("Status changes")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addLayout(controls)
        layout.addWidget(self.message)
        layout.addWidget(self.body, 1)
        layout.addLayout(log_header)
        layout.addWidget(self.log)

        self._clock = QTimer(
            self
        )  # Keeps "Up for ..." durations current between pings.
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._update_message()

    # Hosts

    def add_from_field(self) -> None:
        hosts = parse_hosts(self.add_field.text())
        if not hosts:
            self._update_message("Enter a host name or IP address to add.")
            return
        existing = {t.host.lower() for t in self.targets}
        new = [h for h in hosts if h.lower() not in existing]
        room = MAX_HOSTS - len(self.targets)
        skipped = len(new) - room if len(new) > room else 0
        for host in new[: max(room, 0)]:
            target = PingTarget(host)
            self.targets.append(target)
            self.tiles[id(target)] = HostTile(target, self.toggle_pause, self.remove)
            self._lookup_name(target)
        self.add_field.clear()
        self._relayout()
        note = None
        if skipped:
            note = f"Only {MAX_HOSTS} hosts fit. {skipped} not added."
        elif len(new) < len(hosts):
            note = "Hosts already in the list were not added again."
        self._update_message(note)

    def _lookup_name(self, target: PingTarget) -> None:
        """For a host typed as an IP address, show its reverse DNS name when one exists."""
        try:
            socket.inet_aton(target.host)
        except OSError:
            return

        def done(name) -> None:
            target.name = name
            self._refresh_tile(target)

        def reverse() -> str | None:
            try:
                return socket.gethostbyaddr(target.host)[0]
            except OSError:
                return None

        run_in_thread(reverse, done, lambda _error: None)

    def remove(self, target: PingTarget) -> None:
        if target in self.targets:
            self.targets.remove(target)
        tile = self.tiles.pop(id(target), None)
        if tile is not None:
            tile.setParent(None)
            tile.deleteLater()
        self._relayout()
        self._update_message()
        if not self.targets and self.running:
            self.stop()

    def toggle_pause(self, target: PingTarget) -> None:
        target.paused = not target.paused
        self._refresh_tile(target)

    def _relayout(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        count = len(self.targets)
        self.body.setCurrentIndex(1 if count else 0)
        if not count:
            return
        # Measure the page, not the grid: the grid has no real size until it is first shown.
        scrollbar = self.scroll.verticalScrollBar().sizeHint().width()
        available = self.width() - 48 - scrollbar
        max_columns = max(1, available // (TILE_MIN_WIDTH + 8))
        columns = min(count, max_columns)
        rows = math.ceil(count / columns)
        columns = math.ceil(
            count / rows
        )  # Balance the rows, for example 5 + 5 rather than 6 + 4.
        for column in range(MAX_HOSTS):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        for row in range(MAX_HOSTS):
            self.grid.setRowStretch(row, 1 if row < rows else 0)
        for index, target in enumerate(self.targets):
            self.grid.addWidget(
                self.tiles[id(target)], index // columns, index % columns
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.targets:
            self._relayout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.targets:
            self._relayout()

    def _update_message(self, note: str | None = None) -> None:
        count = len(self.targets)
        text = f"{count} of {MAX_HOSTS} hosts" if count else ""
        self.message.setText(f"{text} · {note}" if note and text else note or text)
        self.start_button.setEnabled(bool(count) or self.running)

    # Running

    @property
    def running(self) -> bool:
        return self._worker is not None

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if not self.targets:
            return
        for target in self.targets:
            target.reset()
            self._refresh_tile(target)
        worker = MultiPingWorker(
            lambda: list(self.targets), INTERVALS[self.interval.currentIndex()][1]
        )
        worker.results.connect(lambda results, w=worker: self._on_results(w, results))
        self._worker = worker
        self.start_button.setText("Stop all")
        self._log(
            f"{time.strftime('%H:%M:%S')}  Started pinging {len(self.targets)} hosts"
        )
        worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
            self._log(f"{time.strftime('%H:%M:%S')}  Stopped")
        self.start_button.setText("Start all")
        self._update_message()

    def _interval_changed(self, index: int) -> None:
        if self._worker is not None:
            self._worker.interval_s = INTERVALS[index][1]

    def _on_results(self, worker: MultiPingWorker, results: list) -> None:
        if worker is not self._worker:
            return
        now = time.time()
        for target, ip, error, rtt in results:
            if target not in self.targets or target.paused:
                continue
            target.ip = ip or target.ip
            target.error = error
            previous = target.record(rtt, now)
            # The first result of a host is not a change worth logging.
            if previous is not None and previous is not HostState.IDLE:
                self._log(change_line(target, previous, now))
            self._refresh_tile(target)

    def _refresh_tile(self, target: PingTarget) -> None:
        tile = self.tiles.get(id(target))
        if tile is not None:
            tile.refresh()

    def _tick(self) -> None:
        if self.running and self.isVisible():
            for tile in self.tiles.values():
                tile.update()

    def _log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def apply_theme(self) -> None:
        for tile in self.tiles.values():
            tile.refresh()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
