"""Make the README screenshots: opens NetBlade from source, fills each tool with sample data and saves PNGs.

Sends no network traffic and reads no real configuration. All addresses are documentation ranges
(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) or private ranges.
Settings, notes and the vendor list go to Qt's test locations, never the user's real ones.

Run from the project root:  python assets/make_screenshots.py
Output: assets/screenshots/<tool>-light.png and <tool>-dark.png
"""

import datetime as dt
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import netblade.app
from netblade.app import MainWindow, release_qt_objects
from netblade.core.curl import CurlResult
from netblade.core.dns_lookup import DnsRecord, LookupResult
from netblade.core.mtr import HopStats
from netblade.core.multiping import PingTarget
from netblade.core.oui import OuiDatabase, Vendor
from netblade.core.portscan import ScanRow
from netblade.core.probe import ProbeKind, ProbeResult
from netblade.core.rdap import Contact, Query, RdapResult
from netblade.core.text_diff import compare
from netblade.core.tlscheck import CertInfo, TlsResult
from netblade.ui import theme
from netblade.ui.curl_page import BODY_VIEW
from netblade.ui.multiping_page import HostTile

OUT = ROOT / "assets" / "screenshots"
WIDTH, HEIGHT = 1120, 720
rng = random.Random(7)


# Sample data, one function per tool


def fill_ping(window: MainWindow) -> None:
    page = window.ping_page
    path = [
        ("192.168.1.1", "router.home.lan", 1.2, 0.3, 0),
        ("10.64.0.1", None, 7.8, 1.5, 0),
        ("198.51.100.1", "core1.ldn.isp.example.net", 9.4, 1.2, 0),
        (None, None, None, 0, 100),
        ("198.51.100.42", "edge2.ldn.isp.example.net", 11.0, 2.5, 6),
        ("203.0.113.9", "ae-4.fra.transit.example.net", 18.6, 2.0, 0),
        ("203.0.113.66", "lb1.example.com", 19.3, 1.6, 0),
        ("203.0.113.80", "www.example.com", 19.8, 1.8, 0),
    ]
    hops = []
    for ttl, (address, name, base, jitter, loss) in enumerate(path, start=1):
        hop = HopStats(ttl, hostname=name)
        for _ in range(60):
            if address is None or rng.random() * 100 < loss:
                hop.record(ProbeResult(ProbeKind.TIMEOUT, address, None))
            else:
                spike = rng.random() < 0.05
                rtt = base + abs(rng.gauss(0, jitter)) + (12 if spike else 0)
                hop.record(ProbeResult(ProbeKind.REPLY, address, rtt))
        hops.append(hop)
    page.target.setText("www.example.com")
    page.title.setText("www.example.com")
    page._ip = "203.0.113.80"
    page.body.setCurrentIndex(1)
    page.start_button.setText("Stop")
    page._on_updated(None, hops, len(hops))


def fill_dns(window: MainWindow) -> None:
    page = window.dns_page
    page.name.setText("example.com")
    page.record_type.setCurrentText("MX")
    page.nameserver.setCurrentIndex(1)
    records = [
        DnsRecord("example.com.", "MX", 3600, "10 mx1.mail.example.com."),
        DnsRecord("example.com.", "MX", 3600, "10 mx2.mail.example.com."),
        DnsRecord("example.com.", "MX", 3600, "20 mx-backup.example.net."),
        DnsRecord("example.com.", "MX", 3600, "30 relay.example.org."),
    ]
    result = LookupResult(records, page.nameserver.currentText().split()[0], 14.2)
    page.model.set_records(result.records)
    page.summary.setText(f"4 records from {result.nameserver} in 14 ms")
    page.body.setCurrentIndex(1)


