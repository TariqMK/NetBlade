"""Checks that sidebar tools can be reordered, that the order is saved, and that pages and shortcuts follow it."""

from PySide6.QtCore import QModelIndex

from netblade.app import DEFAULT_ORDER, ORDER_KEY, MainWindow, tool_order


def test_tool_order_merges_saved_and_new_tools():
    default = ["A", "B", "C", "D"]
    assert tool_order(None, default) == default
    assert tool_order(["C", "A"], default) == [
        "C",
        "A",
        "B",
        "D",
    ]  # New tools go at the end.
    assert tool_order(["C", "Gone", "C", "A"], default) == [
        "C",
        "A",
        "B",
        "D",
    ]  # Unknown and repeats dropped.


def test_move_current_reorders_saves_and_keeps_page(settings):
    window = MainWindow(settings)
    window.nav.setCurrentRow(2)  # IP config
    window.move_current(-1)
    expected = ["Ping and trace", "IP config", "DNS lookup", *DEFAULT_ORDER[3:]]
    assert window.tool_names() == expected
    assert window.nav.currentRow() == 1
    assert window.pages.currentWidget() is window.ipconfig_page
    assert list(settings.value(ORDER_KEY)) == expected


def test_move_stops_at_the_ends(settings):
    window = MainWindow(settings)
    window.nav.setCurrentRow(0)
    window.move_current(-1)
    window.nav.setCurrentRow(len(DEFAULT_ORDER) - 1)
    window.move_current(1)
    assert window.tool_names() == DEFAULT_ORDER


def test_saved_order_is_restored_with_matching_pages(settings):
    saved = ["Subnet calculator", "curl", "Ping and trace"]
    settings.setValue(ORDER_KEY, saved)
    window = MainWindow(settings)
    names = window.tool_names()
    assert names[:3] == saved
    assert set(names) == set(DEFAULT_ORDER)
    for row, name in enumerate(names):
        window.nav.setCurrentRow(row)
        assert window.pages.currentWidget() is window.tool_pages[name]


def test_single_saved_tool_is_read_as_a_list(settings):
    settings.setValue(ORDER_KEY, "nmap")  # How QSettings can return a one-item list.
    window = MainWindow(settings)
    assert window.tool_names()[0] == "nmap"


def test_reset_order(settings):
    window = MainWindow(settings)
    window.nav.setCurrentRow(4)
    window.move_current(-1)
    window.reset_order()
    assert window.tool_names() == DEFAULT_ORDER
    assert settings.value(ORDER_KEY) is None
    assert (
        window.pages.currentWidget() is window.multiping_page
    )  # The open tool stays open.


def test_completed_drop_saves_order(settings):
    """After Qt moves a row for a drop, the list reports it and the order is saved.

    Qt only accepts an internal-move drop from a real drag that started in the same list,
    so the drag itself is checked by hand in the running app, not here.
    """
    window = MainWindow(settings)
    nav = window.nav
    nav.setCurrentRow(6)
    nav.model().moveRow(QModelIndex(), 6, QModelIndex(), 0)
    nav.order_changed.emit()
    names = window.tool_names()
    assert names[0] == "Subnet calculator"
    assert sorted(names) == sorted(DEFAULT_ORDER)
    assert list(settings.value(ORDER_KEY)) == names
