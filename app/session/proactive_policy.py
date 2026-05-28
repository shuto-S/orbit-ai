from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.proactive_agent import ProactiveAgent, ProactiveCandidate
from app.memory.store import MemoryStore


@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    candidate: ProactiveCandidate
    reason: str


class ProactivePolicy:
    def __init__(self, config: dict[str, Any], store: MemoryStore, agent: ProactiveAgent | None = None) -> None:
        self.config = config
        self.store = store
        self.agent = agent or ProactiveAgent()

    def evaluate(self, idle_since: datetime | None, now: datetime | None = None) -> ProactiveDecision:
        empty = ProactiveCandidate(False, 0.0, "", "")
        if not self.config.get("enabled", True):
            return ProactiveDecision(False, empty, "proactive disabled")

        now = now or datetime.now(UTC)
        min_idle = int(self.config.get("min_idle_seconds", 180))
        if idle_since is None or now - idle_since < timedelta(seconds=min_idle):
            return ProactiveDecision(False, empty, "idle時間が不足")

        if self._recent_reject(now):
            return ProactiveDecision(False, empty, "直近で拒否されたためcooldown中")

        if self._hourly_count(now) >= int(self.config.get("max_interventions_per_hour", 2)):
            return ProactiveDecision(False, empty, "1時間あたりの上限到達")

        if self._daily_count(now) >= int(self.config.get("max_interventions_per_day", 12)):
            return ProactiveDecision(False, empty, "1日あたりの上限到達")

        candidate = self.agent.build_candidate(self.store.latest_open_loops())
        if not candidate.should_speak:
            return ProactiveDecision(False, candidate, candidate.reason)
        return ProactiveDecision(True, candidate, candidate.reason)

    def _recent_reject(self, now: datetime) -> bool:
        cooldown = int(self.config.get("cooldown_after_reject_seconds", 1800))
        for event in self.store.recent_proactive_events(limit=10):
            if event.get("outcome") != "rejected":
                continue
            created_at = self._parse_time(str(event.get("created_at", "")))
            if created_at and now - created_at < timedelta(seconds=cooldown):
                return True
        return False

    def _hourly_count(self, now: datetime) -> int:
        return self._count_since(now - timedelta(hours=1))

    def _daily_count(self, now: datetime) -> int:
        return self._count_since(now - timedelta(days=1))

    def _count_since(self, threshold: datetime) -> int:
        count = 0
        for event in self.store.recent_proactive_events(limit=100):
            created_at = self._parse_time(str(event.get("created_at", "")))
            if created_at and created_at >= threshold:
                count += 1
        return count

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
