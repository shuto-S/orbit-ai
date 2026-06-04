from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.autonomous.providers import AutonomousProvider, ProviderResult, default_providers
from app.memory.models import AutonomousNotification
from app.memory.store import MemoryStore
from app.memory.utils import utc_aware


class AutonomousScheduler:
    def __init__(
        self,
        store: MemoryStore,
        providers: dict[str, AutonomousProvider] | None = None,
        lock_owner: str | None = None,
        retry_after_seconds: int = 300,
    ) -> None:
        self.store = store
        self.providers = providers or default_providers(store)
        self.lock_owner = lock_owner or f"orbit-{uuid.uuid4()}"
        self.retry_after_seconds = max(1, retry_after_seconds)

    def tick(self, now: datetime | None = None, limit: int = 5) -> list[AutonomousNotification]:
        current = utc_aware(now or datetime.now(UTC))
        created_notification_ids: list[int] = []
        jobs = self.store.claim_due_autonomous_jobs(current, self.lock_owner, limit=limit)
        for job in jobs:
            notification_id = self._run_job(job.id, current)
            if notification_id is not None:
                created_notification_ids.append(notification_id)
        return [
            notification
            for notification_id in created_notification_ids
            if (notification := self.store.get_autonomous_notification(notification_id)) is not None
        ]

    def _run_job(self, job_id: int, now: datetime) -> int | None:
        job = self.store.get_autonomous_job(job_id)
        if job is None:
            return None
        started_at = now
        provider = self.providers.get(job.kind)
        if provider is None:
            return self._record_failure(job_id, started_at, now, f"unknown autonomous provider: {job.kind}")

        try:
            result = provider.run(job, now)
        except Exception as exc:  # pragma: no cover - defensive safety path
            return self._record_failure(job_id, started_at, now, str(exc))

        completed_at = datetime.now(UTC)
        notification_id = self._add_notification(job_id, result)
        next_run_at, status = self._next_schedule(job, result, now)
        self.store.finish_autonomous_job_success(job_id, completed_at, next_run_at, status)
        self.store.add_autonomous_job_run(
            job_id=job_id,
            status="success",
            started_at=started_at,
            completed_at=completed_at,
            result={
                "should_notify": result.should_notify,
                "notification_id": notification_id,
                "metadata": result.metadata,
                "next_run_at": next_run_at,
                "status": status,
            },
        )
        return notification_id

    def _record_failure(
        self,
        job_id: int,
        started_at: datetime,
        completed_at: datetime,
        error: str,
    ) -> None:
        retry_at = (utc_aware(completed_at) + timedelta(seconds=self.retry_after_seconds)).isoformat()
        self.store.finish_autonomous_job_failure(job_id, completed_at, retry_at, error)
        self.store.add_autonomous_job_run(
            job_id=job_id,
            status="failure",
            started_at=started_at,
            completed_at=completed_at,
            result={},
            error=error,
        )
        return None

    def _add_notification(self, job_id: int, result: ProviderResult) -> int | None:
        if not result.should_notify:
            return None
        if not result.sources:
            return None
        return self.store.add_autonomous_notification(
            title=result.title or "自律通知",
            body=result.body,
            job_id=job_id,
            priority=0.7,
            sources=result.sources,
        )

    @staticmethod
    def _next_schedule(job: object, result: ProviderResult, now: datetime) -> tuple[str | None, str]:
        schedule_type = getattr(job, "schedule_type", "once")
        if schedule_type == "once":
            return None, "completed"
        next_run = result.next_run_at
        interval_seconds = getattr(job, "interval_seconds", None)
        if next_run is None and isinstance(interval_seconds, int) and interval_seconds > 0:
            next_run = utc_aware(now) + timedelta(seconds=interval_seconds)
        if next_run is None:
            return None, "paused"
        return utc_aware(next_run).isoformat(), "active"
