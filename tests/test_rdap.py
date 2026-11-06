"""Checks RDAP query parsing, registry selection from IANA bootstrap data, and answer parsing. No network."""

import pytest

from netblade.core.rdap import (
    Query,
    RdapError,
    find_server,
    parse,
    parse_query,
    registry_name,
)


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("8.8.8.8", "ip", "8.8.8.8"),
        ("8.8.8.0/24", "ip", "8.8.8.0/24"),
        ("8.8.8.5/24", "ip", "8.8.8.0/24"),
        ("2001:db8::1", "ip", "2001:db8::1"),
        ("AS13335", "asn", "13335"),
        ("as 64512", "asn", "64512"),
        ("13335", "asn", "13335"),
        ("Example.COM.", "domain", "example.com"),
        ("https://www.example.com/path", "domain", "www.example.com"),
        ("someone@example.org", "domain", "example.org"),
        ("bücher.de", "domain", "xn--bcher-kva.de"),
    ],
)
def test_parse_query(text, kind, value):
    query = parse_query(text)
    assert (query.kind, query.value) == (kind, value)


@pytest.mark.parametrize("text", ["", "not a thing!", "AS99999999999"])
def test_parse_query_errors(text):
    with pytest.raises(RdapError):
        parse_query(text)


IP4_SERVICES = [
    [
        ["8.0.0.0/8"],
        ["https://rdap.arin.net/registry/", "http://rdap.arin.net/registry/"],
    ],
    [["8.8.0.0/16"], ["https://more-specific.example/rdap"]],
    [["185.0.0.0/8"], ["https://rdap.db.ripe.net/"]],
]
ASN_SERVICES = [
    [["13312-18431"], ["https://rdap.arin.net/registry/"]],
    [["1-1876"], ["https://rdap.ripe/"]],
]
DNS_SERVICES = [
    [["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
    [["uk"], ["https://rdap.nominet.uk/uk/"]],
]


def test_find_server_picks_most_specific_https():
    assert (
        find_server(Query("ip", "8.8.8.8", "8.8.8.8"), IP4_SERVICES)
        == "https://more-specific.example/rdap/"
    )
    assert (
        find_server(Query("ip", "8.1.1.1", "8.1.1.1"), IP4_SERVICES)
        == "https://rdap.arin.net/registry/"
    )
    assert find_server(Query("ip", "9.9.9.9", "9.9.9.9"), IP4_SERVICES) is None
    assert (
        find_server(Query("asn", "13335", "AS13335"), ASN_SERVICES)
        == "https://rdap.arin.net/registry/"
    )
    assert (
        find_server(Query("domain", "bbc.co.uk", "bbc.co.uk"), DNS_SERVICES)
        == "https://rdap.nominet.uk/uk/"
    )
    assert (
        find_server(Query("domain", "example.org", "example.org"), DNS_SERVICES) is None
    )


def vcard(name, email="", org=""):
    items = [["version", {}, "text", "4.0"], ["fn", {}, "text", name]]
    if email:
        items.append(["email", {}, "text", email])
    if org:
        items.append(["org", {}, "text", org])
    return ["vcard", items]


IP_ANSWER = {
    "objectClassName": "ip network",
    "handle": "NET-8-8-8-0-2",
    "startAddress": "8.8.8.0",
    "endAddress": "8.8.8.255",
    "name": "GOGL",
    "type": "DIRECT ALLOCATION",
    "cidr0_cidrs": [{"v4prefix": "8.8.8.0", "length": 24}],
    "events": [
        {"eventAction": "registration", "eventDate": "2014-03-14T16:52:05-04:00"},
        {"eventAction": "last changed", "eventDate": "2014-03-14T16:52:05Z"},
    ],
    "entities": [
        {
            "roles": ["registrant"],
            "handle": "GOGL",
            "vcardArray": vcard("Google LLC"),
            "entities": [
                {
                    "roles": ["abuse"],
                    "handle": "ABUSE5250-ARIN",
                    "vcardArray": vcard("Abuse", "network-abuse@google.com"),
                }
            ],
        }
    ],
}


def test_parse_ip_answer():
    result = parse(
        Query("ip", "8.8.8.8", "8.8.8.8"), IP_ANSWER, "https://rdap.arin.net/registry/"
    )
    rows = dict(result.rows)
    assert rows["Network name"] == "GOGL"
    assert rows["Range"] == "8.8.8.0 – 8.8.8.255"
    assert rows["CIDR"] == "8.8.8.0/24"
    assert rows["Owner"] == "Google LLC"
    assert (
        rows["Abuse contact"] == "network-abuse@google.com"
    )  # Found in a nested entity.
    assert rows["Registered"] == "2014-03-14"
    assert rows["Registry"] == "ARIN (rdap.arin.net)"
    assert [c.roles for c in result.contacts] == [["registrant"], ["abuse"]]


DOMAIN_ANSWER = {
    "objectClassName": "domain",
    "ldhName": "EXAMPLE.COM",
    "status": ["client transfer prohibited"],
    "secureDNS": {"delegationSigned": True},
    "nameservers": [
        {"ldhName": "A.IANA-SERVERS.NET."},
        {"ldhName": "b.iana-servers.net"},
    ],
    "events": [{"eventAction": "expiration", "eventDate": "2030-08-13T04:00:00Z"}],
    "entities": [
        {
            "roles": ["registrar"],
            "vcardArray": vcard("RESERVED-Internet Assigned Numbers Authority"),
        }
    ],
}


def test_parse_domain_answer():
    result = parse(
        Query("domain", "example.com", "example.com"),
        DOMAIN_ANSWER,
        "https://rdap.verisign.com/com/v1/",
    )
    rows = dict(result.rows)
    assert rows["Domain"] == "example.com"
    assert rows["Registrar"] == "RESERVED-Internet Assigned Numbers Authority"
    assert rows["Name servers"] == "a.iana-servers.net\nb.iana-servers.net"
    assert rows["DNSSEC"] == "Signed"
    assert rows["Expires"] == "2030-08-13"
    assert result.expires.year == 2030
    assert "Owner" not in rows  # Empty rows are left out.


def test_parse_asn_answer():
    data = {
        "startAutnum": 13335,
        "endAutnum": 13335,
        "name": "CLOUDFLARENET",
        "handle": "AS13335",
    }
    rows = dict(
        parse(
            Query("asn", "13335", "AS13335"), data, "https://rdap.arin.net/registry/"
        ).rows
    )
    assert rows["AS number"] == "AS13335"
    assert rows["Name"] == "CLOUDFLARENET"


def test_registry_name():
    assert registry_name("https://rdap.db.ripe.net/") == "RIPE NCC (rdap.db.ripe.net)"
    assert registry_name("https://rdap.nominet.uk/uk/") == "rdap.nominet.uk"
