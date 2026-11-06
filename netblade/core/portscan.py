"""Built-in port scanner: host discovery by ping and TCP connect scans, with no external tools.

Also holds the target and port parsing shared with the nmap tool, and the result row type.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from netblade.core.probe import ProbeKind, probe, resolve_ipv4

MAX_TARGETS = 1024
MAX_WORKERS = 256

TOP_20 = [
    21,
    22,
    23,
    25,
    53,
    80,
    110,
    111,
    135,
    139,
    143,
    443,
    445,
    993,
    995,
    1723,
    3306,
    3389,
    5900,
    8080,
]
# The same 100 ports as nmap -F (its most common TCP ports).
TOP_100 = [
    7, 9, 13, 21, 22, 23, 25, 26, 37, 53, 79, 80, 81, 88, 106, 110, 111, 113, 119, 135, 139, 143, 144, 179, 199,
    389, 427, 443, 444, 445, 465, 513, 514, 515, 543, 544, 548, 554, 587, 631, 646, 873, 990, 993, 995, 1025,
    1026, 1027, 1028, 1029, 1110, 1433, 1720, 1723, 1755, 1900, 2000, 2001, 2049, 2121, 2717, 3000, 3128, 3306,
    3389, 3986, 4899, 5000, 5009, 5051, 5060, 5101, 5190, 5357, 5432, 5631, 5666, 5800, 5900, 6000, 6001, 6646,
    7070, 8000, 8008, 8009, 8080, 8081, 8443, 8888, 9100, 9999, 10000, 32768, 49152, 49153, 49154, 49155,
    49156, 49157,
]  # fmt: skip
PORT_PRESETS = {
    "Top 20 ports": TOP_20,
    "Top 100 ports": TOP_100,
    "Web ports": [80, 443, 8000, 8008, 8080, 8443, 8888],
    "Windows ports": [135, 139, 445, 3389, 5985, 5986],
    "Custom ports": None,
}

# Names for ports that the system services file often lacks or names differently.
_SERVICE_NAMES = {
    53: "dns", 135: "msrpc", 139: "netbios-ssn", 445: "microsoft-ds", 1433: "ms-sql", 1723: "pptp", 3306: "mysql",
    3389: "rdp", 5357: "wsdapi", 5432: "postgresql", 5900: "vnc", 5985: "winrm", 5986: "winrm-https",
    8080: "http-proxy", 8443: "https-alt", 9100: "jetdirect",
}  # fmt: skip


class ScanInputError(Exception):
    """The targets or ports can't be scanned. The message is written for the user."""


@dataclass(frozen=True)
class ScanRow:
    """One line of results: an open port, or a host with no open ports."""

    host: str
    name: str
    port: str
    state: str
    service: str
    detail: str = ""


