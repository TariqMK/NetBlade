"""Checks target and port parsing, result rows, and a real built-in scan of a local listening socket."""

import socket

import pytest

from netblade.core.portscan import (
    TOP_100,
    BuiltinScan,
    HostResult,
    ScanInputError,
    parse_ports,
    parse_targets,
    rows_from_results,
    service_name,
)


def test_parse_ports_lists_and_ranges():
    assert parse_ports("443, 22,80 8000-8002;22") == [22, 80, 443, 8000, 8001, 8002]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "at least one"),
        ("http", "not a port"),
        ("0-10", "outside"),
        ("70000", "outside"),
    ],
)
def test_parse_ports_errors(text, message):
    with pytest.raises(ScanInputError, match=message):
        parse_ports(text)


def test_parse_targets_forms():
    assert parse_targets("192.168.1.0/30") == ["192.168.1.1", "192.168.1.2"]
    assert parse_targets("10.0.0.254-255") == ["10.0.0.254", "10.0.0.255"]
    assert parse_targets("10.0.0.1-10.0.0.3, server.example") == [
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
        "server.example",
    ]
    assert parse_targets("10.0.0.5/32") == ["10.0.0.5"]
    assert parse_targets("10.0.0.1 10.0.0.1") == ["10.0.0.1"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Enter a host"),
        ("10.0.0.0/21", "at most 1,024"),
        ("10.0.0.9-3", "ends before"),
        ("10.0.0.0/40", "not a valid"),
    ],
)
def test_parse_targets_errors(text, message):
    with pytest.raises(ScanInputError, match=message):
        parse_targets(text)


def test_top_100_matches_nmap_fast_scan_size():
    assert len(TOP_100) == len(set(TOP_100)) == 100


def test_service_names():
    assert service_name(3389) == "rdp"
    assert service_name(22) == "ssh"


def test_rows_from_results():
    results = [
        HostResult(
            "a",
            address="10.0.0.1",
            name="router.home",
            ping_ms=1.2,
            open_ports=[22, 443],
        ),
        HostResult("b", address="10.0.0.2", ping_ms=3.0),
        HostResult("c", address="10.0.0.3"),
        HostResult("bad.name", error="Can't resolve name"),
    ]
    rows = rows_from_results(results, port_count=20)
    assert [(r.host, r.port, r.state) for r in rows] == [
        ("10.0.0.1", "22/tcp", "open"),
        ("10.0.0.1", "443/tcp", "open"),
        ("10.0.0.2", "—", "Up"),
        ("bad.name", "—", "Can't resolve name"),
    ]
    assert rows[2].detail == "Ping 3.0 ms · no open ports among 20"
    with_down = rows_from_results(results, port_count=20, show_down=True)
    assert ("10.0.0.3", "No reply") in [(r.host, r.state) for r in with_down]


def test_real_scan_finds_local_listening_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        open_port = server.getsockname()[1]
        # A port that was free a moment ago, so it is very likely closed.
        with socket.socket() as spare:
            spare.bind(("127.0.0.1", 0))
            closed_port = spare.getsockname()[1]
        scan = BuiltinScan(
            ["127.0.0.1"], [open_port, closed_port], timeout_s=1.0, ping=False
        )
        results, _seconds = scan.run()
    assert results[0].open_ports == [open_port]
    assert results[0].up
