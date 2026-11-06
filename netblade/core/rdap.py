"""Whois / IP owner lookups over RDAP, the modern replacement for whois.

IANA's bootstrap files say which registry (ARIN, RIPE, a domain registry and so on) holds each
IP range, AS number or top-level domain. NetBlade then asks that registry directly over HTTPS.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from netblade import APP_NAME, __version__

BOOTSTRAP_URLS = {
    "ip4": "https://data.iana.org/rdap/ipv4.json",
    "ip6": "https://data.iana.org/rdap/ipv6.json",
    "asn": "https://data.iana.org/rdap/asn.json",
    "domain": "https://data.iana.org/rdap/dns.json",
}
PUBLIC_IP_URLS = ("https://api.ipify.org", "https://api64.ipify.org")
TIMEOUT_S = 15.0
USER_AGENT = f"{APP_NAME}/{__version__}"

_bootstrap_cache: dict[str, list] = {}
_cache_lock = threading.Lock()


class RdapError(Exception):
    """A lookup failed. The message is written for the user."""


@dataclass(frozen=True)
class Query:
    kind: str  # "ip", "asn" or "domain"
    value: str  # What goes in the RDAP path, for example "8.8.8.0/24", "13335" or "example.com".
    display: str


@dataclass
class Contact:
    roles: list[str]
    name: str = ""
    organisation: str = ""
    email: str = ""
    phone: str = ""
    handle: str = ""


@dataclass
class RdapResult:
    query: Query
    source: str  # The registry server that answered.
    rows: list[tuple[str, str]]
    contacts: list[Contact]
    raw: dict
    expires: dt.datetime | None = None
    notes: list[str] = field(default_factory=list)


def parse_query(text: str) -> Query:
    """Read an IP address or network, an AS number (AS13335 or 13335), a domain, URL or e-mail address."""
    text = text.strip()
    if not text:
        raise RdapError(
            "Enter an IP address, domain or AS number, for example 8.8.8.8, example.com or AS13335."
        )
    asn = re.fullmatch(r"(?i)(?:AS)?\s*(\d{1,10})", text)
    if asn:
        number = int(asn.group(1))
        if number > 4294967295:
            raise RdapError(f"{text} is not a valid AS number.")
        return Query("asn", str(number), f"AS{number}")
    try:
        network = ipaddress.ip_network(text, strict=False)
        value = (
            str(network.network_address) if network.num_addresses == 1 else str(network)
        )
        return Query("ip", value, value)
    except ValueError:
        pass
    if "://" in text:
        text = urlsplit(text).hostname or ""
    elif "@" in text:
        text = text.rsplit("@", 1)[1]
    domain = text.strip().strip(".").lower()
    if not re.fullmatch(
        r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain.encode("idna").decode() if domain else ""
    ):
        raise RdapError(f"{text} is not an IP address, domain or AS number.")
    domain = domain.encode("idna").decode()
    return Query("domain", domain, domain)


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/rdap+json, application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise RdapError("not found") from error
        if error.code == 429:
            raise RdapError(
                "The registry is limiting requests. Wait a minute, then try again."
            ) from error
        raise RdapError(
            f"The registry answered with an error (HTTP {error.code})."
        ) from error
    except urllib.error.URLError as error:
        raise RdapError(
            f"Can't reach {urlsplit(url).hostname}: {error.reason}. Check the internet connection."
        ) from error
    except TimeoutError as error:
        raise RdapError(
            f"{urlsplit(url).hostname} didn't answer within {TIMEOUT_S:g} s."
        ) from error
    except json.JSONDecodeError as error:
        raise RdapError("The registry sent an answer NetBlade can't read.") from error


def _services(kind: str) -> list:
    with _cache_lock:
        if kind not in _bootstrap_cache:
            _bootstrap_cache[kind] = _get_json(BOOTSTRAP_URLS[kind]).get("services", [])
        return _bootstrap_cache[kind]


def find_server(query: Query, services: list | None = None) -> str | None:
    """The RDAP base URL for a query, from IANA's bootstrap data. Most specific match wins."""
    if query.kind == "ip":
        network = ipaddress.ip_network(query.value, strict=False)
        services = (
            services
            if services is not None
            else _services("ip4" if network.version == 4 else "ip6")
        )
        best, best_len = None, -1
        for entries, urls in services:
            for entry in entries:
                candidate = ipaddress.ip_network(entry, strict=False)
                if (
                    candidate.version == network.version
                    and network.subnet_of(candidate)
                    and candidate.prefixlen > best_len
                ):
                    best, best_len = urls, candidate.prefixlen
        return _https(best)
    if query.kind == "asn":
        services = services if services is not None else _services("asn")
        number = int(query.value)
        for entries, urls in services:
            for entry in entries:
                low, _, high = entry.partition("-")
                if int(low) <= number <= int(high or low):
                    return _https(urls)
        return None
    services = services if services is not None else _services("domain")
    labels = query.value.split(".")
    for size in range(len(labels) - 1, 0, -1):  # "co.uk" before "uk".
        suffix = ".".join(labels[-size:])
        for entries, urls in services:
            if suffix in entries:
                return _https(urls)
    return None


