"""MAC address vendor lookup from the IEEE registry (MA-L, MA-M and MA-S blocks).

The IEEE lists are downloaded on request into a local folder and then used offline.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from netblade import APP_NAME, __version__

# (file name, prefix length in bits, URL). Longer prefixes are more specific and are checked first.
SOURCES = [
    ("oui36.csv", 36, "https://standards-oui.ieee.org/oui36/oui36.csv"),
    ("mam.csv", 28, "https://standards-oui.ieee.org/oui28/mam.csv"),
    ("oui.csv", 24, "https://standards-oui.ieee.org/oui/oui.csv"),
]
META_FILE = "netblade-oui.json"
TIMEOUT_S = 60.0
USER_AGENT = f"{APP_NAME}/{__version__}"


class OuiError(Exception):
    """A lookup or download failed. The message is written for the user."""


@dataclass(frozen=True)
class Vendor:
    name: str
    address: str
    block: str  # "MA-L", "MA-M" or "MA-S"
    prefix: str  # The assigned prefix, for example "28:6F:B9".


class OuiDatabase:
    def __init__(
        self, tables: dict[int, dict[int, Vendor]], downloaded: str = ""
    ) -> None:
        self.tables = tables  # prefix length in bits -> {prefix as int: Vendor}
        self.downloaded = downloaded

    def __len__(self) -> int:
        return sum(len(table) for table in self.tables.values())

    def lookup(self, mac: int, known_bits: int = 48) -> Vendor | None:
        """The most specific vendor for a 48-bit MAC. known_bits limits the search when only a prefix was given."""
        for bits in sorted(self.tables, reverse=True):
            if bits > known_bits:
                continue
            vendor = self.tables[bits].get(mac >> (48 - bits))
            if vendor is not None:
                return vendor
        return None


def parse_csv(text: str, bits: int) -> dict[int, Vendor]:
    table: dict[int, Vendor] = {}
    for row in csv.DictReader(io.StringIO(text)):
        assignment = (row.get("Assignment") or "").strip()
        if not assignment:
            continue
        try:
            prefix = int(assignment, 16)
        except ValueError:
            continue
        hex_digits = bits // 4
        padded = assignment.upper().zfill(hex_digits)
        pretty = ":".join(padded[i : i + 2] for i in range(0, len(padded) - 1, 2)) + (
            padded[-1] if len(padded) % 2 else ""
        )
        table[prefix] = Vendor(
            name=" ".join((row.get("Organization Name") or "").split()),
            address=" ".join((row.get("Organization Address") or "").split()),
            block=(row.get("Registry") or "").strip(),
            prefix=pretty,
        )
    return table


def load(folder: Path) -> OuiDatabase | None:
    """The downloaded database, or None when it has not been downloaded yet."""
    tables: dict[int, dict[int, Vendor]] = {}
    for name, bits, _url in SOURCES:
        path = folder / name
        if path.exists():
            tables[bits] = parse_csv(
                path.read_text(encoding="utf-8", errors="replace"), bits
            )
    if 24 not in tables:
        return None
    downloaded = ""
    meta = folder / META_FILE
    if meta.exists():
        try:
            downloaded = json.loads(meta.read_text(encoding="utf-8")).get(
                "downloaded", ""
            )
        except (OSError, json.JSONDecodeError):
            downloaded = ""
    return OuiDatabase(tables, downloaded)


def download(
    folder: Path, progress: Callable[[str], None] | None = None
) -> OuiDatabase:
    """Download the three IEEE lists into folder. Files are replaced only after a full download."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, _bits, url in SOURCES:
        if progress is not None:
            progress(f"Downloading {name}…")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise OuiError(
                f"Can't download the IEEE list {name}: {error}. Check the internet connection."
            ) from error
        if not data.startswith(b"Registry,"):
            raise OuiError(
                f"The IEEE server sent something other than the list {name}. Try again later."
            )
        temporary = folder / f"{name}.part"
        temporary.write_bytes(data)
        temporary.replace(folder / name)
    (folder / META_FILE).write_text(
        json.dumps({"downloaded": dt.date.today().isoformat()}), encoding="utf-8"
    )
    database = load(folder)
    if database is None:
        raise OuiError("The downloaded IEEE lists could not be read.")
    return database


