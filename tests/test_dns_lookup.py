"""Checks DNS input handling: nameserver parsing, reverse names and empty input. No network traffic."""

import pytest

from netblade.core.dns_lookup import (
    SYSTEM_DEFAULT,
    LookupFailed,
    lookup,
    parse_nameserver,
    preset_label,
    query_name,
    self_check,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (SYSTEM_DEFAULT, None),
        ("", None),
        (preset_label("1.1.1.1", "Cloudflare"), "1.1.1.1"),
        ("  9.9.9.9 ", "9.9.9.9"),
        ("2606:4700:4700::1111", "2606:4700:4700::1111"),
    ],
)
def test_parse_nameserver(text, expected):
    assert parse_nameserver(text) == expected


@pytest.mark.parametrize("text", ["dns.google", "1.1.1", "999.1.1.1"])
def test_parse_nameserver_rejects_non_ip(text):
    with pytest.raises(LookupFailed, match="IP address"):
        parse_nameserver(text)


def test_ptr_of_ip_uses_reverse_name():
    assert query_name("8.8.8.8", "PTR") == "8.8.8.8.in-addr.arpa."
    assert query_name(" example.com ", "A") == "example.com"
    assert query_name("example.com", "PTR") == "example.com"


def test_empty_name_is_rejected_before_any_query():
    with pytest.raises(LookupFailed, match="Enter a name"):
        lookup("   ", "A", "1.1.1.1")


def test_self_check_loads_every_record_type():
    self_check()
