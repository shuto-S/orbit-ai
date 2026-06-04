from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config.loader import load_proactive_config, load_profile
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.startup_briefing import StartupBriefingService
from app.session.state import SessionState
from tests.helpers.fakes import FakeResponseAgent


def test_startup_briefing_returns_none_without_relevant_state(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")

    briefing = StartupBriefingService().build(store, now=_now())

    assert briefing is None


def test_startup_briefing_falls_back_when_legacy_task_schema_is_missing_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              priority REAL DEFAULT 0.5,
              due_at TEXT,
              source TEXT,
              source_session_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (title, status, created_at, updated_at)
            VALUES ('古いDBのタスク', 'open', '2026-06-04T00:00:00+00:00', '2026-06-04T00:00:00+00:00')
            """
        )
    store = MemoryStore(db_path)

    briefing = StartupBriefingService().build(store, now=_now())

    assert briefing is None


def test_startup_briefing_uses_open_tasks(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    store.add_task("Orbit AIの起動後自立動作設計", "manual", priority=0.9)

    briefing = StartupBriefingService().build(store, now=_now())

    assert briefing is not None
    assert briefing.reason == "open_task"
    assert "未完了の項目" in briefing.text
    assert "Orbit AIの起動後自立動作設計" in briefing.text
    assert briefing.text.count("？") <= 1
    assert briefing.suggested_actions[0] == "resume:Orbit AIの起動後自立動作設計"


def test_startup_briefing_prioritizes_overdue_snoozed_task(tmp_path: Path) -> None:
    now = _now()
    store = MemoryStore(tmp_path / "test.sqlite3")
    open_id = store.add_task("普通の未完了タスク", "manual", priority=1.0)
    due_id = store.add_task("期限到来の確認", "manual", priority=0.1)
    assert open_id is not None
    assert due_id is not None
    store.snooze_task(due_id, (now - timedelta(minutes=1)).isoformat())

    briefing = StartupBriefingService().build(store, now=now)

    assert briefing is not None
    assert briefing.reason == "due_task"
    assert "期限が来ている未完了項目" in briefing.text
    assert "期限到来の確認" in briefing.text
    assert "まずは「期限到来の確認の続き」から始めますか？" in briefing.text


def test_startup_briefing_caps_output_to_three_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    for index in range(4):
        store.add_task(f"未完了項目{index + 1}", "manual", priority=1.0 - index * 0.1)

    briefing = StartupBriefingService().build(store, now=_now())

    assert briefing is not None
    assert "未完了項目1" in briefing.text
    assert "未完了項目2" in briefing.text
    assert "未完了項目3" in briefing.text
    assert "未完了項目4" not in briefing.text
    assert "3件以上" in briefing.text


def test_start_conversation_uses_startup_briefing_and_stores_message(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    store.add_open_loop(
        "起動後の自立動作",
        summary="起動時ブリーフィングとタスク抽出の範囲が未決定",
        suggested_next_step="起動時ブリーフィングから決める",
    )
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
    )

    output = manager.start_conversation()

    assert output.state == SessionState.WAITING_FOR_NEXT_TURN
    assert output.session_id is not None
    assert output.text is not None
    assert "起動後の自立動作" in output.text
    assert "起動時ブリーフィングから決める" in output.text
    messages = store.get_session_messages(output.session_id)
    assert [(message.role, message.content) for message in messages] == [("assistant", output.text)]


def _now() -> datetime:
    return datetime(2026, 6, 4, 9, 0, tzinfo=UTC)