IPCONFIG = """
Windows IP Configuration

   Host Name . . . . . . . . . . . . : WORKSTATION-07
   Primary Dns Suffix  . . . . . . . :
   Node Type . . . . . . . . . . . . : Hybrid
   IP Routing Enabled. . . . . . . . : No
   WINS Proxy Enabled. . . . . . . . : No
   DNS Suffix Search List. . . . . . : home.lan

Ethernet adapter Ethernet:

   Connection-specific DNS Suffix  . : home.lan
   Description . . . . . . . . . . . : Intel(R) Ethernet Controller I225-V
   Physical Address. . . . . . . . . : 3C-7C-3F-1A-2B-4C
   DHCP Enabled. . . . . . . . . . . : Yes
   Autoconfiguration Enabled . . . . : Yes
   IPv6 Address. . . . . . . . . . . : 2001:db8:4f2a:1::24(Preferred)
   Link-local IPv6 Address . . . . . : fe80::1c4a:9e2f:7d10:3b5c%12(Preferred)
   IPv4 Address. . . . . . . . . . . : 192.168.1.24(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Lease Obtained. . . . . . . . . . : 06 November 2026 08:14:02
   Lease Expires . . . . . . . . . . : 07 November 2026 08:14:02
   Default Gateway . . . . . . . . . : 192.168.1.1
   DHCP Server . . . . . . . . . . . : 192.168.1.1
   DNS Servers . . . . . . . . . . . : 192.168.1.1
                                       1.1.1.1
   NetBIOS over Tcpip. . . . . . . . : Enabled

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . : office.example.com
   Description . . . . . . . . . . . : Intel(R) Wi-Fi 6E AX211 160MHz
   Physical Address. . . . . . . . . : 8A-21-5E-90-C4-17
   DHCP Enabled. . . . . . . . . . . : Yes
   Autoconfiguration Enabled . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 10.20.30.115(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.252.0
   Default Gateway . . . . . . . . . : 10.20.28.1
   DHCP Server . . . . . . . . . . . : 10.20.28.2
   DNS Servers . . . . . . . . . . . : 10.20.28.10
                                       10.20.28.11
   NetBIOS over Tcpip. . . . . . . . : Enabled

Ethernet adapter vEthernet (WSL):

   Connection-specific DNS Suffix  . :
   Description . . . . . . . . . . . : Hyper-V Virtual Ethernet Adapter
   Physical Address. . . . . . . . . : 00-15-5D-E4-24-53
   DHCP Enabled. . . . . . . . . . . : No
   Autoconfiguration Enabled . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 172.29.160.1(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.240.0
   Default Gateway . . . . . . . . . :
   NetBIOS over Tcpip. . . . . . . . : Enabled

Ethernet adapter Bluetooth Network Connection:

   Media State . . . . . . . . . . . : Media disconnected
   Description . . . . . . . . . . . : Bluetooth Device (Personal Area Network)
   Physical Address. . . . . . . . . : 3C-7C-3F-1A-2B-50
"""


def fill_ipconfig(window: MainWindow) -> None:
    page = window.ipconfig_page
    page._loaded = True  # Never run the real ipconfig.
    page._on_output(IPCONFIG.strip("\n"))


CURL_VERBOSE = """\
*   Trying 203.0.113.80:443...
* Connected to api.example.com (203.0.113.80) port 443
* ALPN: curl offers h2,http/1.1
* SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384
* ALPN: server accepted h2
> GET /v1/status HTTP/2
> Host: api.example.com
> User-Agent: curl/8.10.1
> Accept: application/json
>
< HTTP/2 200
< content-type: application/json
< content-length: 182
< cache-control: no-store
< server: example-edge
< date: Fri, 06 Nov 2026 14:02:11 GMT
<
* Connection #0 to host api.example.com left intact
"""


def fill_curl(window: MainWindow) -> None:
    page = window.curl_page
    page.url.setText("https://api.example.com/v1/status")
    body = (
        b'{"service":"api","status":"ok","version":"4.12.0","region":"eu-west",'
        b'"uptime_s":1209600,"checks":{"database":"ok","cache":"ok","queue":"ok"}}'
    )
    write_out = {
        "http_code": "200",
        "http_version": "2",
        "remote_ip": "203.0.113.80",
        "remote_port": "443",
        "size_download": str(len(body)),
        "num_redirects": "0",
        "content_type": "application/json",
        "url_effective": "https://api.example.com/v1/status",
        "time_namelookup": "0.012",
        "time_connect": "0.031",
        "time_appconnect": "0.064",
        "time_pretransfer": "0.064",
        "time_starttransfer": "0.142",
        "time_total": "0.146",
    }
    result = CurlResult(0, CURL_VERBOSE, body, False, write_out, 0.146)
    page.body_stack.setCurrentIndex(1)
    page.view_group.button(BODY_VIEW).setChecked(True)
    page._show_result(result)


