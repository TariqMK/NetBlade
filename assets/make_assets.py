"""Renders the NetBlade icon files and the README banner from the icon in netblade/ui/icons.py.

Run from the project folder: python assets/make_assets.py
Writes assets/icon/ (netblade.svg, netblade.png, netblade.ico, netblade.icns), assets/banner.png
and assets/social-card.png (the GitHub social preview).
The banner uses the Segoe UI and Cascadia Mono or Consolas fonts, so run it on Windows.
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from netblade.ui.icons import APP_ICON_SIZES, APP_ICON_SVG

ASSETS = Path(__file__).resolve().parent
ICON_DIR = ASSETS / "icon"

BANNER_W, BANNER_H, BANNER_SCALE = 1280, 320, 2
TAGLINE = "Eleven network tools. One window."

app = QGuiApplication(sys.argv)


renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path) -> None:
    """Windows .ico with one PNG image per size."""
    blobs = [png(render(size)) for size in APP_ICON_SIZES]
    offset = 6 + 16 * len(blobs)
    entries = b""
    for size, blob in zip(APP_ICON_SIZES, blobs):
        dim = 0 if size >= 256 else size  # 0 means 256 in the .ico format.
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(blobs)) + entries + b"".join(blobs))


def write_icns(path: Path) -> None:
    """macOS .icns with PNG images, including the @2x (Retina) sizes."""
    types = {
        b"ic11": 32,
        b"ic12": 64,
        b"ic07": 128,
        b"ic13": 256,
        b"ic08": 256,
        b"ic14": 512,
        b"ic09": 512,
        b"ic10": 1024,
    }
    body = b""
    for kind, size in types.items():
        blob = png(render(size))
        body += kind + struct.pack(">I", len(blob) + 8) + blob
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def font(families: list[str], pixel_size: int, weight: QFont.Weight) -> QFont:
    available = set(QFontDatabase.families())
    result = QFont()
    result.setFamilies(
        [family for family in families if family in available] or families
    )
    result.setPixelSize(pixel_size)
    result.setWeight(weight)
    return result


def write_banner(path: Path) -> None:
    image = QImage(
        BANNER_W * BANNER_SCALE, BANNER_H * BANNER_SCALE, QImage.Format.Format_ARGB32
    )
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(BANNER_SCALE)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    painter.setPen(QPen(QColor("#232325"), 1))
    painter.setBrush(QColor("#0f0f10"))
    painter.drawRoundedRect(QRectF(0.5, 0.5, BANNER_W - 1, BANNER_H - 1), 24, 24)

    icon_size, gap = 176, 40
    title = font(["Segoe UI", "Inter", "Helvetica Neue"], 96, QFont.Weight.DemiBold)
    title.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98)
    tagline = font(
        ["Cascadia Mono", "Consolas", "SF Mono", "Menlo"], 24, QFont.Weight.Normal
    )
    text_w = max(
        QFontMetrics(title).horizontalAdvance("NetBlade"),
        QFontMetrics(tagline).horizontalAdvance(TAGLINE),
    )
    left = (
        BANNER_W - (icon_size + gap + text_w)
    ) / 2  # Centre the icon and text as one block.

    renderer.render(
        painter, QRectF(left, (BANNER_H - icon_size) / 2, icon_size, icon_size)
    )
    text_x = left + icon_size + gap
    painter.setFont(title)
    painter.setPen(QColor("#ededed"))
    painter.drawText(QPointF(text_x - 4, 168), "NetBlade")
    painter.setFont(tagline)
    painter.setPen(QColor("#a1a1a6"))
    painter.drawText(QPointF(text_x, 220), TAGLINE)
    painter.end()
    image.save(str(path))


def write_social_card(path: Path) -> None:
    """GitHub social preview, 1280 x 640 px. All content stays 80 px inside each edge (GitHub's crop margin)."""
    width, height = 1280, 640
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(
        QColor("#0f0f10")
    )  # Full bleed: sites crop and round the card themselves.
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    icon_size, gap = 168, 40
    title = font(["Segoe UI", "Inter", "Helvetica Neue"], 112, QFont.Weight.DemiBold)
    title.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98)
    title_w = QFontMetrics(title).horizontalAdvance("NetBlade")
    left = (width - (icon_size + gap + title_w)) / 2
    icon_top = 150
    renderer.render(painter, QRectF(left, icon_top, icon_size, icon_size))
    painter.setFont(title)
    painter.setPen(QColor("#ededed"))
    # Baseline set so the cap height sits centred on the icon.
    painter.drawText(QPointF(left + icon_size + gap - 4, icon_top + 124), "NetBlade")

    def centred(text: str, text_font: QFont, color: str, baseline: float) -> None:
        painter.setFont(text_font)
        painter.setPen(QColor(color))
        x = (width - QFontMetrics(text_font).horizontalAdvance(text)) / 2
        painter.drawText(QPointF(x, baseline), text)

    mono = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo"]
    centred(TAGLINE, font(mono, 34, QFont.Weight.Normal), "#a1a1a6", 420)
    centred(
        "Windows · macOS · Linux", font(mono, 24, QFont.Weight.Normal), "#6e6e73", 476
    )
    painter.end()
    image.save(str(path))


ICON_DIR.mkdir(exist_ok=True)
(ICON_DIR / "netblade.svg").write_text(APP_ICON_SVG, encoding="utf-8")
render(1024).save(str(ICON_DIR / "netblade.png"))
write_ico(ICON_DIR / "netblade.ico")
write_icns(ICON_DIR / "netblade.icns")
write_banner(ASSETS / "banner.png")
write_social_card(ASSETS / "social-card.png")
print(
    "Wrote",
    ", ".join(p.name for p in sorted(ICON_DIR.iterdir())),
    "banner.png and social-card.png",
)
