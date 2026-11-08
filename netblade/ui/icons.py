"""Line icons for the sidebar, drawn from Lucide paths (ISC licence, lucide.dev) and tinted per theme."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "route": '<circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/>'
    '<circle cx="18" cy="5" r="3"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
    '<path d="M2 12h20"/>',
    "network": '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/>'
    '<rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>'
    '<path d="M12 12V8"/>',
    "terminal": '<path d="m7 11 2-2-2-2"/><path d="M11 13h4"/><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>',
    "grid": '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/>'
    '<rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 '
    '4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "landmark": '<line x1="3" x2="21" y1="22" y2="22"/><line x1="6" x2="6" y1="18" y2="11"/>'
    '<line x1="10" x2="10" y1="18" y2="11"/><line x1="14" x2="14" y1="18" y2="11"/>'
    '<line x1="18" x2="18" y1="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    "tag": '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 '
    '2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/>',
    "note": '<path d="M16 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8Z"/><path d="M15 3v4a2 2 0 0 0 2 2h4"/>',
    "diff": '<path d="M12 3v14"/><path d="M5 10h14"/><path d="M5 21h14"/>',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 '
    '.623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
    "radar": '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/><path d="M4 6h.01"/>'
    '<path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/><path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/>'
    '<path d="M12 18h.01"/><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/><circle cx="12" cy="12" r="2"/>'
    '<path d="m13.41 10.59 5.66-5.66"/>',
    "calculator": '<rect width="16" height="20" x="4" y="2" rx="2"/><line x1="8" x2="16" y1="6" y2="6"/>'
    '<line x1="16" x2="16" y1="14" y2="18"/><path d="M16 10h.01"/><path d="M12 10h.01"/><path d="M8 10h.01"/>'
    '<path d="M12 14h.01"/><path d="M8 14h.01"/><path d="M12 18h.01"/><path d="M8 18h.01"/>',
}

ICON_PX = 16
_SCALE = 2  # Render at 2x so icons stay sharp on high-DPI screens.


def _pixmap(name: str, color: str) -> QPixmap:
    # Stroke 2.25 in the 24-unit box gives 1.5 px lines at 16 px.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round">'
        f"{_PATHS[name]}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(ICON_PX * _SCALE, ICON_PX * _SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return pixmap


def nav_icon(name: str, color: str, selected_color: str) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_pixmap(name, color), QIcon.Mode.Normal)
    icon.addPixmap(_pixmap(name, selected_color), QIcon.Mode.Selected)
    return icon


def icon(name: str, color: str) -> QIcon:
    """A single-colour icon, for example for buttons drawn on a coloured tile."""
    return QIcon(_pixmap(name, color))


# The NetBlade app icon: ping rings with a fine 45° cut. Single source for the window icon and for
# assets/icon (assets/make_assets.py renders the .ico, .icns and PNG files from this string).
APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="-75 -75 150 150">
  <rect x="-74.5" y="-74.5" width="149" height="149" rx="34" fill="#0f0f10" stroke="#232325" stroke-width="1"/>
  <circle r="44" fill="none" stroke="#5c5c61" stroke-width="11"/>
  <circle r="25" fill="none" stroke="#a1a1a6" stroke-width="11"/>
  <circle r="9" fill="#ededed"/>
  <line x1="-50" y1="50" x2="50" y2="-50" stroke="#0f0f10" stroke-width="3.5"/>
</svg>
"""
APP_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_app_icon(size: int) -> QPixmap:
    """The app icon as a square pixmap of `size` px."""
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """The window and taskbar icon, rendered at each common size so Qt never scales a bitmap."""
    result = QIcon()
    for size in APP_ICON_SIZES:
        result.addPixmap(render_app_icon(size))
    return result
