"""Builds nmap commands from the form, runs the installed nmap and reads its XML results.

nmap is not bundled: its licence does not allow it. The user installs it from nmap.org.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from netblade.core.portscan import ScanInputError, ScanRow, parse_ports

DOWNLOAD_URL = "https://nmap.org/download.html"

# NetBlade never needs admin rights, so nmap always runs unprivileged. That rules out SYN and UDP
# scans, OS detection and --traceroute, which need raw packets.
UNPRIVILEGED = "--unprivileged"
SCAN_TYPES = {
    "TCP connect scan (-sT)": ["-sT"],
    "Ping sweep only (-sn)": ["-sn"],
}
PORT_CHOICES = {
    "Top 1000 ports": [],
    "Top 100 ports (-F)": ["-F"],
    "All ports (-p-)": ["-p-"],
    "Custom ports": None,
}
TIMINGS = {
    "Polite (-T2)": ["-T2"],
    "Normal (-T3)": [],
    "Aggressive (-T4)": ["-T4"],
    "Insane (-T5)": ["-T5"],
}
DEFAULT_TIMING = "Aggressive (-T4)"

SUGGESTED_OPTIONS = [
    ("--open", "Show only open ports"),
    ("--reason", "Show why nmap decided each port state"),
    ("-n", "Skip reverse DNS lookups, for speed"),
    ("--top-ports 20", "Scan only the 20 most common ports"),
    ("--script http-title", "Show the title of web pages"),
    ("--script ssl-cert", "Show TLS certificate details"),
    (
        "--script ssl-enum-ciphers -p 443",
        "List the TLS versions and ciphers a server accepts",
    ),
    ("--max-retries 1", "Retry each probe only once, for speed"),
    ("--host-timeout 60s", "Give up on a host after 60 s"),
    ("-e eth0", "Send from a specific network interface"),
    ("--exclude 192.168.1.1", "Leave an address out of the scan"),
    ("-6", "Scan IPv6 addresses"),
]


@dataclass
class NmapRequest:
    targets: str
    scan_type: str = "TCP connect scan (-sT)"
    ports: str = "Top 1000 ports"
    custom_ports: str = ""
    timing: str = DEFAULT_TIMING
    version_detection: bool = False
    default_scripts: bool = False
    skip_discovery: bool = False
    extra_args: str = ""


def build_args(request: NmapRequest) -> list[str]:
    """The nmap arguments the user would type, without the program name."""
    targets = request.targets.split()
    if not targets:
        raise ScanInputError("Enter a host, IP address, network or range to scan.")
    args = [UNPRIVILEGED, *SCAN_TYPES[request.scan_type]]
    ping_only = request.scan_type == "Ping sweep only (-sn)"
    if not ping_only:
        port_args = PORT_CHOICES[request.ports]
        if port_args is None:
            parse_ports(request.custom_ports)  # Validates; nmap gets the text as typed.
            port_args = ["-p", request.custom_ports.replace(" ", "")]
        args += port_args
        if request.version_detection:
            args.append("-sV")
        if request.default_scripts:
            args.append("-sC")
    if request.skip_discovery:
        args.append("-Pn")
    args += TIMINGS[request.timing]
    if request.extra_args.strip():
        try:
            args += shlex.split(request.extra_args)
        except ValueError as error:
            raise ScanInputError(
                f"Check the quotes in More options: {error}."
            ) from error
    return args + targets


def display_command(args: list[str]) -> str:
    if sys.platform == "win32":
        quoted = [
            a if a and all(c.isalnum() or c in "-_./:=,+" for c in a) else f'"{a}"'
            for a in args
        ]
    else:
        quoted = [shlex.quote(a) for a in args]
    return " ".join(["nmap", *quoted])


def find_nmap() -> str | None:
    path = shutil.which("nmap")
    if path:
        return path
    if sys.platform == "win32":
        for folder in (r"C:\Program Files (x86)\Nmap", r"C:\Program Files\Nmap"):
            candidate = Path(folder) / "nmap.exe"
            if candidate.exists():
                return str(candidate)
    return None


def nmap_version(path: str) -> str:
    output = _run_quiet([path, "--version"])
    first = output.splitlines()[0] if output else ""
    return first.replace("Nmap version", "nmap").split(" ( ")[0].strip()


def _run_quiet(command: list[str]) -> str:
    kwargs: dict = {"capture_output": True, "timeout": 15}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(command, check=False, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.decode("utf-8", errors="replace")


@dataclass
class NmapResult:
    exit_code: int
    output: str
    xml: str
    cancelled: bool = False


class NmapRun:
    """Runs one nmap command. cancel() may be called from another thread."""

    def __init__(self, path: str, args: list[str]) -> None:
        self.path = path
        self.args = args
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None and self._process.poll() is None:
            self._process.kill()

    def run(self) -> NmapResult:
        with tempfile.TemporaryDirectory(prefix="netblade-nmap-") as folder:
            xml_path = Path(folder) / "scan.xml"
            command = [self.path, *self.args, "-oX", str(xml_path)]
            kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._process = subprocess.Popen(command, **kwargs)
            if self._cancelled:
                self._process.kill()
            output, _ = self._process.communicate()
            xml = (
                xml_path.read_text(encoding="utf-8", errors="replace")
                if xml_path.exists()
                else ""
            )
        return NmapResult(
            exit_code=self._process.returncode,
            output=output.decode("utf-8", errors="replace").replace("\r\n", "\n"),
            xml=xml,
            cancelled=self._cancelled,
        )


def parse_xml(xml: str) -> tuple[list[ScanRow], str]:
    """Rows for the results table and nmap's summary line. Hosts that are down are left out."""
    if not xml.strip():
        return [], ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], "nmap stopped before it wrote complete results"
    rows: list[ScanRow] = []
    for host in root.iter("host"):
        status = host.find("status")
        if status is not None and status.get("state") != "up":
            continue
        address = next(
            (
                a.get("addr")
                for a in host.findall("address")
                if a.get("addrtype") in ("ipv4", "ipv6")
            ),
            "",
        )
        mac = host.find("address[@addrtype='mac']")
        hostname = host.find("hostnames/hostname")
        name = hostname.get("name", "") if hostname is not None else ""
        os_match = host.find("os/osmatch")
        notes = []
        if mac is not None:
            vendor = mac.get("vendor")
            notes.append(f"MAC {mac.get('addr')}" + (f" ({vendor})" if vendor else ""))
        if os_match is not None:
            notes.append(f"OS {os_match.get('name')} ({os_match.get('accuracy')}%)")
        host_note = " · ".join(notes)

        ports = host.findall("ports/port")
        for port in ports:
            state = port.find("state")
            service = port.find("service")
            detail = ""
            if service is not None:
                detail = " ".join(
                    filter(
                        None,
                        [
                            service.get("product"),
                            service.get("version"),
                            service.get("extrainfo"),
                        ],
                    )
                )
            scripts = [
                f"{s.get('id')}: {s.get('output', '').strip()}"
                for s in port.findall("script")
            ]
            detail = " · ".join(filter(None, [detail, *scripts]))
            rows.append(
                ScanRow(
                    host=address,
                    name=name,
                    port=f"{port.get('portid')}/{port.get('protocol')}",
                    state=state.get("state", "") if state is not None else "",
                    service=service.get("name", "") if service is not None else "",
                    detail=detail,
                )
            )
        if not ports:
            rows.append(
                ScanRow(address, name, "—", "Up", "", host_note or "No ports listed")
            )
        elif host_note:
            first = rows[-len(ports)]
            rows[-len(ports)] = ScanRow(
                first.host, first.name, first.port, first.state, first.service,
                " · ".join(filter(None, [first.detail, host_note])),
            )  # fmt: skip
    finished = root.find("runstats/finished")
    summary = finished.get("summary", "") if finished is not None else ""
    return rows, summary
