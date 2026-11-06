"""Checks the continuous-traceroute logic with a fake network, so no real probes are sent."""

from netblade.core.mtr import MtrSession
from netblade.core.probe import ProbeKind, ProbeResult

TARGET = "1.1.1.1"


def fake_path(
    routers: list[str | None], target_ttl: int, lossy: dict[int, int] | None = None
):
    """Build a probe function for a path. routers[i] answers TTL i+1; None never answers."""
    lossy = lossy or {}
    calls: dict[int, int] = {}

    def probe(ip: str, ttl: int) -> ProbeResult:
        calls[ttl] = calls.get(ttl, 0) + 1
        if ttl in lossy and calls[ttl] % lossy[ttl] == 0:
            return ProbeResult(ProbeKind.TIMEOUT)
        if ttl >= target_ttl:
            return ProbeResult(ProbeKind.REPLY, ip, 10.0 + ttl)
        router = routers[ttl - 1]
        if router is None:
            return ProbeResult(ProbeKind.TIMEOUT)
        return ProbeResult(ProbeKind.TTL_EXPIRED, router, float(ttl))

    return probe


def test_first_cycle_finds_destination_and_drops_hops_beyond_it():
    session = MtrSession(TARGET)
    session.run_cycle(fake_path(["192.168.1.1", "10.0.0.1"], target_ttl=3))
    assert session.destination_ttl == 3
    assert [hop.ttl for hop in session.visible_hops()] == [1, 2, 3]
    assert max(session.hops) == 3
    assert session.destination.address == TARGET


def test_later_cycles_only_probe_up_to_destination():
    probed: list[int] = []
    base = fake_path(["192.168.1.1"], target_ttl=2)

    def counting(ip, ttl):
        probed.append(ttl)
        return base(ip, ttl)

    session = MtrSession(TARGET)
    session.run_cycle(counting)
    probed.clear()
    session.run_cycle(counting)
    assert probed == [1, 2]


def test_loss_and_latency_statistics():
    session = MtrSession(TARGET)
    probe = fake_path(["192.168.1.1", "10.0.0.1"], target_ttl=3, lossy={2: 4})
    for _ in range(8):
        session.run_cycle(probe)
    hop2 = session.hops[2]
    assert hop2.sent == 8
    assert hop2.received == 6
    assert hop2.loss_percent == 25.0
    assert hop2.best_ms == hop2.worst_ms == hop2.avg_ms == 2.0
    assert list(hop2.history).count(None) == 2
    assert session.hops[1].loss_percent == 0.0


def test_silent_hop_is_shown_as_no_reply():
    session = MtrSession(TARGET)
    session.run_cycle(fake_path(["192.168.1.1", None, "62.24.91.7"], target_ttl=4))
    hop2 = session.hops[2]
    assert not hop2.responded
    assert hop2.address is None
    assert hop2.loss_percent == 100.0
    assert [hop.ttl for hop in session.visible_hops()] == [1, 2, 3, 4]


def test_unreached_destination_shows_one_hop_past_last_answer():
    session = MtrSession(TARGET, max_hops=10)
    session.run_cycle(
        fake_path(["192.168.1.1", "10.0.0.1"] + [None] * 8, target_ttl=99)
    )
    assert session.destination_ttl is None
    assert [hop.ttl for hop in session.visible_hops()] == [1, 2, 3]
