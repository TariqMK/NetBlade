"""Builds curl commands from the form, runs the system curl and splits its output for display."""

from __future__ import annotations

import ipaddress
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
IP_VERSIONS = {"Any IP version": None, "IPv4 only": "-4", "IPv6 only": "-6"}
HTTP_VERSIONS = {
    "Any HTTP version": None,
    "HTTP/1.0": "--http1.0",
    "HTTP/1.1": "--http1.1",
    "HTTP/2": "--http2",
}
DEFAULT_TIMEOUT_S = 30
MAX_BODY_BYTES = 1_000_000  # Larger bodies are cut for display.

# Common extra options for network troubleshooting: (text inserted, what it does).
SUGGESTED_OPTIONS = [
    ("--connect-timeout 5", "Give up if the TCP connection takes longer than 5 s"),
    ("--proxy http://proxy.example:8080", "Send the request through a proxy"),
    ("--noproxy '*'", "Ignore proxy settings from the environment"),
    ("--interface 192.168.1.10", "Send from a specific source IP or interface"),
    ("--cacert ca.pem", "Trust a private certificate authority"),
    ("--cert client.pem --key client.key", "Send a client certificate"),
    ("--tlsv1.2 --tls-max 1.2", "Force TLS 1.2"),
    ("--tlsv1.3", "Require TLS 1.3"),
    ("--compressed", "Ask for a compressed response and decompress it"),
    ("--user name:password", "Send basic authentication"),
    ("-A NetBlade", "Set the User-Agent header"),
    ("--retry 3", "Retry up to 3 times on transient errors"),
    ("--path-as-is", "Send ../ and ./ in the path unchanged"),
]


class CurlInputError(Exception):
    """The form can't be turned into a curl command. The message is written for the user."""


@dataclass
class CurlRequest:
    url: str
    method: str = "GET"
    headers: list[str] = field(default_factory=list)
    body: str = ""
    follow_redirects: bool = True
    insecure: bool = False
    ip_version: str = "Any IP version"
    http_version: str = "Any HTTP version"
    connect_to_ip: str = ""
    timeout_s: int = DEFAULT_TIMEOUT_S
    extra_args: str = ""


