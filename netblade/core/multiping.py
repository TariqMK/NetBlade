"""Continuous ping of up to 10 hosts with up, unstable and down states (vmPing-style monitor)."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

MAX_HOSTS = 10
STRIKES = 3  # Lost in a row for down; replies in a row (with a clean window) for up.
WINDOW = 10  # Any loss in the last WINDOW pings keeps a host unstable.
HISTORY_LENGTH = 30


class HostState(Enum):
    IDLE = "Not started"
    UP = "Up"
    UNSTABLE = "Unstable"
    DOWN = "Down"


@dataclass
class PingTarget:
    """One monitored host and its results."""

    host: str
    ip: str | None = None
    name: str | None = (
        None  # Reverse DNS name when the host was entered as an IP address.
    )
    error: str | None = None
    state: HostState = HostState.IDLE
    since: float = 0.0  # Wall-clock time of the last state change.
    paused: bool = False
    sent: int = 0
    received: int = 0
    last_ms: float | None = None
    total_ms: float = 0.0
    last_reply_at: float | None = None
    consecutive_ok: int = 0
    consecutive_lost: int = 0
    window: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LENGTH))

    @property
    def loss_percent(self) -> float:
        return (
            0.0 if self.sent == 0 else 100.0 * (self.sent - self.received) / self.sent
        )

    @property
    def avg_ms(self) -> float | None:
        return self.total_ms / self.received if self.received else None

    @property
    def lost_in_window(self) -> int:
        return sum(1 for ok in self.window if not ok)

    def record(
        self, rtt_ms: float | None, now: float | None = None
    ) -> HostState | None:
        """Add one ping result. Returns the previous state if the state changed, otherwise None."""
        now = time.time() if now is None else now
        self.sent += 1
        replied = rtt_ms is not None
        self.window.append(replied)
        self.history.append(rtt_ms)
        if replied:
            self.received += 1
            self.last_ms = rtt_ms
            self.total_ms += rtt_ms
            self.last_reply_at = now
            self.consecutive_ok += 1
            self.consecutive_lost = 0
        else:
            self.consecutive_lost += 1
            self.consecutive_ok = 0

        new_state = self._evaluate()
        if new_state is self.state:
            return None
        previous = self.state
        self.state = new_state
        self.since = now
        return previous

    def _evaluate(self) -> HostState:
        if self.consecutive_lost >= STRIKES:
            return HostState.DOWN
        needed = min(STRIKES, self.sent)  # A new host is up on its first reply.
        if self.consecutive_ok >= needed and self.lost_in_window == 0:
            return HostState.UP
        return HostState.UNSTABLE

    def reset(self) -> None:
        """Clear results, for example when monitoring starts again."""
        fresh = PingTarget(self.host, ip=self.ip, name=self.name)
        self.__dict__.update(fresh.__dict__)


def parse_hosts(text: str) -> list[str]:
    """Split typed or pasted text into hosts. Accepts newlines, commas, semicolons and spaces."""
    hosts: list[str] = []
    for part in re.split(r"[\s,;]+", text.strip()):
        if part and part not in hosts:
            hosts.append(part)
    return hosts


def format_duration(seconds: float) -> str:
    """Short duration, for example "45 s", "4 min 12 s", "2 h 14 min" or "3 d 5 h"."""
    seconds = max(int(seconds), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def status_line(target: PingTarget, now: float | None = None) -> str:
    """The state line on a tile, for example "Up for 2 h 14 min" or "Unstable · 2 of last 10 lost"."""
    now = time.time() if now is None else now
    if target.paused:
        return "Paused"
    if target.error and target.state is not HostState.UP:
        return target.error
    if target.state is HostState.IDLE:
        return "Not started" if target.sent == 0 else "Waiting for reply"
    if target.state is HostState.UNSTABLE:
        return f"Unstable · {target.lost_in_window} of last {len(target.window)} lost"
    return f"{target.state.value} for {format_duration(now - target.since)}"


def change_line(target: PingTarget, previous: HostState, at: float) -> str:
    """One line for the status change log."""
    stamp = time.strftime("%H:%M:%S", time.localtime(at))
    address = f" ({target.ip})" if target.ip and target.ip != target.host else ""
    return f"{stamp}  {target.host}{address}  {previous.value} → {target.state.value}"
