"""Colours, fonts and the app-wide style sheet. Follows the system light or dark appearance."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Tokens:
    sidebar: str
    page: str
    card: str
    raised: str
    hover: str
    hairline: QColor
    hairline_strong: QColor
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_soft: QColor
    positive: str
    warning: str
    negative: str
    primary_fill: str
    primary_hover: str
    on_primary: str


def _alpha(hex_color: str, alpha: float) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


# Graphite ramp from the design direction. One cobalt accent; semantic colours only for loss.
DARK = Tokens(
    sidebar="#0f0f10",
    page="#141415",
    card="#19191b",
    raised="#232325",
    hover="#1e1e20",
    hairline=_alpha("#ededed", 0.08),
    hairline_strong=_alpha("#ededed", 0.16),
    text="#ededed",
    text_secondary="#a1a1a6",
    text_muted="#85858b",
    accent="#6b9bff",
    accent_soft=_alpha("#6b9bff", 0.18),
    positive="#4cc38a",
    warning="#e5a00d",
    negative="#e5484d",
    primary_fill="#ededed",
    primary_hover="#d4d4d6",
    on_primary="#141415",
)

LIGHT = Tokens(
    sidebar="#f4f4f5",
    page="#fafafa",
    card="#ffffff",
    raised="#ffffff",
    hover="#efeff0",
    hairline=_alpha("#171717", 0.08),
    hairline_strong=_alpha("#171717", 0.16),
    text="#171717",
    text_secondary="#525252",
    text_muted="#6e6e73",
    accent="#1f5fd6",
    accent_soft=_alpha("#1f5fd6", 0.12),
    positive="#16a34a",
    warning="#b45309",
    negative="#dc2626",
    primary_fill="#171717",
    primary_hover="#2e2e2e",
    on_primary="#ffffff",
)

BASE_PX = 13
SMALL_PX = 12

_current = DARK


def current() -> Tokens:
    return _current


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def ui_font(
    pixel_size: int = BASE_PX, weight: QFont.Weight = QFont.Weight.Normal
) -> QFont:
    font = QFont(QApplication.font())
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


# Preferred monospace faces per platform; the system fixed font is the fallback (Courier New on Windows).
MONO_FAMILIES = [
    "Cascadia Mono",
    "Consolas",
    "SF Mono",
    "Menlo",
    "JetBrains Mono",
    "DejaVu Sans Mono",
]


@lru_cache(maxsize=1)
def _mono_families() -> tuple[str, ...]:
    available = set(QFontDatabase.families())
    families = [family for family in MONO_FAMILIES if family in available]
    families.append(
        QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    )
    return tuple(families)


def mono_font(pixel_size: int = SMALL_PX) -> QFont:
    font = QFont()
    font.setFamilies(list(_mono_families()))
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    return font


def loss_color(loss_percent: float, responded: bool) -> str:
    t = current()
    if not responded:
        return t.text_muted
    if loss_percent == 0:
        return t.positive
    if loss_percent < 20:
        return t.warning
    return t.negative


def _rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def _palette(t: Tokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.page,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.card,
        QPalette.ColorRole.AlternateBase: t.raised,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.raised,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.PlaceholderText: t.text_muted,
        QPalette.ColorRole.ToolTipBase: t.raised,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: t.accent,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorRole.Accent, QColor(t.accent))
    return palette


_ASSET_DIR = Path(tempfile.mkdtemp(prefix="netblade-"))
atexit.register(shutil.rmtree, _ASSET_DIR, ignore_errors=True)


def _write_assets(t: Tokens) -> dict[str, str]:
    """Write the chevron and check-mark images the style sheet needs, coloured for this theme."""
    shapes = {
        "chevron": (t.text_secondary, '<path d="m6 9 6 6 6-6"/>'),
        "check": (t.on_primary, '<path d="M20 6 9 17l-5-5"/>'),
    }
    paths = {}
    for name, (color, body) in shapes.items():
        path = _ASSET_DIR / f"{name}-{color.lstrip('#')}.svg"
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{body}</svg>',
            encoding="utf-8",
        )
        paths[name] = path.as_posix()
    return paths


def _style_sheet(t: Tokens) -> str:
    assets = _write_assets(t)
    hairline = _rgba(t.hairline)
    strong = _rgba(t.hairline_strong)
    soft = _rgba(t.accent_soft)
    return f"""
    QMainWindow, QWidget#page, QStackedWidget#pages {{ background: {t.page}; }}
    QWidget#sidebar {{ background: {t.sidebar}; border-right: 1px solid {hairline}; }}
    QLabel {{ color: {t.text}; background: transparent; }}
    QLabel[role="secondary"] {{ color: {t.text_secondary}; }}
    QLabel[role="muted"] {{ color: {t.text_muted}; }}
    QLabel[role="error"] {{ color: {t.negative}; }}

    QListWidget#nav {{ background: transparent; border: none; outline: 0; }}
    QListWidget#nav::item {{
        height: 32px; padding: 0 8px; margin-bottom: 2px; border-radius: 8px; color: {t.text_secondary};
    }}
    QListWidget#nav::item:hover {{ background: {t.hover}; }}
    QListWidget#nav::item:selected {{ background: {t.raised}; color: {t.text}; }}

    QLineEdit, QComboBox {{
        background: {t.card}; color: {t.text}; border: 1px solid {strong}; border-radius: 8px;
        padding: 0 10px; min-height: 30px; selection-background-color: {soft}; selection-color: {t.text};
    }}
    QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border: 1px solid {t.accent}; }}
    QLineEdit:disabled {{ color: {t.text_muted}; }}
    QComboBox {{ padding-right: 28px; }}
    QComboBox QLineEdit {{ border: none; padding: 0; min-height: 0; background: transparent; }}
    QComboBox::drop-down {{ border: none; background: transparent; width: 28px; }}
    QComboBox::down-arrow {{ image: url("{assets["chevron"]}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{
        background: {t.card}; color: {t.text}; border: 1px solid {strong}; outline: 0;
        selection-background-color: {soft}; selection-color: {t.text};
    }}

    QPushButton {{
        background: {t.raised}; color: {t.text}; border: 1px solid {strong}; border-radius: 8px;
        padding: 0 16px; min-height: 30px;
    }}
    QPushButton:hover {{ background: {t.hover}; }}
    QPushButton:focus {{ border: 1px solid {t.accent}; }}
    QPushButton#primary {{ background: {t.primary_fill}; color: {t.on_primary}; border: 1px solid {t.primary_fill}; }}
    QPushButton#primary:hover {{ background: {t.primary_hover}; }}
    QPushButton#primary:focus {{ border: 1px solid {t.accent}; }}
    QPushButton#segment {{
        background: transparent; color: {t.text_secondary}; border: 1px solid transparent; padding: 0 12px;
    }}
    QPushButton#segment:hover {{ color: {t.text}; }}
    QPushButton#segment:checked {{ background: {t.raised}; color: {t.text}; border: 1px solid {strong}; }}
    QPushButton#segment:focus {{ border: 1px solid {t.accent}; }}
    QWidget#segmented {{ background: {t.sidebar}; border: 1px solid {hairline}; border-radius: 10px; }}

    QCheckBox {{ color: {t.text_secondary}; spacing: 8px; }}
    QToolButton#tileButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; }}
    QToolButton#tileButton:hover {{ background: rgba(0, 0, 0, 40); }}
    QToolButton#tileButton:focus {{ border: 1px solid {t.accent}; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px; border: 1px solid {strong}; border-radius: 4px; background: {t.card};
    }}
    QCheckBox::indicator:checked {{
        background: {t.primary_fill}; border: 1px solid {t.primary_fill}; image: url("{assets["check"]}");
    }}
    QCheckBox::indicator:focus {{ border: 1px solid {t.accent}; }}

    QTableView {{
        background: transparent; color: {t.text}; border: none; gridline-color: transparent; outline: 0;
        selection-background-color: {soft}; selection-color: {t.text};
    }}
    QTableView::item {{ border-bottom: 1px solid {hairline}; padding: 0 8px; }}
    QTableView::item:selected {{ background: {soft}; color: {t.text}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{
        background: transparent; color: {t.text_muted}; border: none; border-bottom: 1px solid {hairline};
        padding: 0 8px; min-height: 28px;
    }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QFrame#card {{ background: {t.card}; border: 1px solid {hairline}; border-radius: 12px; }}
    QPlainTextEdit#raw {{
        background: {t.card}; color: {t.text}; border: 1px solid {hairline}; border-radius: 12px; padding: 8px;
        selection-background-color: {soft}; selection-color: {t.text};
    }}
    QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {strong}; padding: 4px; }}
    QMenu::item {{ padding: 6px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {soft}; color: {t.text}; }}
    QWidget#chartPanel {{ background: {t.card}; border: 1px solid {hairline}; border-radius: 12px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {strong}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {t.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {t.raised}; color: {t.text}; border: 1px solid {strong}; padding: 4px 8px; }}
    """


def apply(app: QApplication) -> None:
    """Apply the light or dark theme to match the system appearance."""
    global _current
    _current = DARK if is_dark() else LIGHT
    app.setStyle("Fusion")
    font = QFont(app.font())
    font.setPixelSize(BASE_PX)
    app.setFont(font)
    app.setPalette(_palette(_current))
    app.setStyleSheet(_style_sheet(_current))
