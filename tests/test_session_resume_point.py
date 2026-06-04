from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import SessionSummarizer
from app.session.lifecycle import close_session
from app.session.startup_briefing import StartupBriefingService


def test_close_session_persists_resume_point_from_unresolved_topic(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    session_id = "session-1"
    store.add_message(session_id, "user", "起動後ブリーフィングの実装方針を検討したい")
    store.add_message(session_id, "assistant", "起動後に未完了項目を短く出す方針がよさそうです。")

    assistant_text = close_session(store, session_id, SessionSummarizer(), MemoryExtractor())

    assert "次回は「起動後ブリーフィングの実装方針を検討したい」から再開できます。" in assistant_text
    resume_point = store.latest_resume_point()
    assert resume_point is not None
    assert resume_point.title == "起動後ブリーフィングの実装方針を検討したい"
    assert resume_point.suggested_next_step == "起動後ブリーフィングの実装方針を検討したいの次の一手を決める"
    assert resume_point.metadata["kind"] == "next_resume_point"
    logs = store.recent_decision_logs()
    assert logs[0].kind == "session_resume_point"
    assert json.loads(logs[0].metadata_json or "{}")["suggested_next_action"].endswith("次の一手を決める")


def test_close_session_uses_recent_message_fallback_for_resume_point(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    session_id = "session-1"
    store.add_message(session_id, "user", "OpenLoopモデルを実装している")
    store.add_message(session_id, "assistant", "OpenLoopモデルの保存先を確認しました。")

    close_session(store, session_id, SessionSummarizer(), MemoryExtractor())

    resume_point = store.latest_resume_point()
    assert resume_point is not None
    assert resume_point.title == "OpenLoopモデルを実装している"
    assert resume_point.metadata["reason"] == "recent_message_fallback"


def test_close_session_skips_noisy_casual_resume_point(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    session_id = "session-1"
    store.add_message(session_id, "user", "今日は雑談しよう")
    store.add_message(session_id, "assistant", "いいですね。")

    assistant_text = close_session(store, session_id, SessionSummarizer(), MemoryExtractor())

    assert assistant_text == "わかりました。また呼んでください。"
    assert store.latest_resume_point() is None
    assert [log.kind for log in store.recent_decision_logs()] == []


def test_close_session_does_not_save_sensitive_resume_point(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    session_id = "session-1"
    store.add_message(session_id, "user", "API key is secret をあとで確認したい")

    assistant_text = close_session(store, session_id, SessionSummarizer(), MemoryExtractor())

    assert assistant_text == "わかりました。また呼んでください。"
    assert store.latest_resume_point() is None
    assert [log.kind for log in store.recent_decision_logs()] == []


def test_startup_briefing_can_retrieve_latest_resume_point(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    session_id = "session-1"
    store.add_message(session_id, "user", "OpenLoopモデルを実装している")
    close_session(store, session_id, SessionSummarizer(), MemoryExtractor())

    briefing = StartupBriefingService().build(store, now=datetime(2026, 6, 4, 9, 0, tzinfo=UTC))

    assert briefing is not None
    assert "OpenLoopモデルを実装している" in briefing.text
    assert "次の一手を決める" in briefing.text
