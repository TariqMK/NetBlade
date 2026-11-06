"""Checks the nmap command builder and the XML results parser. nmap itself is not needed."""

import pytest

from netblade.core.nmap import NmapRequest, build_args, display_command, parse_xml
from netblade.core.portscan import ScanInputError

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nmaprun scanner="nmap" args="nmap -sV 192.168.1.0/30" version="7.95">
  <host>
    <status state="up" reason="arp-response"/>
    <address addr="192.168.1.1" addrtype="ipv4"/>
    <address addr="AA:BB:CC:00:11:22" addrtype="mac" vendor="Ubiquiti"/>
    <hostnames><hostname name="router.home" type="PTR"/></hostnames>
    <ports>
      <port protocol="tcp" portid="22">
        <state state="open" reason="syn-ack"/>
        <service name="ssh" product="OpenSSH" version="9.6" extrainfo="protocol 2.0"/>
      </port>
      <port protocol="tcp" portid="443">
        <state state="open" reason="syn-ack"/>
        <service name="https"/>
        <script id="http-title" output="UniFi OS"/>
      </port>
    </ports>
    <os><osmatch name="Linux 5.x" accuracy="96"/></os>
  </host>
  <host>
    <status state="up" reason="echo-reply"/>
    <address addr="192.168.1.2" addrtype="ipv4"/>
    <hostnames/>
  </host>
  <host>
    <status state="down" reason="no-response"/>
    <address addr="192.168.1.3" addrtype="ipv4"/>
  </host>
  <runstats>
    <finished time="1762390000" summary="Nmap done at Thu Nov  6 00:46:40 2026; 4 IP addresses (2 hosts up) scanned in 6.21 seconds"/>
  </runstats>
</nmaprun>
"""


def test_default_request():
    assert build_args(NmapRequest(targets="192.168.1.0/24")) == [
        "--unprivileged",
        "-sT",
        "-T4",
        "192.168.1.0/24",
    ]


def test_options_map_to_flags():
    request = NmapRequest(
        targets="10.0.0.1 10.0.0.2",
        ports="Custom ports",
        custom_ports="22, 80,8000-8010",
        timing="Normal (-T3)",
        version_detection=True,
        default_scripts=True,
        skip_discovery=True,
        extra_args="--open --script 'http-title'",
    )
    assert build_args(request) == [
        "--unprivileged", "-sT", "-p", "22,80,8000-8010", "-sV", "-sC", "-Pn", "--open", "--script", "http-title",
        "10.0.0.1", "10.0.0.2",
    ]  # fmt: skip


def test_ping_sweep_ignores_port_and_script_options():
    request = NmapRequest(
        targets="10.0.0.0/24",
        scan_type="Ping sweep only (-sn)",
        ports="All ports (-p-)",
        version_detection=True,
    )
    assert build_args(request) == ["--unprivileged", "-sn", "-T4", "10.0.0.0/24"]


def test_form_offers_nothing_that_needs_admin_rights():
    """NetBlade assumes no admin rights: no raw-packet scan types, OS detection or traceroute."""
    from netblade.core.nmap import SCAN_TYPES, SUGGESTED_OPTIONS

    offered = {flag for flags in SCAN_TYPES.values() for flag in flags}
    offered |= {word for text, _ in SUGGESTED_OPTIONS for word in text.split()}
    assert not offered & {
        "-sS",
        "-sU",
        "-sA",
        "-sW",
        "-sF",
        "-sX",
        "-sN",
        "-O",
        "-A",
        "--traceroute",
    }


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        (NmapRequest(targets=" "), "Enter a host"),
        (
            NmapRequest(targets="10.0.0.1", ports="Custom ports", custom_ports="abc"),
            "not a port",
        ),
        (NmapRequest(targets="10.0.0.1", extra_args="--script 'x"), "quotes"),
    ],
)
def test_invalid_input(request_, message):
    with pytest.raises(ScanInputError, match=message):
        build_args(request_)


def test_display_command_starts_with_nmap():
    assert display_command(["-sV", "10.0.0.1"]) == "nmap -sV 10.0.0.1"


def test_parse_xml_rows_and_summary():
    rows, summary = parse_xml(SAMPLE_XML)
    assert summary.endswith("4 IP addresses (2 hosts up) scanned in 6.21 seconds")
    assert [(r.host, r.name, r.port, r.state, r.service) for r in rows] == [
        ("192.168.1.1", "router.home", "22/tcp", "open", "ssh"),
        ("192.168.1.1", "router.home", "443/tcp", "open", "https"),
        ("192.168.1.2", "", "—", "Up", ""),
    ]
    assert (
        rows[0].detail
        == "OpenSSH 9.6 protocol 2.0 · MAC AA:BB:CC:00:11:22 (Ubiquiti) · OS Linux 5.x (96%)"
    )
    assert rows[1].detail == "http-title: UniFi OS"


def test_parse_xml_handles_empty_and_cut_off_output():
    assert parse_xml("") == ([], "")
    rows, summary = parse_xml("<nmaprun><host>")
    assert rows == []
    assert "stopped" in summary
