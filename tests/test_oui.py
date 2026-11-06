"""Checks MAC parsing and formats, IEEE list reading, most-specific vendor lookup and address flags. No network."""

import pytest

from netblade.core.oui import (
    OuiError,
    describe,
    load,
    parse_csv,
    parse_mac,
    split_input,
)

OUI_CSV = """Registry,Assignment,Organization Name,Organization Address
MA-L,00155D,Microsoft Corporation,One Microsoft Way Redmond WA US 98052
MA-L,70B3D5,IEEE Registration Authority,445 Hoes Lane Piscataway NJ US 08554
MA-L,5847CA,IEEE Registration Authority,445 Hoes Lane Piscataway NJ US 08554
"""
MAM_CSV = """Registry,Assignment,Organization Name,Organization Address
MA-M,5847CA7,"Shenzhen Meigao   Electronic Equipment Co.,Ltd",Shenzhen CN
"""
OUI36_CSV = """Registry,Assignment,Organization Name,Organization Address
MA-S,70B3D51C0,"W. H. Leary Co., Inc.",Overland Park KS US
"""


@pytest.fixture
def database(tmp_path):
    (tmp_path / "oui.csv").write_text(OUI_CSV, encoding="utf-8")
    (tmp_path / "mam.csv").write_text(MAM_CSV, encoding="utf-8")
    (tmp_path / "oui36.csv").write_text(OUI36_CSV, encoding="utf-8")
    return load(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "00:15:5d:e4:24:53",
        "00-15-5D-E4-24-53",
        "0015.5de4.2453",
        "00155de42453",
        " 00 15 5d e4 24 53 ",
    ],
)
def test_parse_mac_formats(text):
    mac = parse_mac(text)
    assert mac.is_full
    assert mac.formats() == {
        "Colon": "00:15:5d:e4:24:53",
        "Hyphen": "00-15-5D-E4-24-53",
        "Bare": "00155DE42453",
        "Cisco": "0015.5de4.2453",
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("zz:11:22", "not a MAC"),
        ("0015", "hex digits"),
        ("00155de4245399", "hex digits"),
    ],
)
def test_parse_mac_errors(text, message):
    with pytest.raises(OuiError, match=message):
        parse_mac(text)


def test_most_specific_block_wins(database):
    assert describe("00:15:5d:e4:24:53", database).vendor == "Microsoft Corporation"
    meigao = describe("58-47-CA-70-D1-35", database)
    assert (
        meigao.vendor == "Shenzhen Meigao Electronic Equipment Co.,Ltd"
    )  # MA-M beats the MA-L block.
    assert meigao.notes.startswith("MA-M")
    assert (
        describe("70:B3:D5:1C:00:01", database).vendor == "W. H. Leary Co., Inc."
    )  # MA-S.
    assert (
        describe("70:B3:D5:99:00:01", database).vendor == "IEEE Registration Authority"
    )


def test_prefix_only_lookup(database):
    result = describe("00155D", database)
    assert result.vendor == "Microsoft Corporation"
    assert result.mac == "00:15:5d"


def test_flags_and_unknowns(database):
    assert "Locally administered" in describe("aa:bb:cc:00:11:22", database).notes
    assert describe("aa:bb:cc:00:11:22", database).vendor == ""
    assert "IPv4 multicast" in describe("01:00:5e:00:00:fb", database).notes
    assert "IPv6 multicast" in describe("33:33:00:00:00:01", database).notes
    assert "Broadcast" in describe("ff:ff:ff:ff:ff:ff", database).notes
    assert "Not in the IEEE list" in describe("00:00:01:00:00:00", database).notes
    bad = describe("hello", database)
    assert not bad.ok


def test_lookup_without_database_still_flags():
    result = describe("aa:bb:cc:00:11:22", None)
    assert result.ok
    assert "Locally administered" in result.notes


def test_load_returns_none_until_downloaded(tmp_path):
    assert load(tmp_path) is None


def test_parse_csv_cleans_names():
    table = parse_csv(MAM_CSV, 28)
    vendor = next(iter(table.values()))
    assert vendor.name == "Shenzhen Meigao Electronic Equipment Co.,Ltd"
    assert vendor.prefix == "58:47:CA7"


def test_split_input():
    assert split_input(
        "00:15:5d:e4:24:53\n00-15-5D-E4-24-53, aabb.ccdd.eeff;00:15:5d:e4:24:53"
    ) == [
        "00:15:5d:e4:24:53",
        "00-15-5D-E4-24-53",
        "aabb.ccdd.eeff",
    ]