def fill_multiping(window: MainWindow) -> None:
    page = window.multiping_page
    now = dt.datetime.now().timestamp()
    hosts = [
        ("192.168.1.1", "Router", "up", 1.1),
        ("192.168.1.40", "Printer 2F", "down", 3.0),
        ("10.20.28.10", "DNS 1", "up", 2.4),
        ("198.51.100.25", "VPN gateway", "unstable", 24.0),
        ("203.0.113.80", None, "up", 19.5),
        ("192.168.1.75", "Camera yard", "unstable", 6.0),
    ]
    for host, alias, kind, base in hosts:
        target = PingTarget(host, ip=host, alias=alias)
        if alias is None:
            target.name = "www.example.com"
        for i in range(120):
            if kind == "down":
                lost = i > 100
            elif kind == "unstable":
                lost = rng.random() < 0.15 or i in (116, 118)
            else:
                lost = False
            rtt = None if lost else base + abs(rng.gauss(0, base * 0.15))
            target.record(rtt, now - (120 - i))
        target.since = now - {"up": 3725, "down": 19, "unstable": 214}[kind]
        page.targets.append(target)
        page.tiles[id(target)] = HostTile(
            target, page.toggle_pause, page.remove, page.rename
        )
    page.start_button.setText("Stop all")
    for line in (
        "13:38:02  Started pinging 6 hosts",
        "13:39:47  VPN gateway (198.51.100.25)  Up → Unstable",
        "13:41:15  Camera yard (192.168.1.75)  Up → Unstable",
        "13:42:11  Printer 2F (192.168.1.40)  Unstable → Down",
    ):
        page._log(line)
    page._relayout()
    page._update_message()


def fill_scan(window: MainWindow) -> None:
    page = window.scan_page
    page.targets.setText("192.168.1.0/24")
    rows = [
        ScanRow("192.168.1.1", "router.home.lan", "53", "open", "domain"),
        ScanRow("192.168.1.1", "router.home.lan", "80", "open", "http"),
        ScanRow("192.168.1.1", "router.home.lan", "443", "open", "https"),
        ScanRow("192.168.1.24", "workstation-07.home.lan", "135", "open", "msrpc"),
        ScanRow(
            "192.168.1.24", "workstation-07.home.lan", "445", "open", "microsoft-ds"
        ),
        ScanRow(
            "192.168.1.24", "workstation-07.home.lan", "3389", "open", "ms-wbt-server"
        ),
        ScanRow("192.168.1.40", "printer-2f.home.lan", "80", "open", "http"),
        ScanRow("192.168.1.40", "printer-2f.home.lan", "631", "open", "ipp"),
        ScanRow("192.168.1.40", "printer-2f.home.lan", "9100", "open", "jetdirect"),
        ScanRow("192.168.1.60", "nas.home.lan", "22", "open", "ssh"),
        ScanRow("192.168.1.60", "nas.home.lan", "445", "open", "microsoft-ds"),
        ScanRow("192.168.1.60", "nas.home.lan", "5000", "open", "upnp"),
        ScanRow("192.168.1.75", "", "554", "open", "rtsp"),
        ScanRow("192.168.1.112", "phone.home.lan", "", "up", "", "No open ports"),
    ]
    page.model.set_rows(rows)
    page.summary.setText("7 of 256 hosts up · 13 open ports · 6.3 s")
    page.body.setCurrentIndex(1)


def fill_subnet(window: MainWindow) -> None:
    page = window.subnet_page
    page.input.setText("10.20.30.115/22")  # The split list then defaults to /24.


def _cert(cn, issuer_cn, start, end, names, key, ca=False):
    return CertInfo(
        subject=f"CN={cn}",
        subject_cn=cn,
        issuer=f"CN={issuer_cn}, O=Example Trust Services, C=GB",
        issuer_cn=issuer_cn,
        not_before=start,
        not_after=end,
        names=names,
        serial="".join(rng.choice("0123456789ABCDEF") for _ in range(32)),
        signature="ECDSA with SHA-384" if ca else "ECDSA with SHA-256",
        key=key,
        sha256=":".join(f"{rng.randrange(256):02X}" for _ in range(32)),
        is_ca=ca,
        self_signed=False,
    )


