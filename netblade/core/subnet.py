"""Subnet calculations for IPv4 and IPv6: details, address type, binary form and splitting."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPInterface = ipaddress.IPv4Interface | ipaddress.IPv6Interface

MAX_SPLIT_ROWS = 1024  # Larger splits list only the first rows.

_V4_TYPES = [
    (ipaddress.ip_network("0.0.0.0/8"), "This network (RFC 1122)"),
    (ipaddress.ip_network("10.0.0.0/8"), "Private (RFC 1918)"),
    (
        ipaddress.ip_network("100.64.0.0/10"),
        "Shared address space, carrier-grade NAT (RFC 6598)",
    ),
    (ipaddress.ip_network("127.0.0.0/8"), "Loopback"),
    (ipaddress.ip_network("169.254.0.0/16"), "Link-local (APIPA)"),
    (ipaddress.ip_network("172.16.0.0/12"), "Private (RFC 1918)"),
    (ipaddress.ip_network("192.0.2.0/24"), "Documentation (TEST-NET-1)"),
    (ipaddress.ip_network("192.168.0.0/16"), "Private (RFC 1918)"),
    (ipaddress.ip_network("198.18.0.0/15"), "Benchmark testing (RFC 2544)"),
    (ipaddress.ip_network("198.51.100.0/24"), "Documentation (TEST-NET-2)"),
    (ipaddress.ip_network("203.0.113.0/24"), "Documentation (TEST-NET-3)"),
    (ipaddress.ip_network("224.0.0.0/4"), "Multicast"),
    (ipaddress.ip_network("255.255.255.255/32"), "Limited broadcast"),
    (ipaddress.ip_network("240.0.0.0/4"), "Reserved (class E)"),
]

_V6_TYPES = [
    (ipaddress.ip_network("::1/128"), "Loopback"),
    (ipaddress.ip_network("::/128"), "Unspecified"),
    (ipaddress.ip_network("::ffff:0:0/96"), "IPv4-mapped"),
    (ipaddress.ip_network("64:ff9b::/96"), "NAT64 (RFC 6052)"),
    (ipaddress.ip_network("2001:db8::/32"), "Documentation"),
    (ipaddress.ip_network("2002::/16"), "6to4"),
    (ipaddress.ip_network("fc00::/7"), "Unique local (ULA)"),
    (ipaddress.ip_network("fe80::/10"), "Link-local"),
    (ipaddress.ip_network("ff00::/8"), "Multicast"),
    (ipaddress.ip_network("2000::/3"), "Global unicast"),
]


class SubnetInputError(Exception):
    """The input is not an address with a prefix. The message is written for the user."""


def parse(text: str) -> IPInterface:
    """Read "192.168.1.10/24", "192.168.1.10 255.255.255.0", "192.168.1.10/255.255.255.0" or "2001:db8::1/64"."""
    text = text.strip()
    if not text:
        raise SubnetInputError(
            "Enter an address with a prefix, for example 192.168.1.10/24."
        )
    parts = re.split(r"\s*/\s*|\s+", text)
    if len(parts) == 1:
        raise SubnetInputError(
            "Add a prefix length or mask, for example /24 or 255.255.255.0."
        )
    if len(parts) != 2:
        raise SubnetInputError(
            "Enter one address and one prefix, for example 192.168.1.10/24."
        )
    address, prefix = parts
    try:
        return ipaddress.ip_interface(f"{address}/{prefix}")
    except ValueError as error:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise SubnetInputError(
                f"{address} is not a valid IPv4 or IPv6 address."
            ) from error
        raise SubnetInputError(
            f"{prefix} is not a valid prefix length or mask. Masks must be contiguous, for example 255.255.255.0."
        ) from error


def address_type(address: IPAddress) -> str:
    table = _V4_TYPES if address.version == 4 else _V6_TYPES
    for network, label in table:
        if address in network:
            return label
    return "Public" if address.version == 4 else "Reserved"


def ipv4_class(address: ipaddress.IPv4Address) -> str:
    first = int(address) >> 24
    for limit, name in ((127, "A"), (191, "B"), (223, "C"), (239, "D (multicast)")):
        if first <= limit:
            return name
    return "E (reserved)"


def usable_range(network: IPNetwork) -> tuple[IPAddress, IPAddress, int]:
    """First and last usable host and the host count. /31 and /32 (and /127, /128) follow RFC 3021."""
    if network.version == 4 and network.prefixlen <= 30:
        return (
            network.network_address + 1,
            network.broadcast_address - 1,
            network.num_addresses - 2,
        )
    # IPv6 has no broadcast, so every address is usable; /31 and /32 use all addresses too.
    return network.network_address, network.broadcast_address, network.num_addresses


def format_count(count: int, compact: bool = False) -> str:
    """Address counts. Very large IPv6 counts also show the power of two, or only that when compact."""
    if count > 2**32:
        power = f"2^{count.bit_length() - 1}"
        return power if compact else f"{count:,} ({power})"
    return f"{count:,}"


@dataclass(frozen=True)
class Row:
    label: str
    value: str


def details(interface: IPInterface) -> list[Row]:
    """The rows shown in the results card, in reading order."""
    address = interface.ip
    network = interface.network
    first, last, hosts = usable_range(network)
    if address.version == 4:
        rows = [
            Row("Address", str(address)),
            Row("Network", str(network)),
            Row("Netmask", str(network.netmask)),
            Row("Wildcard mask", str(network.hostmask)),
            Row("Broadcast", str(network.broadcast_address)),
            Row("First usable", str(first)),
            Row("Last usable", str(last)),
            Row("Usable hosts", f"{hosts:,}"),
            Row("Total addresses", f"{network.num_addresses:,}"),
            Row("Address type", address_type(address)),
            Row("Class", ipv4_class(address)),
            Row("Reverse DNS zone", reverse_zone(network)),
        ]
        if network.prefixlen in (31, 32):
            rows[7] = Row(
                "Usable hosts",
                f"{hosts:,} (point-to-point, RFC 3021)"
                if network.prefixlen == 31
                else "1",
            )
        return rows
    return [
        Row("Address", address.compressed),
        Row("Expanded", address.exploded),
        Row("Network", network.compressed),
        Row("Prefix length", f"/{network.prefixlen}"),
        Row("First address", first.compressed),
        Row("Last address", last.compressed),
        Row("Addresses", format_count(network.num_addresses)),
        Row("Address type", address_type(address)),
        Row("Reverse DNS zone", reverse_zone(network)),
    ]


def reverse_zone(network: IPNetwork) -> str:
    """The reverse DNS zone that holds this network. Rounded out to whole octets or nibbles."""
    if network.version == 4:
        octets = network.prefixlen // 8
        labels = str(network.network_address).split(".")[:octets]
        return (
            ".".join(reversed(labels)) + ".in-addr.arpa" if labels else "in-addr.arpa"
        )
    nibbles = network.prefixlen // 4
    digits = network.network_address.exploded.replace(":", "")[:nibbles]
    return ".".join(reversed(digits)) + ".ip6.arpa" if digits else "ip6.arpa"


def binary_groups(address: IPAddress) -> list[str]:
    """Bits grouped as octets for IPv4 and 16-bit groups for IPv6."""
    bits = format(int(address), f"0{address.max_prefixlen}b")
    size = 8 if address.version == 4 else 16
    return [bits[i : i + size] for i in range(0, len(bits), size)]


def split_options(network: IPNetwork, limit: int = 16) -> list[int]:
    """New prefix lengths offered for splitting: the next `limit` longer prefixes."""
    return list(
        range(
            network.prefixlen + 1,
            min(network.max_prefixlen, network.prefixlen + limit) + 1,
        )
    )


@dataclass(frozen=True)
class SplitRow:
    network: str
    first: str
    last: str
    broadcast: str
    hosts: str


def split(network: IPNetwork, new_prefix: int) -> tuple[list[SplitRow], int]:
    """Subnets of the new prefix, up to MAX_SPLIT_ROWS, and the total number of subnets."""
    if not network.prefixlen < new_prefix <= network.max_prefixlen:
        raise SubnetInputError(
            f"Choose a prefix between /{network.prefixlen + 1} and /{network.max_prefixlen}."
        )
    total = 2 ** (new_prefix - network.prefixlen)
    rows = []
    for index, subnet in enumerate(network.subnets(new_prefix=new_prefix)):
        if index >= MAX_SPLIT_ROWS:
            break
        first, last, hosts = usable_range(subnet)
        broadcast = str(subnet.broadcast_address) if subnet.version == 4 else "—"
        rows.append(
            SplitRow(
                subnet.compressed,
                first.compressed,
                last.compressed,
                broadcast,
                format_count(hosts, compact=True),
            )
        )
    return rows, total


@dataclass(frozen=True)
class Summary:
    version: int
    networks: list[
        IPNetwork
    ]  # The exact summary: the fewest networks that cover the input and nothing else.
    supernet: IPNetwork  # The single smallest network that covers all the input.
    input_addresses: int
    extra_addresses: int  # Addresses inside the supernet that the input did not cover.


def summarise(text: str) -> tuple[list[Summary], list[str]]:
    """Summarise routes from pasted text. Returns one Summary per IP version and a list of problems.

    Accepts networks and single addresses, one per line or separated by commas, semicolons or spaces.
    Host bits are ignored, so 10.0.0.5/24 counts as 10.0.0.0/24.
    """
    problems: list[str] = []
    by_version: dict[int, list[IPNetwork]] = {4: [], 6: []}
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            problems.append(f"{token} is not a network or address, so it was skipped.")
            continue
        if (
            "/" in token
            and ipaddress.ip_address(token.split("/")[0]) != network.network_address
        ):
            problems.append(f"{token} has host bits set, so it counts as {network}.")
        by_version[network.version].append(network)
    summaries = []
    for version, networks in by_version.items():
        if not networks:
            continue
        exact = list(ipaddress.collapse_addresses(networks))
        supernet = exact[0]
        while not all(n.subnet_of(supernet) for n in exact):
            supernet = supernet.supernet()
        covered = sum(n.num_addresses for n in exact)
        summaries.append(
            Summary(version, exact, supernet, covered, supernet.num_addresses - covered)
        )
    return summaries, problems
