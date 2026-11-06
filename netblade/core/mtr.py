"""Continuous traceroute (MTR-style): probes every hop each cycle and keeps loss and latency per hop."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from netblade.core.probe import ProbeKind, ProbeResult

HISTORY_LENGTH = 60
MAX_HOPS = 30

ProbeFn = Callable[[str, int], ProbeResult]
MapFn = Callable[[Callable[[int], ProbeResult], Iterable[int]], Iterable[ProbeResult]]


@dataclass
class HopStats:
    """Results for one hop (one TTL value) on the path."""

    ttl: int
    address: str | None = None
    hostname: str | None = None
    sent: int = 0
    received: int = 0
    last_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    total_ms: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LENGTH))

    def record(self, result: ProbeResult) -> None:
        self.sent += 1
        if result.address:
            self.address = result.address
        if result.kind is ProbeKind.TIMEOUT or result.rtt_ms is None:
            self.history.append(None)
            return
        rtt = result.rtt_ms
        self.received += 1
        self.last_ms = rtt
        self.best_ms = rtt if self.best_ms is None else min(self.best_ms, rtt)
        self.worst_ms = rtt if self.worst_ms is None else max(self.worst_ms, rtt)
        self.total_ms += rtt
        self.history.append(rtt)

    @property
    def loss_percent(self) -> float:
        if self.sent == 0:
            return 0.0
        return 100.0 * (self.sent - self.received) / self.sent

    @property
    def avg_ms(self) -> float | None:
        return self.total_ms / self.received if self.received else None

    @property
    def responded(self) -> bool:
        return self.received > 0


class MtrSession:
    """Tracks the path to one IPv4 address across repeated probe cycles."""

    def __init__(self, target_ip: str, max_hops: int = MAX_HOPS) -> None:
        self.target_ip = target_ip
        self.max_hops = max_hops
        self.hops: dict[int, HopStats] = {}
        self.destination_ttl: int | None = None

    def run_cycle(self, probe: ProbeFn, map_fn: MapFn = map) -> None:
        """Probe every hop once. Pass a thread pool's map as map_fn to probe hops in parallel."""
        limit = self.destination_ttl or self.max_hops
        ttls = list(range(1, limit + 1))
        results = list(map_fn(lambda ttl: probe(self.target_ip, ttl), ttls))

        for ttl, result in zip(ttls, results):
            self.hops.setdefault(ttl, HopStats(ttl)).record(result)
            reached = (
                result.kind is ProbeKind.REPLY and result.address == self.target_ip
            )
            if reached and (self.destination_ttl is None or ttl < self.destination_ttl):
                self.destination_ttl = ttl

        if self.destination_ttl is not None:
            for ttl in [t for t in self.hops if t > self.destination_ttl]:
                del self.hops[ttl]

    def visible_hops(self) -> list[HopStats]:
        """Hops to show: up to the destination, or one past the last hop that answered."""
        if self.destination_ttl is not None:
            limit = self.destination_ttl
        else:
            answered = [ttl for ttl, hop in self.hops.items() if hop.responded]
            limit = min(max(answered, default=0) + 1, self.max_hops)
        return [self.hops[ttl] for ttl in range(1, limit + 1) if ttl in self.hops]

    @property
    def destination(self) -> HopStats | None:
        if self.destination_ttl is None:
            return None
        return self.hops.get(self.destination_ttl)
