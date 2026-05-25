import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from os import getenv
from typing import Any


@dataclass
class LatencyLogger:
    enabled: bool = False
    turn_started_at: float | None = None
    last_event_at: float | None = None
    event_started_at: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "LatencyLogger":
        latency = profile.get("latency", {})
        profile_enabled = isinstance(latency, dict) and bool(latency.get("enabled", False))
        return cls(enabled=profile_enabled or getenv("ORBIT_AI_LATENCY_LOG") == "1")

    def start_turn(self) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        self.turn_started_at = now
        self.last_event_at = now

    def event(self, name: str, **fields: object) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if self.turn_started_at is None:
            self.start_turn()
        since_turn = now - (self.turn_started_at or now)
        since_last = now - (self.last_event_at or now)
        self.last_event_at = now
        extras = " ".join(f"{key}={value}" for key, value in fields.items())
        suffix = f" {extras}" if extras else ""
        print(f"latency event={name} total={since_turn:.3f}s delta={since_last:.3f}s{suffix}", file=sys.stderr)

    @contextmanager
    def span(self, name: str, **fields: object) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        self.event(f"{name}.start", **fields)
        try:
            yield
        finally:
            duration = time.perf_counter() - start
            self.event(f"{name}.end", duration=f"{duration:.3f}s", **fields)


DISABLED_LATENCY_LOGGER = LatencyLogger(False)
