"""Checks the window builds, shows the version from netblade/_version.py and switches between tools."""

from netblade import __version__
from netblade.app import TOOLS, MainWindow, display_version
from netblade.core.mtr import HopStats
from netblade.core.probe import ProbeKind, ProbeResult


def test_display_version_has_v_prefix():
    assert display_version() == f"v{__version__}"


def test_header_shows_version(qapp):
    window = MainWindow()
    assert window.version_label.text() == f"v{__version__}"


def test_sidebar_switches_pages(qapp):
    window = MainWindow()
    assert window.nav.count() == len(TOOLS)
    for row in range(len(TOOLS)):
        window.nav.setCurrentRow(row)
        assert window.pages.currentIndex() == row


def test_hop_table_renders_snapshot(qapp):
    window = MainWindow()
    page = window.ping_page
    hop = HopStats(ttl=1)
    hop.record(ProbeResult(ProbeKind.REPLY, "1.1.1.1", 12.0))
    silent = HopStats(ttl=2)
    silent.record(ProbeResult(ProbeKind.TIMEOUT))
    page.model.set_hops([hop, silent])
    page._update_chart()
    window.resize(1000, 700)
    window.grab()  # Paints the delegates and the chart.
    assert page.model.rowCount() == 2
    assert page.model.index(1, page.model.LOSS).data() == "—"
