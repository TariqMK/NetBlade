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