def _resolve_arg(url: str, ip: str) -> str:
    """Build the --resolve value host:port:ip, so curl connects to ip but keeps the host name."""
    parts = urlsplit(url if "://" in url else f"http://{url}")
    if not parts.hostname:
        raise CurlInputError("Enter a URL with a host name to use Connect to IP.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = ipaddress.ip_address(ip)
    target = f"[{address}]" if address.version == 6 else str(address)
    return f"{parts.hostname}:{port}:{target}"


def build_args(request: CurlRequest) -> list[str]:
    """Return the curl arguments the user would type, without the program name."""
    url = request.url.strip()
    if not url:
        raise CurlInputError("Enter a URL, for example https://example.com.")
    if request.timeout_s <= 0:
        raise CurlInputError("Set a timeout of 1 s or more.")

    args = ["-v"]
    method = request.method.upper()
    has_body = bool(request.body)
    if method == "HEAD":
        args.append("--head")
    elif not (method == "GET" and not has_body) and not (method == "POST" and has_body):
        # curl sends GET, or POST when there is a body, on its own. -X is only needed for the rest.
        args += ["-X", method]
    if request.follow_redirects:
        args.append("-L")
    if request.insecure:
        args.append("-k")
    ip_flag = IP_VERSIONS.get(request.ip_version)
    if ip_flag:
        args.append(ip_flag)
    http_flag = HTTP_VERSIONS.get(request.http_version)
    if http_flag:
        args.append(http_flag)
    if request.connect_to_ip.strip():
        try:
            args += ["--resolve", _resolve_arg(url, request.connect_to_ip.strip())]
        except ValueError as error:
            raise CurlInputError(
                "Enter Connect to IP as an IP address, for example 93.184.215.14."
            ) from error
    args += ["--max-time", str(request.timeout_s)]
    for header in request.headers:
        if header.strip():
            args += ["-H", header.strip()]
    if has_body:
        args += ["--data-raw", request.body]
    if request.extra_args.strip():
        try:
            args += shlex.split(request.extra_args)
        except ValueError as error:
            raise CurlInputError(
                f"Check the quotes in More options: {error}."
            ) from error
    args.append(url)
    return args


def program_name() -> str:
    # In Windows PowerShell, "curl" is an alias for Invoke-WebRequest, so show curl.exe.
    return "curl.exe" if sys.platform == "win32" else "curl"


def _quote_windows(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./:=@,+%[]" for c in arg):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def display_command(args: list[str]) -> str:
    """The command as the user would type it in a terminal on this system."""
    quote = _quote_windows if sys.platform == "win32" else shlex.quote
    return " ".join([program_name(), *(quote(arg) for arg in args)])


@dataclass
class CurlResult:
    exit_code: int
    verbose: str
    body: bytes
    body_truncated: bool
    write_out: dict
    elapsed_s: float
    cancelled: bool = False

    @property
    def status(self) -> int:
        return int(self.write_out.get("http_code") or 0)

    @property
    def error(self) -> str | None:
        if self.cancelled:
            return "Cancelled"
        if self.exit_code == 0:
            return None
        message = self.write_out.get("errormsg")
        if not message:
            # Without the write-out data, curl's own error is the last "curl: (N) ..." line.
            lines = [
                line for line in self.verbose.splitlines() if line.startswith("curl: (")
            ]
            message = (
                lines[-1].split(") ", 1)[-1]
                if lines
                else f"curl exited with code {self.exit_code}"
            )
        return message


def status_text(code: int) -> str:
    try:
        return f"{code} {HTTPStatus(code).phrase}"
    except ValueError:
        return str(code)


def find_curl() -> str:
    path = shutil.which("curl")
    if path is None:
        raise CurlInputError(
            "curl is not installed on this system. Install curl, then try again."
        )
    return path


class CurlRun:
    """Runs one curl command. cancel() may be called from another thread."""

    def __init__(self, args: list[str], timeout_s: int) -> None:
        self.args = args
        self.timeout_s = timeout_s
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None and self._process.poll() is None:
            self._process.kill()

    def run(self) -> CurlResult:
        curl = find_curl()
        with tempfile.TemporaryDirectory(prefix="netblade-curl-") as folder:
            body_path = Path(folder) / "body"
            command = [
                curl,
                *self.args,
                "--no-progress-meter",
                "-o",
                str(body_path),
                "-w",
                "%{json}",
            ]
            kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            started = time.monotonic()
            self._process = subprocess.Popen(command, **kwargs)
            if self._cancelled:
                self._process.kill()
            try:
                stdout, stderr = self._process.communicate(timeout=self.timeout_s + 15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                stdout, stderr = self._process.communicate()
            elapsed = time.monotonic() - started

            body = b""
            truncated = False
            if body_path.exists():
                with body_path.open("rb") as handle:
                    body = handle.read(MAX_BODY_BYTES + 1)
                truncated = len(body) > MAX_BODY_BYTES
                body = body[:MAX_BODY_BYTES]

        try:
            write_out = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            write_out = {}
        return CurlResult(
            exit_code=self._process.returncode,
            verbose=stderr.decode("utf-8", errors="replace").replace("\r\n", "\n"),
            body=body,
            body_truncated=truncated,
            write_out=write_out,
            elapsed_s=elapsed,
            cancelled=self._cancelled,
        )


def split_verbose(verbose: str) -> tuple[list[str], list[str]]:
    """Return (request header lines, response header lines) from curl -v output.

    With redirects there is more than one exchange; a blank line separates them.
    """
    request: list[str] = []
    response: list[str] = []
    for line in verbose.splitlines():
        if line.startswith("> "):
            text = line[2:]
            if (
                request
                and text
                and text.split(" ", 1)[0] in METHODS
                and request[-1] != ""
            ):
                request.append("")
            if text:
                request.append(text)
        elif line.startswith("< "):
            text = line[2:]
            if text.startswith("HTTP/") and response and response[-1] != "":
                response.append("")
            if text:
                response.append(text)
    return request, response


@dataclass(frozen=True)
class Phase:
    name: str
    ms: float


def timing_phases(write_out: dict) -> list[Phase]:
    """Split curl's timers into DNS, Connect, TLS, Server and Transfer phases.

    When curl follows redirects, it adds each timer up over all requests in the chain. So only
    differences between timers are used: those stay correct as sums. TLS shows only for HTTPS.
    """
    if "time_total" not in write_out:
        return []

    def t(key: str) -> float:
        return float(write_out.get(key) or 0.0)

    dns = t("time_namelookup")
    connect = max(t("time_connect"), dns)
    ready = max(
        t("time_pretransfer"), connect
    )  # Connected, TLS done, request about to go.
    first_byte = max(t("time_starttransfer"), ready)
    total = max(t("time_total"), first_byte)
    uses_tls = t("time_appconnect") > 0
    phases = [
        Phase("DNS", dns),
        Phase("Connect", connect - dns if uses_tls else ready - dns),
        Phase("TLS", ready - connect if uses_tls else 0.0),
        Phase("Server", first_byte - ready),
        Phase("Transfer", total - first_byte),
    ]
    return [
        Phase(p.name, round(p.ms * 1000, 1))
        for p in phases
        if p.ms > 0 or p.name == "Server"
    ]


def body_text(body: bytes, content_type: str | None) -> str:
    """Body for display: JSON is pretty-printed, binary is described instead of shown."""
    if b"\x00" in body[:4096]:
        return f"Binary content, {len(body):,} bytes. Not shown."
    text = body.decode("utf-8", errors="replace")
    if content_type and "json" in content_type.lower():
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return text
