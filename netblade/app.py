"""Main window of NetBlade: the sidebar with the app name, version and tools, and the tool pages."""

import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QImageReader, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from netblade import APP_NAME, __version__
from netblade.core import dns_lookup
from netblade.ui import theme
from netblade.ui.curl_page import CurlPage
from netblade.ui.dns_page import DnsPage
from netblade.ui.icons import ICON_PX, nav_icon
from netblade.ui.ipconfig_page import IpConfigPage
from netblade.ui.ping_trace import PingTracePage

SIDEBAR_WIDTH = 216
TOOLS = [
    ("Ping and trace", "route"),
    ("DNS lookup", "globe"),
    ("IP config", "network"),
    ("curl", "terminal"),
]


def display_version() -> str:
    """Return the version as users see it, for example "v2.4.0"."""
    return f"v{__version__}"


class MainWindow(QMainWindow):
    """Top-level NetBlade window."""

    def __init__(self) -> None:
        super().__init__()
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

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(ICON_PX, ICON_PX))
        self.nav.setFont(theme.ui_font())
        self.nav.setAccessibleName("Tools")
        for name, _icon in TOOLS:
            QListWidgetItem(name, self.nav)

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
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        for page in (self.ping_page, self.dns_page, self.ipconfig_page, self.curl_page):
            self.pages.addWidget(page)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.nav.currentRowChanged.connect(self._show_tool)
        self.nav.setCurrentRow(0)
        for number in range(len(TOOLS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number + 1}"), self)
            shortcut.activated.connect(lambda row=number: self.nav.setCurrentRow(row))
        self.apply_theme()

    def _show_tool(self, row: int) -> None:
        if row < 0:
            return
        self.pages.setCurrentIndex(row)
        self.setWindowTitle(f"{TOOLS[row][0]} – {APP_NAME}")

    def apply_theme(self) -> None:
        """Re-tint icons and repaint custom-drawn widgets after the system appearance changes."""
        t = theme.current()
        for row, (_name, icon) in enumerate(TOOLS):
            self.nav.item(row).setIcon(nav_icon(icon, t.text_muted, t.accent))
        self.update()
        for widget in self.findChildren(QWidget):
            widget.update()

    def closeEvent(self, event) -> None:
        self.ping_page.shutdown()
        self.curl_page.shutdown()
        super().closeEvent(event)


def main(argv: list[str]) -> int:
    """Start the app. `--self-test` builds the window, checks bundled modules, prints the version and exits."""
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    window = MainWindow()

    def on_scheme_changed(_scheme) -> None:
        theme.apply(app)
        window.apply_theme()

    app.styleHints().colorSchemeChanged.connect(on_scheme_changed)

    if "--self-test" in argv:
        # Used by the release workflow to prove the packaged build starts and has the right version.
        dns_lookup.self_check()
        if b"svg" not in QImageReader.supportedImageFormats():
            # The style sheet draws combo-box chevrons and check marks from SVG files.
            if sys.stdout is not None:
                print("Qt SVG image plugin is missing from this build")
            return 1
        if sys.stdout is not None:
            print(f"{APP_NAME} {window.version_label.text()}")
        return 0

    window.show()
    return app.exec()


def run() -> None:
    """Console entry point used by `python -m netblade` and the `netblade` command."""
    sys.exit(main(sys.argv))
