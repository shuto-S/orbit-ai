import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import getenv
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class LatencyLogger:
    enabled: bool = False
    log_path: Path | None = None
    session_id: str | None = None
    turn_id: str | None = None
    turn_started_at: float | None = None
    last_event_at: float | None = None
    event_started_at: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "LatencyLogger":
        latency = profile.get("latency", {})
        profile_enabled = isinstance(latency, dict) and bool(latency.get("enabled", False))
        env_enabled = getenv("ORBIT_AI_LATENCY_LOG") == "1"
        log_path = getenv("ORBIT_AI_LATENCY_LOG_PATH")
        if not log_path and isinstance(latency, dict):
            profile_log_path = latency.get("log_path")
            if isinstance(profile_log_path, str) and profile_log_path:
                log_path = profile_log_path
        return cls(enabled=profile_enabled or env_enabled, log_path=Path(log_path) if log_path else None)

    def start_turn(self, session_id: str | None = None) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if session_id is not None:
            self.session_id = session_id
        self.turn_id = uuid4().hex
        self.turn_started_at = now
        self.last_event_at = now

    def event(self, name: str, session_id: str | None = None, **fields: object) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if self.turn_started_at is None:
            self.start_turn(session_id=session_id)
        elif session_id is not None:
            self.session_id = session_id
        since_turn = now - (self.turn_started_at or now)
        since_last = now - (self.last_event_at or now)
        self.last_event_at = now
        extras = " ".join(f"{key}={value}" for key, value in fields.items())
        suffix = f" {extras}" if extras else ""
        print(f"latency event={name} total={since_turn:.3f}s delta={since_last:.3f}s{suffix}", file=sys.stderr)
        self._write_jsonl(name, since_turn * 1000, fields)

    @contextmanager
    def span(self, name: str, session_id: str | None = None, **fields: object) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        self.event(f"{name}.start", session_id=session_id, **fields)
        try:
            yield
        finally:
            duration = time.perf_counter() - start
            end_fields = {"duration": f"{duration:.3f}s", "duration_ms": duration * 1000, **fields}
            self.event(f"{name}.end", session_id=session_id, **end_fields)

    def _write_jsonl(self, name: str, elapsed_ms: float, fields: dict[str, object]) -> None:
        if self.log_path is None:
            return
        payload = {
            "event": name,
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "elapsed_ms": round(elapsed_ms, 3),
        }
        payload.update(fields)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError:
            return


DISABLED_LATENCY_LOGGER = LatencyLogger(False)
