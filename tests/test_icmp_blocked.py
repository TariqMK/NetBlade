"""Checks that a system blocking unprivileged ICMP gives a clear error, and that the scanner still checks TCP ports."""

import socket
import sys

import pytest

from netblade.core import portscan
from netblade.core import probe as probe_module
from netblade.core.portscan import BuiltinScan


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows uses IcmpSendEcho, not icmplib"
)
def test_blocked_icmp_raises_permission_error(monkeypatch):
    from icmplib.exceptions import SocketPermissionError

    def blocked(privileged):
        raise SocketPermissionError(privileged)

    monkeypatch.setattr(probe_module, "ICMPv4Socket", blocked)
    with pytest.raises(PermissionError, match="ping_group_range"):
        probe_module.probe("192.0.2.1", 64)


def test_scan_checks_ports_when_ping_is_blocked(monkeypatch):
    def blocked(ip, ttl, timeout_s=1.0):
        raise PermissionError("This system blocks ping for normal users.")

    monkeypatch.setattr(portscan, "probe", blocked)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        results, _ = BuiltinScan(["127.0.0.1"], [port], timeout_s=0.5, ping=True).run()
    assert results[0].open_ports == [port]
    assert results[0].ping_ms is None
    assert "blocks ping" in results[0].ping_error
