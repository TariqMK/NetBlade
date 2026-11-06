"""DNS lookups against the system resolver or a chosen nameserver, using dnspython."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass

import dns.exception
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.resolver
import dns.reversename

RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "PTR", "CAA"]
SYSTEM_DEFAULT = "System default"
PRESET_NAMESERVERS = [
    ("1.1.1.1", "Cloudflare"),
    ("8.8.8.8", "Google"),
    ("9.9.9.9", "Quad9"),
]
LOOKUP_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class DnsRecord:
    name: str
    type: str
    ttl: int
    value: str


@dataclass(frozen=True)
class LookupResult:
    records: list[DnsRecord]
    nameserver: str
    elapsed_ms: float


class LookupFailed(Exception):
    """A lookup failed. The message is written for the user."""


def is_ip_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip())
    except ValueError:
        return False
    return True


def preset_label(ip: str, name: str) -> str:
    return f"{ip} ({name})"


def parse_nameserver(text: str) -> str | None:
    """Turn the nameserver field into an IP address, or None for the system default.

    Accepts "System default", a preset label such as "1.1.1.1 (Cloudflare)" or a typed IP address.
    Raises LookupFailed when the text is not an IP address.
    """
    text = text.strip()
    if not text or text == SYSTEM_DEFAULT:
        return None
    candidate = text.split(" ", 1)[0]
    if not is_ip_address(candidate):
        raise LookupFailed(
            "Enter the nameserver as an IP address, for example 1.1.1.1."
        )
    return candidate


def query_name(name: str, record_type: str) -> str:
    """Return the name to query. A PTR lookup of an IP address becomes its reverse name."""
    name = name.strip()
    if record_type == "PTR" and is_ip_address(name):
        return dns.reversename.from_address(name).to_text()
    return name


def lookup(
    name: str,
    record_type: str,
    nameserver: str | None = None,
    timeout_s: float = LOOKUP_TIMEOUT_S,
) -> LookupResult:
    """Look up one record type. Raises LookupFailed with a user-facing message on failure."""
    if not name.strip():
        raise LookupFailed("Enter a name to look up.")
    try:
        resolver = dns.resolver.Resolver(configure=nameserver is None)
    except dns.resolver.NoResolverConfiguration as error:
        raise LookupFailed(
            "No system nameserver is set up. Choose a nameserver."
        ) from error
    if nameserver:
        resolver.nameservers = [nameserver]
    resolver.lifetime = timeout_s
    server_label = nameserver or (
        resolver.nameservers[0] if resolver.nameservers else "the system nameserver"
    )

    start = time.perf_counter()
    try:
        answer = resolver.resolve(
            query_name(name, record_type), record_type, raise_on_no_answer=False
        )
    except dns.resolver.NXDOMAIN as error:
        raise LookupFailed(f"{name.strip()} doesn't exist (NXDOMAIN).") from error
    except dns.resolver.LifetimeTimeout as error:
        raise LookupFailed(
            f"{server_label} didn't answer within {timeout_s:g} s. Check the nameserver address."
        ) from error
    except dns.resolver.NoNameservers as error:
        raise LookupFailed(
            f"{server_label} couldn't answer. The server failed or refused the query."
        ) from error
    except dns.exception.DNSException as error:
        raise LookupFailed(f"Can't look up {name.strip()}: {error}") from error
    elapsed_ms = (time.perf_counter() - start) * 1000

    records = []
    for rrset in answer.response.answer:
        # MX by preference and SRV by priority, so the record a client would try first comes first.
        ordered = sorted(
            rrset,
            key=lambda rd: (
                getattr(rd, "preference", getattr(rd, "priority", 0)),
                rd.to_text(),
            ),
        )
        records += [
            DnsRecord(
                name=rrset.name.to_text(omit_final_dot=True),
                type=dns.rdatatype.to_text(rrset.rdtype),
                ttl=rrset.ttl,
                value=rdata.to_text(),
            )
            for rdata in ordered
        ]
    return LookupResult(
        records, getattr(answer, "nameserver", None) or server_label, elapsed_ms
    )


def self_check() -> None:
    """Load the record-type modules dnspython imports on demand. Proves a packaged build bundled them."""
    for record_type in RECORD_TYPES:
        dns.rdata.get_rdata_class(
            dns.rdataclass.IN, dns.rdatatype.from_text(record_type)
        )
