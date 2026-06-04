from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.proactive_agent import ProactiveAgent, ProactiveCandidate
from app.config.autonomy import AutonomyConfig
from app.memory.store import MemoryStore


@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    candidate: ProactiveCandidate
    reason: str


class ProactivePolicy:
    def __init__(
        self,
        config: dict[str, Any],
        store: MemoryStore,
        agent: ProactiveAgent | None = None,
        autonomy: AutonomyConfig | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.agent = agent or ProactiveAgent()
        self.autonomy = autonomy or AutonomyConfig()

    def evaluate(self, idle_since: datetime | None, now: datetime | None = None) -> ProactiveDecision:
        empty = ProactiveCandidate(False, 0.0, "", "")
        if not self.autonomy.allows_proactive_suggestions():
            return ProactiveDecision(False, empty, "autonomy off")
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

        contexts = self._resume_contexts(now)
        candidate = self.agent.build_candidate([context["topic"] or "" for context in contexts], contexts=contexts)
        if not candidate.should_speak:
            return ProactiveDecision(False, candidate, candidate.reason)
        return ProactiveDecision(True, candidate, candidate.reason)

    def _resume_contexts(self, now: datetime, limit: int = 5) -> list[dict[str, str | None]]:
        contexts: list[dict[str, str | None]] = []
        selected_titles: set[str] = set()

        for task in self.store.list_due_tasks(now, limit=limit):
            self._append_context(
                contexts,
                selected_titles,
                {
                    "topic": task.title,
                    "source_type": "task",
                    "source_id": str(task.id),
                    "summary": task.description,
                    "suggested_next_step": _task_next_step(task.title),
                    "accepted_prompt": None,
                },
                limit,
            )
        for task in self.store.list_tasks(statuses=("open",), limit=limit):
            self._append_context(
                contexts,
                selected_titles,
                {
                    "topic": task.title,
                    "source_type": "task",
                    "source_id": str(task.id),
                    "summary": task.description,
                    "suggested_next_step": _task_next_step(task.title),
                    "accepted_prompt": None,
                },
                limit,
            )
        known_task_titles = self.store.task_titles()
        for loop in self.store.list_open_loops(statuses=("open",), limit=limit):
            if loop.title in known_task_titles:
                continue
            self._append_context(
                contexts,
                selected_titles,
                {
                    "topic": loop.title,
                    "source_type": "open_loop",
                    "source_id": str(loop.id),
                    "summary": loop.summary,
                    "suggested_next_step": loop.suggested_next_step,
                    "accepted_prompt": None,
                },
                limit,
            )
        for summary in self.store.list_summaries(limit=20):
            for title in summary.open_loops:
                if title in known_task_titles:
                    continue
                self._append_context(
                    contexts,
                    selected_titles,
                    {
                        "topic": title,
                        "source_type": "summary_open_loop",
                        "source_id": summary.session_id,
                        "summary": summary.summary,
                        "suggested_next_step": None,
                        "accepted_prompt": None,
                    },
                    limit,
                )
            for title in summary.follow_up_candidates:
                if title in known_task_titles:
                    continue
                self._append_context(
                    contexts,
                    selected_titles,
                    {
                        "topic": title,
                        "source_type": "summary_follow_up",
                        "source_id": summary.session_id,
                        "summary": summary.summary,
                        "suggested_next_step": None,
                        "accepted_prompt": None,
                    },
                    limit,
                )
        return contexts[:limit]

    @staticmethod
    def _append_context(
        contexts: list[dict[str, str | None]],
        selected_titles: set[str],
        context: dict[str, str | None],
        limit: int,
    ) -> None:
        topic = context.get("topic")
        if not topic or topic in selected_titles or len(contexts) >= limit:
            return
        contexts.append(context)
        selected_titles.add(topic)

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


def _task_next_step(title: str) -> str:
    return f"{title}の次の一手を確認する"
