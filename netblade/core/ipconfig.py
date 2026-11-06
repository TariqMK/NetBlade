"""Runs the system's IP configuration commands and parses Windows `ipconfig /all` into adapters."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field

_SECTION = re.compile(r"^(\S.*?)\s*:?\s*$")
_FIELD = re.compile(r"^\s+(\S.*?)(?:\s?\.)+\s*:\s?(.*)$")
_CONTINUATION = re.compile(r"^\s{8,}(\S.*)$")
_ADDRESS_STATE = re.compile(r"\((Preferred|Deprecated|Duplicate|Tentative)\)$")


@dataclass
class Adapter:
    kind: str  # For example "Ethernet adapter" or "Wireless LAN adapter".
    name: str  # For example "Wi-Fi".
    fields: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str) -> list[str]:
        return self.fields.get(key, [])

    def first(self, key: str) -> str:
        values = self.get(key)
        return values[0] if values else ""

    @property
    def connected(self) -> bool:
        return "disconnected" not in self.first("Media State").lower()


@dataclass
class IpConfig:
    host: dict[str, list[str]] = field(default_factory=dict)
    adapters: list[Adapter] = field(default_factory=list)


def _clean(value: str) -> str:
    return _ADDRESS_STATE.sub("", value.strip()).strip()


def parse_ipconfig_all(text: str) -> IpConfig:
    """Parse English `ipconfig /all` output. Unknown lines are ignored."""
    config = IpConfig()
    current: dict[str, list[str]] | None = None
    last_key: str | None = None

    for line in text.splitlines():
        if not line.strip():
            continue
        field_match = _FIELD.match(line)
        if field_match and current is not None:
            last_key = field_match.group(1).strip()
            value = _clean(field_match.group(2))
            current[last_key] = [value] if value else []
            continue
        continuation = _CONTINUATION.match(line)
        if continuation and current is not None and last_key is not None:
            current[last_key].append(_clean(continuation.group(1)))
            continue
        if not line[0].isspace():
            heading = _SECTION.match(line).group(1).rstrip(":").strip()
            last_key = None
            if heading.endswith("IP Configuration"):
                current = config.host
            elif " adapter " in heading:
                kind, name = heading.split(" adapter ", 1)
                adapter = Adapter(kind=f"{kind} adapter", name=name)
                config.adapters.append(adapter)
                current = adapter.fields
            else:
                current = None
    return config


def commands() -> list[list[str]]:
    """The commands that show the full IP configuration on this system."""
    if sys.platform == "win32":
        return [["ipconfig", "/all"]]
    if sys.platform == "darwin":
        return [["ifconfig"], ["route", "-n", "get", "default"], ["scutil", "--dns"]]
    return [["ip", "addr"], ["ip", "route"], ["resolvectl", "status"]]


def run_command(command: list[str]) -> str:
    """Run one command and return its output, or a short message if it isn't available."""
    kwargs: dict = {"capture_output": True, "timeout": 15}
    encoding = "utf-8"
    if sys.platform == "win32":
        # No console window flashes up from the GUI app.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        encoding = "oem"  # ipconfig writes in the console code page.
    try:
        completed = subprocess.run(command, check=False, **kwargs)
    except FileNotFoundError:
        return f"{command[0]} is not available on this system.\n"
    except subprocess.TimeoutExpired:
        return f"{' '.join(command)} didn't finish within 15 s.\n"
    output = completed.stdout + completed.stderr
    return output.decode(encoding, errors="replace").replace("\r\n", "\n")


def run_all() -> str:
    """Run every command for this system. On macOS and Linux each output gets a `$ command` heading."""
    if sys.platform == "win32":
        return run_command(commands()[0])
    parts = [f"$ {' '.join(command)}\n{run_command(command)}" for command in commands()]
    return "\n".join(parts)