def _https(urls: list[str] | None) -> str | None:
    if not urls:
        return None
    preferred = next((u for u in urls if u.startswith("https://")), urls[0])
    return preferred if preferred.endswith("/") else preferred + "/"


def lookup(query: Query) -> RdapResult:
    server = find_server(query)
    if server is None:
        if query.kind == "domain":
            tld = query.value.rsplit(".", 1)[-1]
            raise RdapError(
                f"No RDAP service is listed for .{tld}. The domain may not exist, "
                "or its registry offers lookups only on its own website."
            )
        raise RdapError(f"No registry lists {query.display} in the IANA directory.")
    path = {"ip": "ip", "asn": "autnum", "domain": "domain"}[query.kind]

    if query.kind == "domain":
        # "www.shop.example.com" is not registered itself; try shorter names until one is found.
        labels = query.value.split(".")
        for start in range(len(labels) - 1):
            name = ".".join(labels[start:])
            try:
                data = _get_json(f"{server}{path}/{name}")
                return parse(Query("domain", name, name), data, server)
            except RdapError as error:
                if str(error) != "not found":
                    raise
        raise RdapError(
            f"{query.display} is not registered, or the registry has no record of it."
        )
    try:
        data = _get_json(f"{server}{path}/{query.value}")
    except RdapError as error:
        if str(error) == "not found":
            raise RdapError(
                f"The registry has no record of {query.display}."
            ) from error
        raise
    return parse(query, data, server)


def public_ip() -> str:
    """This network's public IP address, from ipify. Tries IPv4 first, then IPv6."""
    for url in PUBLIC_IP_URLS:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                text = response.read().decode().strip()
                ipaddress.ip_address(text)
                return text
        except (OSError, ValueError):
            continue
    raise RdapError("Can't find the public IP address. Check the internet connection.")


# Parsing


def _vcard(entity: dict) -> dict[str, str]:
    """Pick name, organisation, e-mail and phone out of a jCard (vcardArray)."""
    found: dict[str, str] = {}
    card = entity.get("vcardArray")
    if not (isinstance(card, list) and len(card) == 2):
        return found
    for item in card[1]:
        if not (isinstance(item, list) and len(item) >= 4):
            continue
        prop, _params, _type, value = item[0], item[1], item[2], item[3]
        if isinstance(value, list):
            value = " ".join(str(v) for v in value if v)
        value = str(value).strip()
        if value.startswith(("tel:", "mailto:")):
            value = value.split(":", 1)[1]
        if prop in ("fn", "org", "email", "tel") and value and prop not in found:
            found[prop] = value
    return found


def _contacts(entities: list, inherited: list[str] | None = None) -> list[Contact]:
    contacts: list[Contact] = []
    for entity in entities or []:
        card = _vcard(entity)
        roles = entity.get("roles") or inherited or []
        contacts.append(
            Contact(
                roles=[r.replace("_", " ") for r in roles],
                name=card.get("fn", ""),
                organisation=card.get("org", ""),
                email=card.get("email", ""),
                phone=card.get("tel", ""),
                handle=entity.get("handle", ""),
            )
        )
        contacts += _contacts(
            entity.get("entities", [])
        )  # A registrar often holds its abuse contact.
    return contacts


