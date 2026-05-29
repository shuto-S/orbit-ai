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

def test_session_close_creates_tasks_from_open_loops_without_duplicates(
    mvp_context: tuple[MemoryStore, SessionManager],
) -> None:
    store, manager = mvp_context

    manager.handle_input("オービット、あとで確認したいことがある")
    manager.handle_input("ありがとう")
    manager.handle_input("うん")

    tasks = store.list_tasks()
    assert [task.title for task in tasks].count("あとで確認したいことがある") == 1
    assert tasks[0].status == "open"
    assert tasks[0].source_session_id is not None

    store.add_tasks_from_summary(
        session_id="duplicate",
        open_loops=["あとで確認したいことがある"],
        follow_up_candidates=["あとで確認したいことがある"],
    )

    tasks_after_duplicate = store.list_tasks()
    assert [task.title for task in tasks_after_duplicate].count("あとで確認したいことがある") == 1


def test_task_command_marks_done_and_snoozes(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        first_id = store.add_task("見積もりを確認する", "open_loop")
        second_id = store.add_task("明日連絡する", "follow_up_candidate")
        assert first_id is not None
        assert second_id is not None

        show_tasks(store)
        handle_task_command(store, f"/task done {first_id}")
        handle_task_command(store, f"/task snooze {second_id} tomorrow morning")

        output = capsys.readouterr().out
        assert "見積もりを確認する" in output
        assert f"Task #{first_id} marked done." in output
        assert f"Task #{second_id} snoozed until tomorrow morning." in output
        tasks = {task.id: task for task in store.list_tasks(statuses=("done", "snoozed"))}
        assert tasks[first_id].status == "done"
        assert tasks[second_id].status == "snoozed"
        assert tasks[second_id].due_at == "tomorrow morning"


def test_daily_command_outputs_candidates_and_saves_review(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        open_id = store.add_task("請求書の確認", "open_loop")
        snoozed_id = store.add_task("リリース前確認", "follow_up_candidate")
        assert open_id is not None
        assert snoozed_id is not None
        store.snooze_task(snoozed_id, "tomorrow morning")
        store.add_summary(
            session_id="previous",
            summary="次回ミーティングの準備が残っている",
            open_loops=["次回ミーティングの論点整理"],
            decisions=[],
            follow_up_candidates=["資料の送付確認"],
        )

        plan = handle_daily_command(store)

        output = capsys.readouterr().out
        assert "今日の確認候補です" in output
        assert f"[task #{open_id}] 請求書の確認" in output
        assert f"[snoozed #{snoozed_id}] リリース前確認" in output
        assert "[open_loop] 次回ミーティングの論点整理" in output
        assert "[follow_up_candidate] 資料の送付確認" in output
        assert "次回ミーティングの準備が残っている" in output
        assert [item.title for item in plan.items] == [
            "請求書の確認",
            "リリース前確認",
            "次回ミーティングの論点整理",
            "資料の送付確認",
        ]

        reviews = store.recent_daily_reviews()
        assert len(reviews) == 1
        assert reviews[0].summary.startswith("今日の確認候補:")
        assert reviews[0].items[0] == {
            "source": "task",
            "id": open_id,
            "title": "請求書の確認",
            "reason": "open task",
        }
        assert reviews[0].items[1]["source"] == "snoozed"
        assert reviews[0].items[1]["reason"] == "snoozed until tomorrow morning"


def test_daily_command_outputs_empty_state_and_saves_review(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")

        handle_daily_command(store)

        output = capsys.readouterr().out
        assert "今日の確認候補はありません" in output
        reviews = store.recent_daily_reviews()
        assert len(reviews) == 1
        assert reviews[0].summary == "今日の確認候補はありません。"
        assert reviews[0].items == []


def test_daily_review_does_not_restore_closed_tasks_from_old_summary() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        store.add_summary(
            session_id="previous",
            summary="closed task follow-up",
            open_loops=["請求書の確認"],
            decisions=[],
            follow_up_candidates=["資料の送付確認"],
        )
        done_id = store.add_task("請求書の確認", "open_loop", source_session_id="previous")
        cancelled_id = store.add_task("資料の送付確認", "follow_up_candidate", source_session_id="previous")

        assert done_id is not None
        assert cancelled_id is not None
        store.mark_task_done(done_id)
        store._update_task_status(cancelled_id, "cancelled")

        plan = handle_daily_command(store)

        assert plan.items == []


def test_parse_due_at_accepts_iso_and_treats_naive_as_utc() -> None:
    zoned = parse_due_at("2026-05-28T10:00:00+09:00")
    date_only = parse_due_at("2026-05-28")
    natural_language = parse_due_at("tomorrow morning")

    assert zoned is not None
    assert zoned.isoformat() == "2026-05-28T10:00:00+09:00"
    assert date_only == datetime(2026, 5, 28, tzinfo=UTC)
    assert natural_language is None


def test_list_due_tasks_filters_future_unparsed_done_and_cancelled() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        due_id = store.add_task("期限到来", "open_loop")
        future_id = store.add_task("期限前", "open_loop")
        unparsed_id = store.add_task("自然文", "open_loop")
        done_id = store.add_task("完了済み", "open_loop")
        cancelled_id = store.add_task("キャンセル済み", "open_loop")
        assert None not in (due_id, future_id, unparsed_id, done_id, cancelled_id)

        store.snooze_task(int(due_id), "2026-05-28T11:59:00+00:00")
        store.snooze_task(int(future_id), "2026-05-28T12:01:00+00:00")
        store.snooze_task(int(unparsed_id), "tomorrow morning")
        store.mark_task_done(int(done_id))
        store._update_task_status(int(cancelled_id), "cancelled")

        due_tasks = store.list_due_tasks(now)
        proactive_titles = store.list_open_task_titles_for_proactive(now, limit=10)

        assert [task.title for task in due_tasks] == ["期限到来"]
        assert "期限到来" in proactive_titles
        assert "期限前" not in proactive_titles
        assert "自然文" not in proactive_titles
        assert "完了済み" not in proactive_titles
        assert "キャンセル済み" not in proactive_titles


def test_list_due_tasks_accepts_naive_now() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        task_id = store.add_task("期限到来", "open_loop")
        assert task_id is not None
        store.snooze_task(task_id, "2026-05-28T11:59:00+00:00")

        due_tasks = store.list_due_tasks(datetime(2026, 5, 28, 12, 0), limit=10)

        assert [task.title for task in due_tasks] == ["期限到来"]


def test_snooze_task_does_not_reopen_done_or_cancelled_tasks() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        done_id = store.add_task("完了済み", "open_loop")
        cancelled_id = store.add_task("キャンセル済み", "open_loop")
        assert done_id is not None
        assert cancelled_id is not None
        store.mark_task_done(done_id)
        store._update_task_status(cancelled_id, "cancelled")

        assert store.snooze_task(done_id, "2026-05-28T11:59:00+00:00") is False
        assert store.snooze_task(cancelled_id, "2026-05-28T11:59:00+00:00") is False
        tasks = {task.id: task for task in store.list_tasks(statuses=("done", "cancelled"), limit=10)}
        assert tasks[done_id].status == "done"
        assert tasks[cancelled_id].status == "cancelled"


def test_action_dispatcher_runs_task_actions_through_typed_requests() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        dispatcher = create_default_dispatcher(store)

        create_result = dispatcher.execute(
            ActionRequest(
                action="create_task",
                payload={"title": "契約書を確認する", "source": "test"},
                request_id="req-1",
                session_id="session-1",
            )
        )

        assert create_result.ok is True
        assert create_result.action == "create_task"
        assert create_result.request_id == "req-1"
        task_id = create_result.data["task_id"]

        snooze_result = dispatcher.execute(
            ActionRequest(
                action="snooze_task",
                payload={"task_id": task_id, "due_at": "tomorrow morning"},
                request_id="req-2",
                session_id="session-1",
            )
        )
        done_result = dispatcher.execute(
            ActionRequest(
                action="mark_task_done",
                payload={"task_id": task_id},
                request_id="req-3",
                session_id="session-1",
            )
        )

        assert snooze_result.ok is True
        assert snooze_result.permission_decision is None
        assert done_result.ok is True
        task = store.list_tasks(statuses=("done",))[0]
        assert task.id == task_id
        assert task.status == "done"


def test_action_dispatcher_unknown_action_fails_safely() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        dispatcher = create_default_dispatcher(store)

        result = dispatcher.execute(ActionRequest(action="delete_everything", payload={}))

        assert result.ok is False
        assert result.error_type == "unknown_action"
        assert "Unknown action" in result.message


def test_action_dispatcher_invalid_payload_fails_safely() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        dispatcher = create_default_dispatcher(store)

        result = dispatcher.execute(ActionRequest(action="snooze_task", payload={"task_id": "1", "due_at": ""}))

        assert result.ok is False
        assert result.error_type == "invalid_payload"
        assert store.list_tasks(statuses=("snoozed",)) == []


def test_action_dispatcher_permission_hook_runs_before_action() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        dispatcher = create_default_dispatcher(store, permission_hook=lambda _request: PermissionDecision.DENY)

        result = dispatcher.execute(ActionRequest(action="create_task", payload={"title": "作成されないタスク"}))

        assert result.ok is False
        assert result.error_type == "permission_not_allowed"
        assert result.permission_decision == PermissionDecision.DENY
        assert store.list_tasks() == []


def test_action_dispatcher_ask_permission_also_stops_before_action() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        dispatcher = create_default_dispatcher(store, permission_hook=lambda _request: PermissionDecision.ASK)

        result = dispatcher.execute(ActionRequest(action="create_task", payload={"title": "確認待ちタスク"}))

        assert result.ok is False
        assert result.error_type == "permission_not_allowed"
        assert result.permission_decision == PermissionDecision.ASK
        assert store.list_tasks() == []


def test_completed_or_snoozed_tasks_do_not_fall_back_to_summary_open_loops() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
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
        store.snooze_task(snoozed_id, "tomorrow morning")

        assert store.latest_open_loops() == []

