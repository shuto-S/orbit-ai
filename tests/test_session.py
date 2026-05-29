# ruff: noqa: F401,I001
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import numpy as np
import pytest

from app.actions import ActionRequest, create_default_dispatcher
from app.ai.app_server_backend import AppServerCodexBackend, BackendResponse, CodexAppServerError
from app.ai.response_agent import CODEX_ERROR_PREFIX, ResponseAgent
from app.ai.streaming import SentenceChunker
from app.config.autonomy import AutonomyLevel, parse_autonomy_config
from app.config.loader import (
    load_autonomy_config,
    load_permission_policy_config,
    load_proactive_config,
    load_profile,
)
from app.config.permission_policy import (
    ActionPermissionPolicy,
    PermissionDecision,
    PermissionPolicyConfig,
    evaluate_permission,
    parse_permission_policy_config,
)
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import DEFAULT_LATENCY_LOG_PATH, LatencyLogger
from app.main import (
    DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS,
    announce_shutdown,
    handle_daily_command,
    handle_proactive_command,
    handle_task_command,
    maybe_start_proactive_permission,
    proactive_check_interval_seconds,
    read_text_with_idle_ticks,
    show_tasks,
)
from app.memory.store import MemoryStore, parse_due_at, utc_aware
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text
from scripts.latency_summary import percentile, read_events
from scripts.stt_faster_whisper import RecordingState
from tests.helpers.fakes import ErrorBackend, FakeBackend, FakeResponseAgent, FakeRpcClient, FakeTranscriber

def test_wake_continue_confirm_end_and_persist(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    idle_output = manager.handle_input("今日は独り言")
    assert idle_output.text is None
    assert idle_output.state == SessionState.IDLE

    wake_output = manager.handle_input("オービット、相談したい")
    assert wake_output.session_id is not None
    assert wake_output.state == SessionState.WAITING_FOR_NEXT_TURN
    assert "受け取りました" in (wake_output.text or "")

    continue_output = manager.handle_input("このアプリのMVPを整理したい")
    assert continue_output.state == SessionState.WAITING_FOR_NEXT_TURN
    assert "MVP" in (continue_output.text or "")

    confirm_output = manager.handle_input("ありがとう")
    assert confirm_output.state == SessionState.CONFIRMING_END
    assert "ここまで" in (confirm_output.text or "")

    closing_output = manager.handle_input("うん")
    assert closing_output.state == SessionState.IDLE
    assert closing_output.session_id is None

    assert len(store.list_summaries()) >= 1
    assert len(store.list_memories()) >= 1
    messages = store.connect().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert messages >= 5


def test_startup_can_begin_without_wake_word_once() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
            start_without_wake_word=True,
        )

        first_output = manager.handle_input("今日の予定を整理したい")

        assert first_output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert first_output.session_id is not None
        assert "受け取りました" in (first_output.text or "")

        manager.handle_input("ありがとう")
        closed_output = manager.handle_input("うん")
        assert closed_output.state == SessionState.IDLE

        idle_output = manager.handle_input("もう一度相談したい")

        assert idle_output.text is None
        assert idle_output.state == SessionState.IDLE


def test_start_conversation_greets_and_waits_for_user_turn() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        fake_agent = FakeResponseAgent()
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=fake_agent,  # type: ignore[arg-type]
        )

        startup_output = manager.start_conversation()

        assert startup_output.text == "こんにちは。何から始めますか？"
        assert startup_output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert startup_output.session_id is not None
        messages = store.get_session_messages(startup_output.session_id)
        assert [(message.role, message.content) for message in messages] == [
            ("assistant", "こんにちは。何から始めますか？")
        ]

        next_output = manager.handle_input("今日の予定を整理したい")

        assert next_output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert "受け取りました" in (next_output.text or "")
        assert fake_agent.calls == ["今日の予定を整理したい"]


def test_utc_aware_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 5, 28, 12, 0)

    assert utc_aware(naive) == datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def test_negative_end_confirmation_continues_session(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    _, manager = mvp_context

    manager.handle_input("オービット、相談したい")
    manager.handle_input("ありがとう")
    output = manager.handle_input("まだ続けて")

    assert output.state == SessionState.WAITING_FOR_NEXT_TURN
    assert output.session_id is not None
    assert "続け" in (output.text or "")


def test_wake_greeting_does_not_resume_old_topic() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        fake_agent = FakeResponseAgent()
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=fake_agent,  # type: ignore[arg-type]
        )
        store.add_summary(
            session_id="previous",
            summary="このアプリのMVP整理を続ける",
            open_loops=["MVP整理の続き"],
            decisions=[],
            follow_up_candidates=["このアプリのMVP整理、続けますか？"],
        )

        output = manager.handle_input("オービットさん。こんにちは")

        assert output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert output.text == "こんにちは。"
        assert fake_agent.calls == []


def test_wake_morning_greeting_gets_greeting_response() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        fake_agent = FakeResponseAgent()
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=fake_agent,  # type: ignore[arg-type]
        )

        output = manager.handle_input("オービットおはよう")

        assert output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert output.text == "おはようございます。"
        assert fake_agent.calls == []


def test_short_wake_word_can_start_session(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    _, manager = mvp_context

    output = manager.handle_input("オル、相談したい")

    assert output.state == SessionState.WAITING_FOR_NEXT_TURN
    assert output.session_id is not None
    assert output.text is not None


@pytest.mark.parametrize(
    "user_text",
    [
        "おーびっと、相談したい",
        "おおびっと、相談したい",
        "Ｏｒｂｉｔ、相談したい",
        "orbit、相談したい",
        "おる、相談したい",
        "ORBIT、相談したい",
    ],
)
def test_wake_word_variants_can_start_session(user_text: str) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        )

        output = manager.handle_input(user_text)

        assert output.state == SessionState.WAITING_FOR_NEXT_TURN
        assert output.session_id is not None
        assert output.text is not None


def test_blank_end_confirmation_repeats_confirmation(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    _, manager = mvp_context

    manager.handle_input("オービット、相談したい")
    confirmation = manager.handle_input("終了して")
    output = manager.handle_input("")

    assert output.state == SessionState.CONFIRMING_END
    assert output.text == confirmation.text


def test_surrogate_input_is_sanitized_before_sqlite_write(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    manager.handle_input("オービットさん。こんにちは")
    output = manager.handle_input("メール\udce3ボックスで受け取ってるメールを要約して")

    assert output.text is not None
    saved = store.get_session_messages(manager.session_id_or_raise())
    assert any("メール�ボックス" in message.content for message in saved)


def test_sanitize_text_replaces_invalid_surrogates() -> None:
    assert sanitize_text("abc\udce3def") == "abc�def"

