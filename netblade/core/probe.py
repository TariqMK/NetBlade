"""Sends one ICMP echo probe with a chosen TTL and reports who answered.

Windows uses the system ICMP API (IcmpSendEcho), so no admin rights are needed.
macOS and Linux use unprivileged ICMP sockets through icmplib.
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from dataclasses import dataclass
from enum import Enum

PROBE_TIMEOUT_S = 1.0


class ProbeKind(Enum):
    REPLY = "reply"  # The destination answered.
    TTL_EXPIRED = "ttl_expired"  # A router on the path answered.
    UNREACHABLE = "unreachable"  # A router reported the destination as unreachable.
    TIMEOUT = "timeout"  # Nobody answered in time.


@dataclass(frozen=True)
class ProbeResult:
    kind: ProbeKind
    address: str | None = None
    rtt_ms: float | None = None


def resolve_ipv4(host: str) -> str:
    """Return the first IPv4 address for a host name or IPv4 literal. Raises socket.gaierror."""
    return socket.getaddrinfo(host.strip(), None, socket.AF_INET)[0][4][0]


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _IpOptionInformation(ctypes.Structure):
        _fields_ = [
            ("Ttl", ctypes.c_ubyte),
            ("Tos", ctypes.c_ubyte),
            ("Flags", ctypes.c_ubyte),
            ("OptionsSize", ctypes.c_ubyte),
            ("OptionsData", ctypes.c_void_p),
        ]

    class _IcmpEchoReply(ctypes.Structure):
        _fields_ = [
            ("Address", ctypes.c_ulong),
            ("Status", ctypes.c_ulong),
            ("RoundTripTime", ctypes.c_ulong),
            ("DataSize", ctypes.c_ushort),
            ("Reserved", ctypes.c_ushort),
            ("Data", ctypes.c_void_p),
            ("Options", _IpOptionInformation),
        ]

    _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    _iphlpapi.IcmpCreateFile.argtypes = []
    _iphlpapi.IcmpCreateFile.restype = wintypes.HANDLE
    _iphlpapi.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    _iphlpapi.IcmpCloseHandle.restype = wintypes.BOOL
    _iphlpapi.IcmpSendEcho.argtypes = [
        wintypes.HANDLE,
        ctypes.c_ulong,
        ctypes.c_void_p,
        wintypes.WORD,
        ctypes.POINTER(_IpOptionInformation),
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _iphlpapi.IcmpSendEcho.restype = wintypes.DWORD

    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _IP_SUCCESS = 0
    _IP_TTL_EXPIRED_TRANSIT = 11013
    _IP_UNREACHABLE = {11002, 11003, 11004, 11005}  # net, host, protocol, port
    _PAYLOAD = b"NetBlade" * 4  # 32 bytes, the same size as ping.exe

    def probe(ip: str, ttl: int, timeout_s: float = PROBE_TIMEOUT_S) -> ProbeResult:
        """Send one echo request to an IPv4 address with the given TTL."""
        handle = _iphlpapi.IcmpCreateFile()
        if not handle or handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            options = _IpOptionInformation(Ttl=ttl)
            reply_size = ctypes.sizeof(_IcmpEchoReply) + len(_PAYLOAD) + 8 + 128
            reply_buffer = ctypes.create_string_buffer(reply_size)
            destination = struct.unpack("<L", socket.inet_aton(ip))[0]
            start = time.perf_counter()
            count = _iphlpapi.IcmpSendEcho(
                handle,
                destination,
                _PAYLOAD,
                len(_PAYLOAD),
                ctypes.byref(options),
                reply_buffer,
                reply_size,
                int(timeout_s * 1000),
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
        finally:
            _iphlpapi.IcmpCloseHandle(handle)

        if count == 0:
            return ProbeResult(ProbeKind.TIMEOUT)
        reply = _IcmpEchoReply.from_buffer(reply_buffer)
        address = socket.inet_ntoa(struct.pack("<L", reply.Address))
        if reply.Status == _IP_SUCCESS:
            kind = ProbeKind.REPLY
        elif reply.Status == _IP_TTL_EXPIRED_TRANSIT:
            kind = ProbeKind.TTL_EXPIRED
        elif reply.Status in _IP_UNREACHABLE:
            kind = ProbeKind.UNREACHABLE
        else:
            return ProbeResult(ProbeKind.TIMEOUT)
        return ProbeResult(kind, address, round(elapsed_ms, 2))

else:
    import itertools
    import os

    from icmplib import ICMPRequest, ICMPv4Socket
    from icmplib.exceptions import TimeoutExceeded

    _sequence = itertools.count(1)

    def probe(ip: str, ttl: int, timeout_s: float = PROBE_TIMEOUT_S) -> ProbeResult:
        """Send one echo request to an IPv4 address with the given TTL."""
        sock = ICMPv4Socket(privileged=False)
        try:
            request = ICMPRequest(
                destination=ip,
                id=os.getpid() & 0xFFFF,
                sequence=next(_sequence) & 0xFFFF,
                ttl=ttl,
            )
            sock.send(request)
            reply = sock.receive(request, timeout_s)
        except TimeoutExceeded:
            return ProbeResult(ProbeKind.TIMEOUT)
        finally:
            sock.close()

        rtt_ms = round((reply.time - request.time) * 1000, 2)
        kinds = {
            0: ProbeKind.REPLY,
            11: ProbeKind.TTL_EXPIRED,
            3: ProbeKind.UNREACHABLE,
        }
        kind = kinds.get(reply.type)
        if kind is None:
            return ProbeResult(ProbeKind.TIMEOUT)
        return ProbeResult(kind, reply.source, rtt_ms)
