"""Small widgets shared by several pages: segmented control and caption label."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget


def segmented(labels: list[str], parent: QWidget) -> tuple[QWidget, QButtonGroup]:
    """A row of exclusive buttons. Button ids are the label positions; the first is checked."""
    host = QWidget()
    host.setObjectName("segmented")
    host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(2)
    group = QButtonGroup(parent)
    for index, text in enumerate(labels):
        button = QPushButton(text)
        button.setObjectName("segment")
        button.setCheckable(True)
        group.addButton(button, index)
        layout.addWidget(button)
    group.button(0).setChecked(True)
    return host, group


def caption(text: str) -> QLabel:
    """Secondary-coloured label for field names and notes."""
    label = QLabel(text)
    label.setProperty("role", "secondary")
    return label
