"""Checks subnet parsing, IPv4 and IPv6 details, binary groups, reverse zones and splitting."""

import pytest

from netblade.core.subnet import (
    MAX_SPLIT_ROWS,
    SubnetInputError,
    address_type,
    binary_groups,
    details,
    parse,
    reverse_zone,
    split,
    split_options,
)


def as_dict(interface):
    return {row.label: row.value for row in details(interface)}


@pytest.mark.parametrize(
    "text",
    [
        "192.168.1.10/24",
        "192.168.1.10 255.255.255.0",
        "192.168.1.10/255.255.255.0",
        " 192.168.1.10 / 24 ",
    ],
)
def test_parse_accepts_prefix_and_mask_forms(text):
    interface = parse(text)
    assert str(interface) == "192.168.1.10/24"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Enter an address"),
        ("192.168.1.10", "Add a prefix"),
        ("192.168.1.300/24", "not a valid IPv4 or IPv6 address"),
        ("192.168.1.10/33", "not a valid prefix"),
        ("192.168.1.10 255.0.255.0", "contiguous"),
        ("1.2.3.4/24 extra", "one address and one prefix"),
    ],
)
def test_parse_errors_explain_the_fix(text, message):
    with pytest.raises(SubnetInputError, match=message):
        parse(text)


def test_ipv4_details():
    rows = as_dict(parse("192.168.1.10/26"))
    assert rows["Network"] == "192.168.1.0/26"
    assert rows["Netmask"] == "255.255.255.192"
    assert rows["Wildcard mask"] == "0.0.0.63"
    assert rows["Broadcast"] == "192.168.1.63"
    assert rows["First usable"] == "192.168.1.1"
    assert rows["Last usable"] == "192.168.1.62"
    assert rows["Usable hosts"] == "62"
    assert rows["Total addresses"] == "64"
    assert rows["Address type"] == "Private (RFC 1918)"
    assert rows["Class"] == "C"
    assert rows["Reverse DNS zone"] == "1.168.192.in-addr.arpa"


def test_ipv4_point_to_point_and_host_routes():
    p2p = as_dict(parse("10.0.0.0/31"))
    assert p2p["First usable"] == "10.0.0.0"
    assert p2p["Last usable"] == "10.0.0.1"
    assert p2p["Usable hosts"].startswith("2 (point-to-point")
    host = as_dict(parse("10.0.0.5/32"))
    assert host["Usable hosts"] == "1"


def test_ipv6_details():
    rows = as_dict(parse("2001:db8:abcd:12::1/64"))
    assert rows["Expanded"] == "2001:0db8:abcd:0012:0000:0000:0000:0001"
    assert rows["Network"] == "2001:db8:abcd:12::/64"
    assert rows["First address"] == "2001:db8:abcd:12::"
    assert rows["Last address"] == "2001:db8:abcd:12:ffff:ffff:ffff:ffff"
    assert rows["Addresses"] == "18,446,744,073,709,551,616 (2^64)"
    assert rows["Address type"] == "Documentation"
    assert rows["Reverse DNS zone"] == "2.1.0.0.d.c.b.a.8.b.d.0.1.0.0.2.ip6.arpa"


@pytest.mark.parametrize(
    ("address", "label"),
    [
        ("100.64.1.1", "Shared address space, carrier-grade NAT (RFC 6598)"),
        ("169.254.10.1", "Link-local (APIPA)"),
        ("8.8.8.8", "Public"),
        ("255.255.255.255", "Limited broadcast"),
        ("fd12::1", "Unique local (ULA)"),
        ("fe80::1", "Link-local"),
        ("2606:4700::1111", "Global unicast"),
    ],
)
def test_address_types(address, label):
    import ipaddress

    assert address_type(ipaddress.ip_address(address)) == label


def test_binary_groups():
    import ipaddress

    assert binary_groups(ipaddress.ip_address("192.168.1.10")) == [
        "11000000",
        "10101000",
        "00000001",
        "00001010",
    ]
    groups = binary_groups(ipaddress.ip_address("2001:db8::1"))
    assert len(groups) == 8
    assert groups[0] == "0010000000000001"


def test_reverse_zone_rounds_to_whole_octets():
    assert reverse_zone(parse("10.20.30.40/20").network) == "20.10.in-addr.arpa"
    assert reverse_zone(parse("0.0.0.0/0").network) == "in-addr.arpa"


def test_split_ipv4():
    network = parse("192.168.1.0/24").network
    rows, total = split(network, 26)
    assert total == 4
    assert [r.network for r in rows] == [
        "192.168.1.0/26",
        "192.168.1.64/26",
        "192.168.1.128/26",
        "192.168.1.192/26",
    ]
    assert (rows[1].first, rows[1].last, rows[1].broadcast, rows[1].hosts) == (
        "192.168.1.65",
        "192.168.1.126",
        "192.168.1.127",
        "62",
    )


def test_split_large_is_capped():
    rows, total = split(parse("10.0.0.0/8").network, 24)
    assert total == 65536
    assert len(rows) == MAX_SPLIT_ROWS


def test_split_ipv6_has_no_broadcast():
    rows, total = split(parse("2001:db8::/48").network, 50)
    assert total == 4
    assert rows[0].network == "2001:db8::/50"
    assert rows[0].broadcast == "—"


def test_split_options_and_invalid_prefix():
    network = parse("192.168.1.0/28").network
    assert split_options(network) == [29, 30, 31, 32]
    assert split_options(parse("1.2.3.4/32").network) == []
    with pytest.raises(SubnetInputError):
        split(network, 28)
