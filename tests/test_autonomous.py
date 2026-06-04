from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.autonomous.providers import ProviderResult
from app.autonomous.reminders import parse_reminder_request
from app.autonomous.runtime import AutonomousRuntime
from app.autonomous.scheduler import AutonomousScheduler
from app.config.loader import load_proactive_config, load_profile
from app.memory.models import AutonomousJob
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState
from tests.helpers.fakes import FakeResponseAgent


class FakeVoice:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak_async(self, text: str) -> None:
        self.spoken.append(text)


def test_reminder_parser_handles_japanese_relative_day_time_and_iso() -> None:
    zone = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 6, 4, 9, 0, tzinfo=zone)

    ten_minutes = parse_reminder_request("10分後 水を飲む", now=now)
    two_hours = parse_reminder_request("2時間後に休憩とリマインドして", now=now, require_intent=True)
    today = parse_reminder_request("今日 18:00 資料確認", now=now)
    tomorrow = parse_reminder_request("明日9時 朝会", now=now)
    iso = parse_reminder_request("2026-06-04T18:00:00+09:00 夕方確認", now=now)

    assert ten_minutes is not None
    assert ten_minutes.due_at == datetime(2026, 6, 4, 9, 10, tzinfo=zone)
    assert ten_minutes.text == "水を飲む"
    assert two_hours is not None
    assert two_hours.due_at == datetime(2026, 6, 4, 11, 0, tzinfo=zone)
    assert two_hours.text == "休憩"
    assert today is not None
    assert today.due_at == datetime(2026, 6, 4, 18, 0, tzinfo=zone)
    assert tomorrow is not None
    assert tomorrow.due_at == datetime(2026, 6, 5, 9, 0, tzinfo=zone)
    assert iso is not None
    assert iso.due_at == datetime(2026, 6, 4, 18, 0, tzinfo=zone)


def test_reminder_parser_returns_none_when_time_is_unparseable() -> None:
    assert parse_reminder_request("あとで水を飲むとリマインドして", require_intent=True) is None


