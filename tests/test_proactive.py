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
from app.ai.proactive_agent import ProactiveCandidate
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

def test_proactive_permission_flow_and_reject_cooldown(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    store.add_summary(
        session_id="previous",
        summary="There is an open issue",
        open_loops=["MVP設計の続き"],
        decisions=[],
        follow_up_candidates=[],
    )
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()
    assert decision.allowed
    assert "今話してもいいですか" in decision.candidate.permission_text

    permission_output = manager.start_proactive_permission(decision.candidate.permission_text)
    assert permission_output.state == SessionState.PROACTIVE_PERMISSION_CHECK

    reject_output = manager.handle_input("今は無理")
    assert reject_output.state == SessionState.IDLE

    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)
    cooldown_decision = manager.check_proactive()
    assert not cooldown_decision.allowed
    assert "cooldown" in cooldown_decision.reason


def test_decision_log_roundtrip(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, _ = mvp_context

    store.add_decision_log(
        kind="proactive_check",
        session_id="session-1",
        task_id=12,
        candidate_text="今話してもいいですか？",
        decision="ask_permission",
        reason="open_loop",
        score=0.7,
        metadata={"trigger": "manual", "state": "idle"},
    )

    logs = store.recent_decision_logs()

    assert len(logs) == 1
    assert logs[0].kind == "proactive_check"
    assert logs[0].session_id == "session-1"
    assert logs[0].task_id == 12
    assert logs[0].candidate_text == "今話してもいいですか？"
    assert logs[0].decision == "ask_permission"
    assert logs[0].reason == "open_loop"
    assert logs[0].score == 0.7
    assert json.loads(logs[0].metadata_json or "{}") == {"trigger": "manual", "state": "idle"}
    assert logs[0].created_at


def test_proactive_allowed_records_manual_decision_log(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive(trigger="manual")

    assert decision.allowed
    logs = store.recent_decision_logs()
    assert logs[0].kind == "proactive_check"
    assert logs[0].decision == "ask_permission"
    assert logs[0].reason == "open_loop"
    assert "請求書の確認" in (logs[0].candidate_text or "")
    assert logs[0].created_at
    assert json.loads(logs[0].metadata_json or "{}")["trigger"] == "manual"


def test_proactive_denied_records_decision_log(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    manager.idle_since = datetime.now(UTC)

    decision = manager.check_proactive(trigger="manual")

    assert not decision.allowed
    logs = store.recent_decision_logs()
    assert logs[0].decision == "deny"
    assert logs[0].reason == "idle時間が不足"
    assert logs[0].candidate_text is None
    assert logs[0].created_at


def test_manual_proactive_command_does_not_duplicate_prompt_when_not_idle(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context
    voice_config = replace(VoiceConfig.from_profile(load_profile()), input_enabled=False, output_enabled=False)
    voice = VoiceIO(voice_config)
    store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    assert handle_proactive_command(manager, voice) is True
    assert manager.state == SessionState.PROACTIVE_PERMISSION_CHECK
    assert handle_proactive_command(manager, voice) is False

    events = store.recent_proactive_events()
    assert [event["outcome"] for event in events] == ["proposed"]
    logs = store.recent_decision_logs()
    assert [json.loads(log.metadata_json or "{}")["trigger"] for log in logs[:2]] == ["manual", "manual"]


def test_proactive_policy_uses_open_tasks(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()

    assert decision.allowed
    assert "請求書の確認" in decision.candidate.permission_text


def test_proactive_policy_uses_due_snoozed_tasks_and_skips_future_snoozed(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context

    due_id = store.add_task("期限到来の確認", "open_loop", source_session_id="previous")
    future_id = store.add_task("期限前の確認", "open_loop", source_session_id="previous")
    assert due_id is not None
    assert future_id is not None
    store.snooze_task(due_id, (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    store.snooze_task(future_id, (datetime.now(UTC) + timedelta(days=1)).isoformat())
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()

    assert decision.allowed
    assert "期限到来の確認" in decision.candidate.permission_text
    assert "期限前の確認" not in decision.candidate.permission_text


def test_proactive_policy_populates_open_loop_resume_metadata(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context
    loop_id = store.add_open_loop(
        "起動後の自立動作",
        summary="起動時ブリーフィングとタスク抽出の範囲が未決定",
        suggested_next_step="起動時ブリーフィングから決める",
    )
    assert loop_id is not None
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive(trigger="manual")

    assert decision.allowed
    assert decision.candidate.topic == "起動後の自立動作"
    assert decision.candidate.source_type == "open_loop"
    assert decision.candidate.source_id == str(loop_id)
    assert decision.candidate.summary == "起動時ブリーフィングとタスク抽出の範囲が未決定"
    assert decision.candidate.suggested_next_step == "起動時ブリーフィングから決める"
    log_metadata = json.loads(store.recent_decision_logs()[0].metadata_json or "{}")
    assert log_metadata["candidate_source_type"] == "open_loop"


def test_proactive_policy_does_not_fall_back_to_completed_or_snoozed_summary_topics(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context
    store.add_summary(
        session_id="previous",
        summary="follow up",
        open_loops=["請求書の確認", "見積もりの確認"],
        decisions=[],
        follow_up_candidates=[],
    )
    done_id = store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    snoozed_id = store.add_task("見積もりの確認", "open_loop", source_session_id="previous")
    assert done_id is not None
    assert snoozed_id is not None
    store.mark_task_done(done_id)
    store.snooze_task(snoozed_id, (datetime.now(UTC) + timedelta(days=1)).isoformat())
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()

    assert not decision.allowed
    assert decision.reason == "no_open_loops"


def test_proactive_accept_uses_candidate_accepted_prompt(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context
    candidate = ProactiveCandidate(
        True,
        0.7,
        "続きについて今話してもいいですか？",
        "open_loop",
        topic="起動後の自立動作",
        source_type="open_loop",
        source_id="42",
        accepted_prompt="前回は「起動後の自立動作」を詰めていました。\nまず起動時ブリーフィングから決めますか？",
    )

    manager.start_proactive_permission(candidate.permission_text, candidate)
    accepted = manager.handle_input("はい")

    assert accepted.state == SessionState.WAITING_FOR_NEXT_TURN
    assert accepted.text == "前回は「起動後の自立動作」を詰めていました。\nまず起動時ブリーフィングから決めますか？"
    assert "42" not in (accepted.text or "")
    events = store.recent_proactive_events()
    assert [event["outcome"] for event in events[:2]] == ["accepted", "proposed"]


def test_proactive_accept_synthesizes_response_from_candidate_metadata(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    _, manager = mvp_context
    candidate = ProactiveCandidate(
        True,
        0.7,
        "さっきの続きで今話してもいいですか？",
        "open_loop",
        topic="起動後の自立動作",
        summary="起動時ブリーフィングとタスク自動抽出が未決定",
        suggested_next_step="起動時ブリーフィングから決める",
    )

    manager.start_proactive_permission(candidate.permission_text, candidate)
    accepted = manager.handle_input("はい")

    assert accepted.text is not None
    assert "起動後の自立動作" in accepted.text
    assert "起動時ブリーフィングとタスク自動抽出が未決定" in accepted.text
    assert "起動時ブリーフィングから決める" in accepted.text
    assert accepted.text.count("？") <= 1


def test_proactive_accept_without_metadata_uses_fallback(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    _, manager = mvp_context

    manager.start_proactive_permission(
        "さっきの件で、1つ確認したいことがあります。今話してもいいですか？",
        ProactiveCandidate(True, 0.7, "さっきの件で、1つ確認したいことがあります。今話してもいいですか？", "open_loop"),
    )
    accepted = manager.handle_input("はい")

    assert accepted.text == (
        "ありがとうございます。では、未完了の論点から短く整理します。どこまで決めるか確認したいです。"
    )


def test_proactive_reject_clears_pending_candidate(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    _, manager = mvp_context
    candidate = ProactiveCandidate(
        True,
        0.7,
        "続きについて今話してもいいですか？",
        "open_loop",
        topic="起動後の自立動作",
    )

    manager.start_proactive_permission(candidate.permission_text, candidate)
    rejected = manager.handle_input("今は無理")

    assert rejected.state == SessionState.IDLE
    assert manager.pending_proactive_text == ""
    assert manager.pending_proactive_candidate is None


def test_proactive_check_interval_config_defaults_and_clamps() -> None:
    assert proactive_check_interval_seconds({}) == DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    assert (
        proactive_check_interval_seconds({"check_interval_seconds": "bad"})
        == DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    )
    assert proactive_check_interval_seconds({"check_interval_seconds": 0}) == 1
    assert proactive_check_interval_seconds({"check_interval_seconds": "5"}) == 5


def test_periodic_proactive_tick_starts_permission_and_logs_event(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context
    voice_config = replace(VoiceConfig.from_profile(load_profile()), input_enabled=False, output_enabled=False)
    voice = VoiceIO(voice_config)

    store.add_summary(
        session_id="previous",
        summary="There is an open issue",
        open_loops=["次回リリースの確認"],
        decisions=[],
        follow_up_candidates=[],
    )
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    started = maybe_start_proactive_permission(manager, voice)

    assert started is True
    assert manager.state == SessionState.PROACTIVE_PERMISSION_CHECK
    assert manager.session_id is None
    events = store.recent_proactive_events()
    assert events[0]["outcome"] == "proposed"
    assert "次回リリースの確認" in events[0]["proposed_text"]
    logs = store.recent_decision_logs()
    assert logs[0].decision == "ask_permission"
    assert json.loads(logs[0].metadata_json or "{}")["trigger"] == "idle"

    accepted = manager.handle_input("はい")

    assert accepted.state == SessionState.WAITING_FOR_NEXT_TURN
    events = store.recent_proactive_events()
    assert [event["outcome"] for event in events[:2]] == ["accepted", "proposed"]
    assert events[0]["user_response"] == "はい"


def test_text_input_timeout_tick_preserves_reject_logging(
    monkeypatch: pytest.MonkeyPatch,
    mvp_context: tuple[MemoryStore, SessionManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, manager = mvp_context
    voice_config = replace(VoiceConfig.from_profile(load_profile()), input_enabled=False, output_enabled=False)
    voice = VoiceIO(voice_config)

    store.add_summary(
        session_id="previous",
        summary="There is an open issue",
        open_loops=["未完了タスクの確認"],
        decisions=[],
        follow_up_candidates=[],
    )
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    class FakeStdin:
        def readline(self) -> str:
            return "今は無理\n"

    fake_stdin = FakeStdin()
    select_results: list[list[object]] = [[], [fake_stdin]]

    def fake_select(
        read_list: list[object], _: list[object], __: list[object], timeout: int
    ) -> tuple[list[object], list[object], list[object]]:
        assert timeout == 1
        return select_results.pop(0), [], []

    monkeypatch.setattr("app.cli.runtime.sys.stdin", fake_stdin)
    monkeypatch.setattr("app.cli.runtime.select.select", fake_select)

    user_text = read_text_with_idle_ticks(
        voice,
        1,
        lambda: maybe_start_proactive_permission(manager, voice, leading_newline=True),
    )
    output = manager.handle_input(user_text)

    assert output.state == SessionState.IDLE
    events = store.recent_proactive_events()
    assert [event["outcome"] for event in events[:2]] == ["rejected", "proposed"]
    assert events[0]["user_response"] == "今は無理"
    captured = capsys.readouterr()
    assert "AI:" in captured.out
