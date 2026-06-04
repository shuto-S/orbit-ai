from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.memory.models import AutonomousJob
from app.memory.store import MemoryStore


@dataclass(frozen=True)
class ProviderResult:
    should_notify: bool
    title: str = ""
    body: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    next_run_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AutonomousProvider(Protocol):
    kind: str

    def run(self, job: AutonomousJob, now: datetime) -> ProviderResult: ...


class ReminderProvider:
    kind = "reminder"

    def run(self, job: AutonomousJob, now: datetime) -> ProviderResult:
        text = str(job.payload.get("text") or job.title).strip()
        body = f"リマインドです。{text}"
        return ProviderResult(
            should_notify=True,
            title=job.title,
            body=body,
            sources=[
                {
                    "kind": "autonomous_job",
                    "id": str(job.id),
                    "title": job.title,
                    "detail": f"kind={job.kind}, next_run_at={job.next_run_at}",
                }
            ],
            metadata={"provider": self.kind, "due_at": job.next_run_at, "now": now.astimezone(UTC).isoformat()},
        )


class LocalDueTaskProvider:
    kind = "local_due_tasks"

    def __init__(self, store: MemoryStore, limit: int = 5) -> None:
        self.store = store
        self.limit = limit

    def run(self, job: AutonomousJob, now: datetime) -> ProviderResult:
        tasks = self.store.list_due_tasks(now, limit=self.limit)
        if not tasks:
            return ProviderResult(
                should_notify=False,
                metadata={"provider": self.kind, "due_task_count": 0},
            )
        lines = "\n".join(f"- {task.title}" for task in tasks)
        sources = [
            {
                "kind": "task",
                "id": str(task.id),
                "title": task.title,
                "detail": f"status={task.status}, due_at={task.due_at}",
            }
            for task in tasks
        ]
        sources.append(
            {
                "kind": "autonomous_job",
                "id": str(job.id),
                "title": job.title,
                "detail": f"kind={job.kind}, interval_seconds={job.interval_seconds}",
            }
        )
        return ProviderResult(
            should_notify=True,
            title="期限到来タスク",
            body=f"期限が来ているタスクがあります。\n{lines}",
            sources=sources,
            metadata={"provider": self.kind, "due_task_count": len(tasks)},
        )


def default_providers(store: MemoryStore) -> dict[str, AutonomousProvider]:
    providers: list[AutonomousProvider] = [
        ReminderProvider(),
        LocalDueTaskProvider(store),
    ]
    return {provider.kind: provider for provider in providers}
