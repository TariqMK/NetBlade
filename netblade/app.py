"""Main window of NetBlade: the sidebar with the app name, version and tools, and the tool pages."""

import gc
import sys

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QImageReader, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade import APP_NAME, __version__
from netblade.core import dns_lookup, tlscheck
from netblade.ui import tasks, theme
from netblade.ui.curl_page import CurlPage
from netblade.ui.dns_page import DnsPage
from netblade.ui.icons import ICON_PX, nav_icon
from netblade.ui.ipconfig_page import IpConfigPage
from netblade.ui.mac_page import MacPage
from netblade.ui.multiping_page import MultiPingPage
from netblade.ui.notepad_page import NotepadPage
from netblade.ui.ping_trace import PingTracePage
from netblade.ui.scan_page import ScanPage
from netblade.ui.subnet_page import SubnetPage
from netblade.ui.tls_page import TlsPage
from netblade.ui.whois_page import WhoisPage

SIDEBAR_WIDTH = 216
TOOLS = [
    ("Ping and trace", "route"),
    ("DNS lookup", "globe"),
    ("IP config", "network"),
    ("curl", "terminal"),
    ("Multi-ping", "grid"),
    ("nmap", "radar"),
    ("Subnet calculator", "calculator"),
    ("TLS certificate", "shield"),
    ("Whois", "landmark"),
    ("MAC lookup", "tag"),
    ("Notepad", "note"),
]
DEFAULT_ORDER = [name for name, _icon in TOOLS]
ORDER_KEY = "sidebar/tool_order"
ToolRole = Qt.ItemDataRole.UserRole + 1


def tool_order(
    saved: list[str] | None, default: list[str] = DEFAULT_ORDER
) -> list[str]:
    """The saved sidebar order, without tools that no longer exist, plus new tools at the end."""
    known = list(dict.fromkeys(name for name in (saved or []) if name in default))
    return known + [name for name in default if name not in known]


class ToolList(QListWidget):
    """Sidebar list that reports every completed drag-and-drop, so the new order can be saved."""

    order_changed = Signal()

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.order_changed.emit()


def display_version() -> str:
    """Return the version as users see it, for example "v2.4.0"."""
    return f"v{__version__}"


