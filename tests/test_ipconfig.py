"""Checks that English `ipconfig /all` output is parsed into host details and adapters."""

from netblade.core.ipconfig import parse_ipconfig_all

SAMPLE = """\r
Windows IP Configuration\r
\r
   Host Name . . . . . . . . . . . . : DESKTOP-NB01\r
   Primary Dns Suffix  . . . . . . . : \r
   Node Type . . . . . . . . . . . . : Hybrid\r
   IP Routing Enabled. . . . . . . . : No\r
\r
Ethernet adapter Ethernet:\r
\r
   Media State . . . . . . . . . . . : Media disconnected\r
   Connection-specific DNS Suffix  . : \r
   Description . . . . . . . . . . . : Intel(R) Ethernet Connection I219-V\r
   Physical Address. . . . . . . . . : 00-1A-2B-3C-4D-5E\r
   DHCP Enabled. . . . . . . . . . . : Yes\r
\r
Wireless LAN adapter Wi-Fi:\r
\r
   Connection-specific DNS Suffix  . : home\r
   Description . . . . . . . . . . . : Intel(R) Wi-Fi 6E AX211 160MHz\r
   Physical Address. . . . . . . . . : 70-A8-D3-11-22-33\r
   DHCP Enabled. . . . . . . . . . . : Yes\r
   Link-local IPv6 Address . . . . . : fe80::1c2d:3e4f:5a6b:7c8d%12(Preferred) \r
   IPv4 Address. . . . . . . . . . . : 192.168.1.42(Preferred) \r
   Subnet Mask . . . . . . . . . . . : 255.255.255.0\r
   Lease Obtained. . . . . . . . . . : 05 November 2026 08:12:44\r
   Default Gateway . . . . . . . . . : fe80::1%12\r
                                       192.168.1.1\r
   DHCP Server . . . . . . . . . . . : 192.168.1.1\r
   DNS Servers . . . . . . . . . . . : 192.168.1.1\r
                                       1.1.1.1\r
   NetBIOS over Tcpip. . . . . . . . : Enabled\r
"""


def test_host_section():
    config = parse_ipconfig_all(SAMPLE)
    assert config.host["Host Name"] == ["DESKTOP-NB01"]
    assert config.host["Primary Dns Suffix"] == []


def test_adapters_and_kinds():
    config = parse_ipconfig_all(SAMPLE)
    assert [(a.kind, a.name) for a in config.adapters] == [
        ("Ethernet adapter", "Ethernet"),
        ("Wireless LAN adapter", "Wi-Fi"),
    ]
    ethernet, wifi = config.adapters
    assert not ethernet.connected
    assert wifi.connected


def test_values_are_cleaned_and_continuations_collected():
    wifi = parse_ipconfig_all(SAMPLE).adapters[1]
    assert wifi.get("IPv4 Address") == ["192.168.1.42"]
    assert wifi.get("Link-local IPv6 Address") == ["fe80::1c2d:3e4f:5a6b:7c8d%12"]
    assert wifi.get("Default Gateway") == ["fe80::1%12", "192.168.1.1"]
    assert wifi.get("DNS Servers") == ["192.168.1.1", "1.1.1.1"]
    assert wifi.first("Lease Obtained") == "05 November 2026 08:12:44"
    assert wifi.first("Physical Address") == "70-A8-D3-11-22-33"


def test_unparseable_output_gives_no_adapters():
    config = parse_ipconfig_all(
        "Configuration IP de Windows\n\n   Nom de l'hôte . . . : PC\n"
    )
    assert config.adapters == []