def parse_ports(text: str) -> list[int]:
    """Read "22,80,443" and ranges "8000-8100". Returns sorted unique ports."""
    ports: set[int] = set()
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ScanInputError(
                f"{part} is not a port or range. Use for example 22,80,443 or 8000-8100."
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if not 1 <= start <= end <= 65535:
            raise ScanInputError(f"{part} is outside the port range 1-65535.")
        ports.update(range(start, end + 1))
    if not ports:
        raise ScanInputError("Enter at least one port, for example 22,80,443.")
    return sorted(ports)


def parse_targets(text: str, limit: int = MAX_TARGETS) -> list[str]:
    """Read host names, IPv4 addresses, CIDR networks and ranges like 192.168.1.10-50."""
    targets: list[str] = []
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        if "/" in part:
            try:
                network = ipaddress.IPv4Network(part, strict=False)
            except ValueError as error:
                raise ScanInputError(f"{part} is not a valid IPv4 network.") from error
            hosts = (
                [str(h) for h in network.hosts()]
                if network.prefixlen < 31
                else [str(h) for h in network]
            )
            targets += hosts
        elif re.fullmatch(r"\d+\.\d+\.\d+\.\d+-\d+(\.\d+\.\d+\.\d+)?", part):
            first_text, last_text = part.split("-")
            first = ipaddress.IPv4Address(first_text)
            last = (
                ipaddress.IPv4Address(last_text)
                if "." in last_text
                else ipaddress.IPv4Address(
                    first_text.rsplit(".", 1)[0] + "." + last_text
                )
            )
            if int(last) < int(first):
                raise ScanInputError(f"{part} ends before it starts.")
            if int(last) - int(first) >= limit:
                raise ScanInputError(f"Scan at most {limit:,} hosts at once.")
            targets += [
                str(ipaddress.IPv4Address(n)) for n in range(int(first), int(last) + 1)
            ]
        else:
            targets.append(part)
        if len(targets) > limit:
            raise ScanInputError(
                f"Scan at most {limit:,} hosts at once, for example a /22 network or smaller."
            )
    if not targets:
        raise ScanInputError("Enter a host, IP address, network or range to scan.")
    return list(dict.fromkeys(targets))


def service_name(port: int) -> str:
    if port in _SERVICE_NAMES:
        return _SERVICE_NAMES[port]
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


@dataclass
class HostResult:
    target: str
    address: str | None = None
    name: str = ""
    ping_ms: float | None = None
    open_ports: list[int] = field(default_factory=list)
    error: str = ""

    @property
    def up(self) -> bool:
        return self.ping_ms is not None or bool(self.open_ports)


def is_open(address: str, port: int, timeout_s: float) -> bool:
    """TCP connect check: True when the port accepts a connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex((address, port)) == 0


class BuiltinScan:
    """Pings each host and tries a TCP connection to each port. cancel() may be called from another thread."""

    def __init__(
        self,
        targets: list[str],
        ports: list[int],
        timeout_s: float = 1.0,
        ping: bool = True,
    ) -> None:
        self.targets = targets
        self.ports = ports
        self.timeout_s = timeout_s
        self.ping = ping
        self._cancel = threading.Event()

    @property
    def total_checks(self) -> int:
        return len(self.targets) * (len(self.ports) + (1 if self.ping else 0))

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(
        self, progress: Callable[[int], None] | None = None
    ) -> tuple[list[HostResult], float]:
        started = time.monotonic()
        results = [HostResult(target) for target in self.targets]
        done = 0
        with ThreadPoolExecutor(MAX_WORKERS) as pool:
            for result in results:
                try:
                    result.address = resolve_ipv4(result.target)
                except (OSError, UnicodeError):
                    result.error = "Can't resolve name"

            jobs = {}
            for result in results:
                if result.address is None:
                    continue
                if self.ping:
                    jobs[pool.submit(self._ping, result.address)] = (result, None)
                for port in self.ports:
                    jobs[pool.submit(self._check, result.address, port)] = (
                        result,
                        port,
                    )

            for future in as_completed(jobs):
                result, port = jobs[future]
                value = future.result()
                if port is None:
                    result.ping_ms = value
                elif value:
                    result.open_ports.append(port)
                done += 1
                if progress is not None and done % 50 == 0:
                    progress(done)
                if self.cancelled:
                    for pending in jobs:
                        pending.cancel()
                    break

            names = {
                r.address: pool.submit(_reverse_name, r.address)
                for r in results
                if r.up and r.address
            }
            for result in results:
                if result.address in names:
                    result.name = names[result.address].result() or ""
                result.open_ports.sort()
        return results, time.monotonic() - started

    def _ping(self, address: str) -> float | None:
        if self.cancelled:
            return None
        result = probe(address, 64, self.timeout_s)
        return result.rtt_ms if result.kind is ProbeKind.REPLY else None

    def _check(self, address: str, port: int) -> bool:
        if self.cancelled:
            return False
        return is_open(address, port, self.timeout_s)


def _reverse_name(address: str) -> str | None:
    try:
        return socket.gethostbyaddr(address)[0]
    except OSError:
        return None


def rows_from_results(
    results: list[HostResult], port_count: int, show_down: bool = False
) -> list[ScanRow]:
    """Table rows: one per open port; a host that is up with no open ports gets one row."""
    rows: list[ScanRow] = []
    for result in results:
        host = result.address or result.target
        name = result.name if result.name != host else ""
        if result.error:
            rows.append(ScanRow(result.target, "", "—", result.error, ""))
            continue
        if not result.up:
            if show_down:
                rows.append(ScanRow(host, name, "—", "No reply", ""))
            continue
        ping = (
            f"Ping {result.ping_ms:.1f} ms"
            if result.ping_ms is not None
            else "No ping reply"
        )
        if not result.open_ports:
            rows.append(
                ScanRow(
                    host,
                    name,
                    "—",
                    "Up",
                    "",
                    f"{ping} · no open ports among {port_count:,}",
                )
            )
        for port in result.open_ports:
            rows.append(
                ScanRow(host, name, f"{port}/tcp", "open", service_name(port), ping)
            )
    return rows
