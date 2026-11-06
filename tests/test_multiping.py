"""Checks the multi-ping state rules, host list parsing and the text shown on tiles and in the log."""

import pytest

from netblade.core.multiping import (
    MAX_ALIAS_LENGTH,
    HostState,
    PingTarget,
    change_line,
    clean_alias,
    format_duration,
    parse_hosts,
    status_line,
)

OK, LOST = 1.0, None


def feed(target: PingTarget, results) -> list[HostState]:
    states = []
    for i, rtt in enumerate(results):
        target.record(rtt, now=1000.0 + i)
        states.append(target.state)
    return states


def test_new_host_is_up_on_first_reply():
    target = PingTarget("10.0.0.1")
    assert feed(target, [OK]) == [HostState.UP]


def test_new_host_that_never_replies_goes_down_after_three():
    target = PingTarget("10.0.0.1")
    assert feed(target, [LOST, LOST, LOST]) == [
        HostState.UNSTABLE,
        HostState.UNSTABLE,
        HostState.DOWN,
    ]


def test_up_host_turns_amber_then_red():
    target = PingTarget("10.0.0.1")
    states = feed(target, [OK] * 5 + [LOST, LOST, LOST])
    assert states[4:] == [
        HostState.UP,
        HostState.UNSTABLE,
        HostState.UNSTABLE,
        HostState.DOWN,
    ]


def test_single_loss_keeps_host_amber_for_ten_pings():
    target = PingTarget("10.0.0.1")
    states = feed(target, [OK] * 5 + [LOST] + [OK] * 10)
    # The loss stays in the 10-ping window for 9 more replies.
    assert states[5:15] == [HostState.UNSTABLE] * 10
    assert states[15] == HostState.UP


def test_recovery_from_down_passes_through_amber():
    target = PingTarget("10.0.0.1")
    feed(target, [LOST] * 5)
    assert target.state is HostState.DOWN
    states = feed(target, [OK] * 10)
    assert states[0] == HostState.UNSTABLE
    assert states[-1] == HostState.UP


def test_record_returns_previous_state_only_on_change():
    target = PingTarget("10.0.0.1")
    assert target.record(OK, now=1.0) is HostState.IDLE
    assert target.record(OK, now=2.0) is None
    assert target.since == 1.0


def test_statistics():
    target = PingTarget("10.0.0.1")
    feed(target, [2.0, LOST, 4.0, LOST])
    assert target.sent == 4
    assert target.received == 2
    assert target.loss_percent == 50.0
    assert target.avg_ms == 3.0
    assert list(target.history) == [2.0, None, 4.0, None]


def test_reset_keeps_host_and_address_only():
    target = PingTarget("router", ip="192.168.1.1", name="router.home")
    feed(target, [LOST] * 4)
    target.paused = True
    target.reset()
    assert (target.host, target.ip, target.name) == (
        "router",
        "192.168.1.1",
        "router.home",
    )
    assert target.sent == 0
    assert target.state is HostState.IDLE
    assert not target.paused


def test_parse_hosts_splits_and_removes_duplicates():
    text = "10.0.0.1, 10.0.0.2;router.home\n1.1.1.1  10.0.0.1\n"
    assert parse_hosts(text) == ["10.0.0.1", "10.0.0.2", "router.home", "1.1.1.1"]
    assert parse_hosts("   ") == []


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0 s"),
        (45, "45 s"),
        (252, "4 min 12 s"),
        (8040, "2 h 14 min"),
        (277200, "3 d 5 h"),
    ],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_status_lines():
    target = PingTarget("10.0.0.1")
    assert status_line(target) == "Not started"
    target.record(OK, now=100.0)
    assert status_line(target, now=352.0) == "Up for 4 min 12 s"
    target.record(LOST, now=353.0)
    assert status_line(target, now=354.0) == "Unstable · 1 of last 2 lost"
    target.error = "Can't resolve name"
    assert status_line(target) == "Can't resolve name"
    target.paused = True
    assert status_line(target) == "Paused"


def test_change_line_includes_address_when_name_was_entered():
    target = PingTarget("printer-2f", ip="192.168.1.40")
    feed(target, [OK, LOST, LOST, LOST])
    line = change_line(target, HostState.UNSTABLE, at=0.0)
    assert line.endswith("printer-2f (192.168.1.40)  Unstable → Down")


# Friendly names


def test_label_and_addresses_with_and_without_alias():
    ip_host = PingTarget("192.168.1.40")
    assert ip_host.label == "192.168.1.40"
    assert ip_host.addresses() == []
    ip_host.name = "printer.home"
    assert ip_host.addresses() == ["printer.home"]

    ip_host.alias = "Printer 2F"
    assert ip_host.label == "Printer 2F"
    assert ip_host.addresses() == [
        "192.168.1.40"
    ]  # The address still shows with a friendly name.

    named = PingTarget("nas.home", ip="10.0.0.5", alias="NAS")
    assert named.addresses() == ["nas.home", "10.0.0.5"]


def test_clean_alias():
    assert clean_alias("  Printer   2F ") == "Printer 2F"
    assert clean_alias("   ") is None
    assert len(clean_alias("x" * 100)) == MAX_ALIAS_LENGTH


def test_reset_keeps_alias():
    target = PingTarget("10.0.0.1", alias="Core switch")
    feed(target, [OK, LOST])
    target.reset()
    assert target.alias == "Core switch"
    assert target.sent == 0


def test_change_line_uses_alias_and_keeps_addresses():
    target = PingTarget("printer-2f", ip="192.168.1.40", alias="Printer 2F")
    feed(target, [OK, LOST, LOST, LOST])
    line = change_line(target, HostState.UNSTABLE, at=0.0)
    assert line.endswith("Printer 2F (printer-2f, 192.168.1.40)  Unstable → Down")
    by_ip = PingTarget("10.0.0.1", ip="10.0.0.1", alias="Core switch")
    feed(by_ip, [LOST] * 3)
    assert change_line(by_ip, HostState.UNSTABLE, at=0.0).endswith(
        "Core switch (10.0.0.1)  Unstable → Down"
    )


def test_tile_rename_flow(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from netblade.ui.multiping_page import MultiPingPage

    page = MultiPingPage()
    page.resize(900, 600)
    page.show()
    page.add_field.setText("192.168.1.40")
    page.add_from_field()
    target = page.targets[0]
    tile = page.tiles[id(target)]

    tile.start_rename()
    QTest.keyClicks(tile._editor, "Printer 2F")
    QTest.keyClick(tile._editor, Qt.Key.Key_Return)
    assert target.alias == "Printer 2F"
    assert not tile._editor.isVisible()
    assert "192.168.1.40 is now called Printer 2F" in page.log.toPlainText()
    assert tile.accessibleName() == "Printer 2F 192.168.1.40"

    tile.start_rename()  # Esc keeps the old name.
    QTest.keyClicks(tile._editor, "Something else")
    QTest.keyClick(tile._editor, Qt.Key.Key_Escape)
    assert target.alias == "Printer 2F"

    tile.start_rename()  # An empty name removes the friendly name.
    tile._editor.clear()
    QTest.keyClick(tile._editor, Qt.Key.Key_Return)
    assert target.alias is None
    assert "friendly name removed" in page.log.toPlainText()
    page.shutdown()