def fill_tls(window: MainWindow) -> None:
    page = window.tls_page
    page.target.setText("www.example.com")
    now = dt.datetime.now(dt.UTC)
    chain = [
        _cert(
            "www.example.com",
            "Example TLS Issuing CA E1",
            now - dt.timedelta(days=24),
            now + dt.timedelta(days=66, hours=12),
            ["www.example.com", "example.com", "api.example.com"],
            "EC P-256",
        ),
        _cert(
            "Example TLS Issuing CA E1",
            "Example Root CA X2",
            now - dt.timedelta(days=600),
            now + dt.timedelta(days=495),
            [],
            "EC P-384",
            ca=True,
        ),
    ]
    result = TlsResult(
        host="www.example.com",
        port=443,
        address="203.0.113.80",
        tls_version="TLSv1.3",
        cipher="TLS_AES_256_GCM_SHA384",
        chain=chain,
        trusted=True,
        trust_error=None,
        name_matches=True,
        elapsed_ms=62,
        protocols={
            "TLS 1.3": True,
            "TLS 1.2": True,
            "TLS 1.1": False,
            "TLS 1.0": False,
        },
    )
    page.show_result(result)


def fill_whois(window: MainWindow) -> None:
    page = window.whois_page
    page.query.setText("198.51.100.25")
    query = Query("ip", "198.51.100.25", "198.51.100.25")
    rows = [
        ("Network name", "EXAMPLE-HOSTING-NET"),
        ("Range", "198.51.100.0 – 198.51.100.255"),
        ("CIDR", "198.51.100.0/24"),
        ("Owner", "Example Hosting Ltd"),
        ("Type", "ASSIGNED PA"),
        ("Country", "GB"),
        ("Handle", "198.51.100.0 - 198.51.100.255"),
        ("Parent", "198.51.0.0 - 198.51.255.255"),
        ("Abuse contact", "abuse@hosting.example.net"),
        ("Registered", "2014-03-18"),
        ("Last changed", "2026-02-09"),
        ("Registry", "RIPE NCC"),
    ]
    contacts = [
        Contact(["registrant"], "", "Example Hosting Ltd", "", "", "ORG-EHL1-RIPE"),
        Contact(
            ["administrative"],
            "Network Operations",
            "Example Hosting Ltd",
            "noc@hosting.example.net",
            "+44 20 7946 0000",
            "EHL-NOC",
        ),
        Contact(
            ["technical"],
            "Network Operations",
            "Example Hosting Ltd",
            "noc@hosting.example.net",
            "+44 20 7946 0000",
            "EHL-NOC",
        ),
        Contact(
            ["abuse"],
            "Abuse desk",
            "Example Hosting Ltd",
            "abuse@hosting.example.net",
            "",
            "EHL-ABUSE",
        ),
    ]
    raw = {"objectClassName": "ip network", "handle": rows[6][1], "name": rows[0][1]}
    page.show_result(
        RdapResult(query, "https://rdap.db.ripe.net/", rows, contacts, raw)
    )


def fill_mac(window: MainWindow) -> None:
    page = window.mac_page
    page._loaded = True  # Never read or download the real vendor list.

    def vendor(name, address, prefix):
        return Vendor(name, address, "MA-L", prefix)

    page.database = OuiDatabase(
        {
            24: {
                0x00155D: vendor(
                    "Microsoft Corporation",
                    "One Microsoft Way, Redmond WA 98052, US",
                    "00:15:5D",
                ),
                0x3C7C3F: vendor(
                    "ASUSTek COMPUTER INC.",
                    "No.15, Lide Rd., Beitou, Taipei 112, TW",
                    "3C:7C:3F",
                ),
                0xB827EB: vendor(
                    "Raspberry Pi Foundation",
                    "Mitchell Wood House, Caldecote, Cambridgeshire, GB",
                    "B8:27:EB",
                ),
                0x001132: vendor(
                    "Synology Incorporated",
                    "3F, No. 106, Chang An W. Rd., Taipei, TW",
                    "00:11:32",
                ),
                0xF4F5D8: vendor(
                    "Google, Inc.",
                    "1600 Amphitheatre Parkway, Mountain View CA 94043, US",
                    "F4:F5:D8",
                ),
            }
        },
        downloaded="2026-11-02",
    )
    page._show_database_status()
    # The sample list holds 5 vendors; show the size of the real IEEE list instead.
    page.db_status.setText(
        "IEEE vendor list: 38,412 vendor blocks, downloaded 2026-11-02"
    )
    page.input.setPlainText(
        "00:15:5d:e4:24:53\n3C-7C-3F-1A-2B-4C\nb827.eb12.3456\n00:11:32:0a:bc:de\n"
        "F4F5D8A01B2C\n8a:21:5e:90:c4:17\n01:00:5e:00:00:fb\nff:ff:ff:ff:ff:ff"
    )
    page.run_lookup()