def _event(data: dict, action: str) -> dt.datetime | None:
    for event in data.get("events", []):
        if event.get("eventAction") == action and event.get("eventDate"):
            try:
                return dt.datetime.fromisoformat(event["eventDate"])
            except ValueError:
                return None
    return None


def _date(value: dt.datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _first(contacts: list[Contact], role: str) -> Contact | None:
    return next((c for c in contacts if role in c.roles), None)


def registry_name(server: str) -> str:
    host = urlsplit(server).hostname or server
    for key, name in (
        ("arin", "ARIN"), ("ripe", "RIPE NCC"), ("apnic", "APNIC"), ("lacnic", "LACNIC"), ("afrinic", "AFRINIC"),
    ):  # fmt: skip
        if key in host:
            return f"{name} ({host})"
    return host


def parse(query: Query, data: dict, server: str) -> RdapResult:
    contacts = _contacts(data.get("entities", []))
    rows: list[tuple[str, str]] = []
    holder = _first(contacts, "registrant")
    abuse = _first(contacts, "abuse")
    registered, changed = _event(data, "registration"), _event(data, "last changed")
    expires = _event(data, "expiration")

    if query.kind == "ip":
        cidrs = [
            f"{c.get('v4prefix') or c.get('v6prefix')}/{c.get('length')}"
            for c in data.get("cidr0_cidrs", [])
            if c.get("length") is not None
        ]
        rows += [
            ("Network name", data.get("name", "")),
            (
                "Range",
                f"{data.get('startAddress', '')} – {data.get('endAddress', '')}".strip(
                    " –"
                ),
            ),
            ("CIDR", ", ".join(cidrs)),
            ("Owner", (holder.organisation or holder.name) if holder else ""),
            ("Type", data.get("type", "")),
            ("Country", data.get("country", "")),
            ("Handle", data.get("handle", "")),
            ("Parent", data.get("parentHandle", "")),
        ]
    elif query.kind == "asn":
        start, end = data.get("startAutnum"), data.get("endAutnum")
        number = f"AS{start}" if start == end or end is None else f"AS{start} – AS{end}"
        rows += [
            ("AS number", number),
            ("Name", data.get("name", "")),
            ("Owner", (holder.organisation or holder.name) if holder else ""),
            ("Type", data.get("type", "")),
            ("Country", data.get("country", "")),
            ("Handle", data.get("handle", "")),
        ]
    else:
        registrar = _first(contacts, "registrar")
        nameservers = [
            ns.get("ldhName", "").lower().rstrip(".")
            for ns in data.get("nameservers", [])
            if ns.get("ldhName")
        ]
        signed = (data.get("secureDNS") or {}).get("delegationSigned")
        rows += [
            ("Domain", data.get("ldhName", query.value).lower()),
            (
                "Registrar",
                (registrar.name or registrar.organisation) if registrar else "",
            ),
            ("Owner", (holder.organisation or holder.name) if holder else ""),
            ("Status", ", ".join(data.get("status", []))),
            ("Name servers", "\n".join(nameservers)),
            (
                "DNSSEC",
                "" if signed is None else ("Signed" if signed else "Not signed"),
            ),
            ("Expires", _date(expires)),
        ]
    rows += [
        ("Abuse contact", abuse.email or abuse.phone if abuse else ""),
        ("Registered", _date(registered)),
        ("Last changed", _date(changed)),
        ("Registry", registry_name(server)),
    ]
    notes = [n.get("title", "") for n in data.get("notices", []) if n.get("title")]
    return RdapResult(
        query=query,
        source=server,
        rows=[(label, value) for label, value in rows if value],
        contacts=[
            c for c in contacts if c.name or c.organisation or c.email or c.phone
        ],
        raw=data,
        expires=expires,
        notes=notes,
    )