def test_due_reminder_runs_once_and_creates_notification(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    due_at = datetime(2026, 6, 4, 0, 0, tzinfo=UTC)
    job_id = store.add_autonomous_job(
        kind="reminder",
        title="水を飲む",
        schedule_type="once",
        next_run_at=due_at.isoformat(),
        payload={"text": "水を飲む"},
        source="test",
    )
    assert job_id is not None
    scheduler = AutonomousScheduler(store)

    notifications = scheduler.tick(due_at + timedelta(minutes=1))
    second_tick = scheduler.tick(due_at + timedelta(minutes=2))

    assert len(notifications) == 1
    assert notifications[0].body == "リマインドです。水を飲む"
    assert notifications[0].sources[0]["kind"] == "autonomous_job"
    assert second_tick == []
    job = store.get_autonomous_job(job_id)
    assert job is not None
    assert job.status == "completed"
    assert job.next_run_at is None
    assert store.list_autonomous_job_runs(job_id)[0].status == "success"


def test_future_job_does_not_run(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    now = datetime(2026, 6, 4, 0, 0, tzinfo=UTC)
    job_id = store.add_autonomous_job(
        kind="reminder",
        title="未来の確認",
        schedule_type="once",
        next_run_at=(now + timedelta(hours=1)).isoformat(),
        payload={"text": "未来の確認"},
    )
    assert job_id is not None

    notifications = AutonomousScheduler(store).tick(now)

    assert notifications == []
    assert store.get_autonomous_job(job_id).status == "active"


def test_interval_local_due_task_job_advances_next_run_without_storm(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    now = datetime(2026, 6, 4, 0, 0, tzinfo=UTC)
    task_id = store.add_task("期限タスク", "test")
    assert task_id is not None
    assert store.snooze_task(task_id, (now - timedelta(minutes=1)).isoformat())
    job_id = store.add_autonomous_job(
        kind="local_due_tasks",
        title="期限タスク確認",
        schedule_type="interval",
        next_run_at=now.isoformat(),
        interval_seconds=3600,
    )
    assert job_id is not None
    scheduler = AutonomousScheduler(store)

    first = scheduler.tick(now)
    second = scheduler.tick(now)

    assert len(first) == 1
    assert "期限が来ているタスクがあります" in first[0].body
    assert second == []
    job = store.get_autonomous_job(job_id)
    assert job is not None
    assert job.status == "active"
    assert job.next_run_at == (now + timedelta(seconds=3600)).isoformat()


def test_failed_provider_records_run_and_retries(tmp_path: Path) -> None:
    class FailingProvider:
        kind = "failing"

        def run(self, job: AutonomousJob, now: datetime) -> ProviderResult:
            raise RuntimeError("provider failed")

    store = MemoryStore(tmp_path / "test.sqlite3")
    now = datetime(2026, 6, 4, 0, 0, tzinfo=UTC)
    job_id = store.add_autonomous_job(
        kind="failing",
        title="失敗する確認",
        schedule_type="interval",
        next_run_at=now.isoformat(),
        interval_seconds=3600,
    )
    assert job_id is not None
    scheduler = AutonomousScheduler(store, providers={"failing": FailingProvider()}, retry_after_seconds=300)

    notifications = scheduler.tick(now)

    assert notifications == []
    job = store.get_autonomous_job(job_id)
    assert job is not None
    assert job.last_error == "provider failed"
    assert job.next_run_at == (now + timedelta(seconds=300)).isoformat()
    assert store.list_autonomous_job_runs(job_id)[0].status == "failure"


def test_autonomous_runtime_delivers_only_when_idle_and_tracks_sources(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        autonomous_config={"default_timezone": "Asia/Tokyo"},
    )
    manager.start_conversation()
    notification_id = store.add_autonomous_notification(
        title="水を飲む",
        body="リマインドです。水を飲む",
        job_id=12,
        sources=[{"kind": "autonomous_job", "id": "12", "title": "水を飲む", "detail": "kind=reminder"}],
    )
    assert notification_id is not None
    voice = FakeVoice()
    outputs: list[str] = []
    runtime = AutonomousRuntime(
        store,
        manager,
        voice,  # type: ignore[arg-type]
        {"enabled": True, "delivery_mode": "speak_when_idle"},
        output=outputs.append,
    )

    delivered = runtime.deliver_pending(datetime(2026, 6, 4, 0, 0, tzinfo=UTC))
    source_output = manager.handle_input("そのソースは？")

    assert len(delivered) == 1
    assert outputs == ["リマインドです。水を飲む"]
    assert voice.spoken == ["リマインドです。水を飲む"]
    assert store.get_autonomous_notification(notification_id).status == "delivered"
    assert source_output.text is not None
    assert f"ソース: autonomous_notification #{notification_id}" in source_output.text
    assert "ソース: autonomous_job #12" in source_output.text


def test_autonomous_runtime_keeps_pending_during_conversation_states(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
    )
    notification_id = store.add_autonomous_notification(
        title="確認",
        body="リマインドです。確認",
        sources=[{"kind": "autonomous_job", "id": "1", "title": "確認"}],
    )
    assert notification_id is not None
    manager.state = SessionState.THINKING
    runtime = AutonomousRuntime(
        store,
        manager,
        FakeVoice(),  # type: ignore[arg-type]
        {"enabled": True, "delivery_mode": "speak_when_idle"},
        output=lambda _text: None,
    )

    delivered = runtime.deliver_pending(datetime(2026, 6, 4, 0, 0, tzinfo=UTC))

    assert delivered == []
    assert store.get_autonomous_notification(notification_id).status == "pending"


def test_conversation_creates_reminder_without_calling_llm(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    fake_agent = FakeResponseAgent()
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=fake_agent,  # type: ignore[arg-type]
        start_without_wake_word=True,
        autonomous_config={"default_timezone": "Asia/Tokyo"},
    )

    output = manager.handle_input("10分後に水を飲むとリマインドして")

    assert output.text is not None
    assert "リマインドを登録しました" in output.text
    jobs = store.list_autonomous_jobs(statuses=("active",), limit=20)
    assert len(jobs) == 1
    assert jobs[0].kind == "reminder"
    assert jobs[0].payload["text"] == "水を飲む"
    assert fake_agent.calls == []


def test_conversation_asks_for_missing_reminder_text(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        start_without_wake_word=True,
    )

    output = manager.handle_input("明日9時に教えて")

    assert output.text == "何をリマインドするかも教えてください。例: 10分後に水を飲むとリマインドして"
    assert store.list_autonomous_jobs(statuses=None, limit=20) == []