NOTES = """\
Site visit: Leeds office, 6 Nov
------------------------------------------------------------
VLAN  Name        Network            Gateway
10    Staff       10.20.28.0/22      10.20.28.1
20    Voice       10.20.40.0/24      10.20.40.1
30    Printers    10.20.50.0/26      10.20.50.1
99    Management  10.20.99.0/27      10.20.99.1

To do
- Printer 2F (192.168.1.40) drops off every few hours. Check the switch port.
- VPN gateway loss starts at hop 5 (edge2.ldn). Raise with the ISP.
- Certificate for www.example.com renews in 66 days.

ISP ticket: INC-204817   NOC: +44 20 7946 0000
"""


def fill_notepad(window: MainWindow) -> None:
    page = window.notepad_page
    page.editor.setPlainText(NOTES)
    page.save()  # Writes to Qt's test location, so the status shows "Saved".


DIFF_ORIGINAL = """\
hostname edge-sw-01
!
interface GigabitEthernet0/1
 description Uplink to core
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30
!
interface GigabitEthernet0/2
 description Printers
 switchport access vlan 30
 spanning-tree portfast
!
ip route 0.0.0.0 0.0.0.0 10.20.99.1
ntp server 192.0.2.10
logging host 192.0.2.40
!
line vty 0 4
 transport input ssh
 exec-timeout 10 0
!
end
"""

DIFF_CHANGED = """\
hostname edge-sw-01
!
interface GigabitEthernet0/1
 description Uplink to core
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30,99
!
interface GigabitEthernet0/2
 description Printers
 switchport access vlan 30
 spanning-tree portfast
 storm-control broadcast level 5.00
!
ip route 0.0.0.0 0.0.0.0 10.20.99.2
ntp server 192.0.2.25
!
line vty 0 4
 transport input ssh
 exec-timeout 5 0
!
end
"""


def fill_diff(window: MainWindow) -> None:
    page = window.diff_page
    page.left_input.setPlainText(DIFF_ORIGINAL)
    page.right_input.setPlainText(DIFF_CHANGED)
    page._timer.stop()  # Show the result now, not after the typing delay.
    page.show_diff(compare(DIFF_ORIGINAL, DIFF_CHANGED))


TOOLS = [
    ("ping-and-trace", "Ping and trace", fill_ping),
    ("dns-lookup", "DNS lookup", fill_dns),
    ("ip-config", "IP config", fill_ipconfig),
    ("curl", "curl", fill_curl),
    ("multi-ping", "Multi-ping", fill_multiping),
    ("nmap", "nmap", fill_scan),
    ("subnet-calculator", "Subnet calculator", fill_subnet),
    ("tls-certificate", "TLS certificate", fill_tls),
    ("whois", "Whois", fill_whois),
    ("mac-lookup", "MAC lookup", fill_mac),
    ("notepad", "Notepad", fill_notepad),
    ("text-diff", "Text diff", fill_diff),
]


def settle(app: QApplication) -> None:
    for _ in range(5):
        app.processEvents()


def capture(app: QApplication, scheme: Qt.ColorScheme, suffix: str) -> None:
    app.styleHints().setColorScheme(scheme)
    theme.apply(app)
    settings = QSettings(
        str(Path(tempfile.mkdtemp()) / "netblade.ini"), QSettings.Format.IniFormat
    )
    window = MainWindow(settings)
    window.resize(WIDTH, HEIGHT)
    window.show()
    settle(app)
    names = window.tool_names()
    for slug, name, fill in TOOLS:
        window.nav.setCurrentRow(names.index(name))
        settle(app)
        fill(window)
        window.apply_theme()
        settle(app)
        path = OUT / f"{slug}-{suffix}.png"
        window.grab().save(str(path))
        print("saved", path.relative_to(ROOT))
    window.close()
    window.deleteLater()
    settle(app)


def latest_tag() -> str:
    """The newest release tag, so the sidebar shows a real version, not the source version."""
    tags = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return tags[0].removeprefix("v") if tags else netblade.app.__version__


def main() -> int:
    netblade.app.__version__ = latest_tag()
    QStandardPaths.setTestModeEnabled(True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    app = QApplication(sys.argv)
    app.setOrganizationName("NetBlade-screenshots")
    app.setApplicationName("NetBlade-screenshots")
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        capture(app, Qt.ColorScheme.Light, "light")
        capture(app, Qt.ColorScheme.Dark, "dark")
    finally:
        release_qt_objects()
    return 0


if __name__ == "__main__":
    sys.exit(main())