class MainWindow(QMainWindow):
    """Top-level NetBlade window."""

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else QSettings()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(880, 560)
        self.resize(1120, 720)

        title = QLabel(APP_NAME)
        title.setFont(theme.ui_font(16, QFont.Weight.Medium))
        self.version_label = QLabel(display_version())
        self.version_label.setFont(theme.mono_font(theme.SMALL_PX))
        self.version_label.setProperty("role", "muted")
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 8, 0)
        brand.setSpacing(8)
        brand.addWidget(title, 0, Qt.AlignmentFlag.AlignBaseline)
        brand.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignBaseline)
        brand.addStretch()

        self.nav = ToolList()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(ICON_PX, ICON_PX))
        self.nav.setFont(theme.ui_font())
        self.nav.setAccessibleName("Tools")
        self.nav.setAccessibleDescription(
            "Drag a tool, or press Alt+Up or Alt+Down, to change the order."
        )
        # Drag and drop moves items inside the list only. Each item keeps its tool name in ToolRole,
        # so pages follow their item to its new position.
        self.nav.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.nav.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.nav.setDropIndicatorShown(True)
        self.nav.order_changed.connect(self._save_order)
        self.nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self._nav_menu)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 20, 8, 16)
        sidebar_layout.setSpacing(20)
        sidebar_layout.addLayout(brand)
        sidebar_layout.addWidget(self.nav, 1)

        self.ping_page = PingTracePage()
        self.dns_page = DnsPage()
        self.ipconfig_page = IpConfigPage()
        self.curl_page = CurlPage()
        self.multiping_page = MultiPingPage()
        self.scan_page = ScanPage()
        self.subnet_page = SubnetPage()
        self.tls_page = TlsPage()
        self.whois_page = WhoisPage()
        self.mac_page = MacPage()
        self.notepad_page = NotepadPage()
        pages = (
            self.ping_page,
            self.dns_page,
            self.ipconfig_page,
            self.curl_page,
            self.multiping_page,
            self.scan_page,
            self.subnet_page,
            self.tls_page,
            self.whois_page,
            self.mac_page,
            self.notepad_page,
        )
        self.tool_pages = dict(zip(DEFAULT_ORDER, pages, strict=True))
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        for page in pages:
            self.pages.addWidget(page)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.nav.currentItemChanged.connect(self._show_tool)
        self._fill_nav(tool_order(self._saved_order()))
        self.nav.setCurrentRow(0)
        # Ctrl+1 to Ctrl+9, then Ctrl+0, open the first ten tools. They follow the sidebar order, so they change when the tools are reordered.
        for row, key in enumerate("1234567890"[: len(TOOLS)]):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{key}"), self)
            shortcut.activated.connect(lambda row=row: self.nav.setCurrentRow(row))
        for keys, step in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(keys), self.nav)
            shortcut.activated.connect(lambda step=step: self.move_current(step))
        self.apply_theme()

    # Sidebar order

    def _saved_order(self) -> list[str] | None:
        value = self.settings.value(ORDER_KEY)
        if isinstance(
            value, str
        ):  # QSettings returns a one-item list as a plain string.
            return [value]
        return list(value) if value else None

    def tool_names(self) -> list[str]:
        return [self.nav.item(row).data(ToolRole) for row in range(self.nav.count())]

    def _fill_nav(self, order: list[str]) -> None:
        current = (
            self.nav.currentItem().data(ToolRole) if self.nav.currentItem() else None
        )
        self.nav.blockSignals(True)
        self.nav.clear()
        for name in order:
            item = QListWidgetItem(name, self.nav)
            item.setData(ToolRole, name)
            # No ItemIsDropEnabled: dropping on an item moves the dragged item, it never replaces one.
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
        self.nav.blockSignals(False)
        if current is not None:
            self.nav.setCurrentRow(order.index(current))

    def _save_order(self, *_args) -> None:
        self.settings.setValue(ORDER_KEY, self.tool_names())

    def move_current(self, step: int) -> None:
        """Move the selected tool up (-1) or down (+1) in the sidebar."""
        row = self.nav.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.nav.count():
            return
        self.nav.blockSignals(True)  # The page stays the same while its item moves.
        item = self.nav.takeItem(row)
        self.nav.insertItem(target, item)
        self.nav.blockSignals(False)
        self.nav.setCurrentRow(target)
        self._save_order()

    def reset_order(self) -> None:
        self.settings.remove(ORDER_KEY)
        self._fill_nav(DEFAULT_ORDER)
        self.apply_theme()

    def _nav_menu(self, position) -> None:
        clicked = self.nav.itemAt(position)
        if clicked is not None:
            self.nav.setCurrentItem(clicked)
        row = self.nav.currentRow()
        menu = QMenu(self.nav)
        for text, step, enabled in (
            ("Move up", -1, row > 0),
            ("Move down", 1, 0 <= row < self.nav.count() - 1),
        ):
            action = QAction(text, menu)
            action.setEnabled(enabled)
            action.triggered.connect(
                lambda _checked=False, step=step: self.move_current(step)
            )
            menu.addAction(action)
        menu.addSeparator()
        reset = QAction("Reset order", menu)
        reset.setEnabled(self.tool_names() != DEFAULT_ORDER)
        reset.triggered.connect(self.reset_order)
        menu.addAction(reset)
        menu.exec(self.nav.viewport().mapToGlobal(position))

    def _show_tool(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        name = item.data(ToolRole)
        self.pages.setCurrentWidget(self.tool_pages[name])
        self.setWindowTitle(f"{name} – {APP_NAME}")

    def apply_theme(self) -> None:
        """Re-tint icons and repaint custom-drawn widgets after the system appearance changes."""
        t = theme.current()
        icons = dict(TOOLS)
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            item.setIcon(nav_icon(icons[item.data(ToolRole)], t.text_muted, t.accent))
        self.multiping_page.apply_theme()
        self.subnet_page.apply_theme()
        self.update()
        for widget in self.findChildren(QWidget):
            widget.update()

    def closeEvent(self, event) -> None:
        self.ping_page.shutdown()
        self.curl_page.shutdown()
        self.multiping_page.shutdown()
        self.scan_page.shutdown()
        self.notepad_page.shutdown()  # Saves notes typed in the last moment.
        super().closeEvent(event)


def main(argv: list[str]) -> int:
    """Start the app. `--self-test` builds the window, checks bundled modules, prints the version and exits."""
    app = QApplication(argv)
    # QSettings keeps the sidebar order under NetBlade/NetBlade.
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    window = MainWindow()

    def on_scheme_changed(_scheme) -> None:
        theme.apply(app)
        window.apply_theme()

    app.styleHints().colorSchemeChanged.connect(on_scheme_changed)

    try:
        if "--self-test" in argv:
            return _self_test(window)
        window.show()
        return app.exec()
    finally:
        release_qt_objects()


def _self_test(window: MainWindow) -> int:
    """Used by the release workflow to prove the packaged build starts and has the right version."""
    dns_lookup.self_check()
    tlscheck.self_check()
    if b"svg" not in QImageReader.supportedImageFormats():
        # The style sheet draws combo-box chevrons and check marks from SVG files.
        if sys.stdout is not None:
            print("Qt SVG image plugin is missing from this build")
        return 1
    if sys.stdout is not None:
        print(f"{APP_NAME} {window.version_label.text()}")
    return 0


def release_qt_objects() -> None:
    """Destroy every window and widget while Qt is still running. Call before the program ends.

    When Python exits it frees objects in no fixed order. A widget kept alive only by a reference loop
    (for example a button connected to a lambda that uses its page) could be freed after the
    QApplication, which crashes on Linux ("shared QObject was deleted directly").
    Background tasks are stopped first, so none of them reports into a widget that is going away.
    """
    tasks.shutdown()
    gc.collect()  # Frees widgets that only reference loops keep alive, while Qt can still delete them.
    for widget in QApplication.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def run() -> None:
    """Console entry point used by `python -m netblade` and the `netblade` command."""
    sys.exit(main(sys.argv))
