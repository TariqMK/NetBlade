"""Checks the multi-ping state rules, host list parsing and the text shown on tiles and in the log."""

import pytest

from netblade.core.multiping import (
    HostState,
    PingTarget,
    change_line,
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