@dataclass(frozen=True)
class MacAddress:
    value: int  # 48-bit value; for a prefix, the missing bits are 0.
    known_bits: int  # 48 for a full MAC, fewer when only a prefix was typed.
    text: str  # As typed.

    @property
    def multicast(self) -> bool:
        return bool((self.value >> 40) & 0x01)

    @property
    def locally_administered(self) -> bool:
        return bool((self.value >> 40) & 0x02)

    @property
    def is_full(self) -> bool:
        return self.known_bits == 48

    def formats(self) -> dict[str, str]:
        digits = format(self.value, "012x")[: self.known_bits // 4]
        pairs = [digits[i : i + 2] for i in range(0, len(digits), 2)]
        result = {
            "Colon": ":".join(pairs),
            "Hyphen": "-".join(pairs).upper(),
            "Bare": digits.upper(),
        }
        if self.is_full:
            result["Cisco"] = ".".join(digits[i : i + 4] for i in range(0, 12, 4))
        return result


def parse_mac(text: str) -> MacAddress:
    """Read aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF, aabb.ccdd.eeff, aabbccddeeff, or a prefix such as AA:BB:CC."""
    digits = re.sub(r"[\s:.\-]", "", text.strip())
    if not re.fullmatch(r"[0-9a-fA-F]+", digits or "x"):
        raise OuiError(f"{text.strip()} is not a MAC address.")
    if len(digits) > 12 or len(digits) < 6:
        raise OuiError(
            f"{text.strip()} has {len(digits)} hex digits. A MAC has 12; a vendor prefix has at least 6."
        )
    known_bits = len(digits) * 4
    return MacAddress(int(digits.ljust(12, "0"), 16), known_bits, text.strip())


def split_input(text: str) -> list[str]:
    """MACs from pasted text: one per line, or separated by commas, semicolons or spaces."""
    items = [item for item in re.split(r"[\s,;]+", text.strip()) if item]
    return list(dict.fromkeys(items))


@dataclass(frozen=True)
class MacResult:
    text: str
    mac: str  # Normalised, for example "28:6f:b9:12:34:56".
    vendor: str
    address: str
    notes: str
    ok: bool


def _special_address(mac: MacAddress) -> str:
    if mac.is_full and mac.value == 0xFFFFFFFFFFFF:
        return "Broadcast address (all devices on the network)"
    if mac.value >> 24 == 0x01005E and mac.known_bits >= 24:
        return "IPv4 multicast address (01:00:5E), not a device"
    if mac.value >> 32 == 0x3333 and mac.known_bits >= 16:
        return "IPv6 multicast address (33:33), not a device"
    return ""


def describe(text: str, database: OuiDatabase | None) -> MacResult:
    try:
        mac = parse_mac(text)
    except OuiError as error:
        return MacResult(text, "", "", "", str(error), False)
    notes = []
    special = _special_address(mac)
    if special:
        notes.append(special)
    elif mac.multicast:
        notes.append("Multicast address, not a device")
    if mac.locally_administered:
        notes.append(
            "Locally administered: a random (private) or virtual address, so it has no vendor"
        )
    vendor = (
        database.lookup(mac.value, mac.known_bits) if database is not None else None
    )
    if (
        vendor is None
        and database is not None
        and not mac.locally_administered
        and not mac.multicast
    ):
        notes.append("Not in the IEEE list")
    if vendor is not None:
        notes.insert(0, f"{vendor.block} {vendor.prefix}")
    return MacResult(
        text=text,
        mac=mac.formats()["Colon"],
        vendor=vendor.name if vendor else "",
        address=vendor.address if vendor else "",
        notes=" · ".join(notes),
        ok=True,
    )
