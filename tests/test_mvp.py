import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import numpy as np
import pytest

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
from app.config.permission_policy import PermissionDecision, evaluate_permission, parse_permission_policy_config
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import DEFAULT_LATENCY_LOG_PATH, LatencyLogger
from app.main import (
    DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS,
    handle_task_command,
    maybe_start_proactive_permission,
    proactive_check_interval_seconds,
    read_text_with_idle_ticks,
    show_tasks,
)
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text
from scripts.latency_summary import percentile, read_events
from scripts.stt_faster_whisper import RecordingState


class FakeResponseAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def respond(
        self,
        profile: dict[str, Any],
        memories: list[Any],
        session_state: str,
        recent_messages: list[Any],
        user_text: str,
        session_id: str,
        store: MemoryStore,
    ) -> str:
        self.calls.append(user_text)
        if "MVP" in user_text:
            return "MVPはテキストの会話セッション管理から固めるのがよさそうです。"
        return "受け取りました。次に決めたいことを1つ教えてください。"


class FakeBackend:
    def __init__(self, response_text: str = "Codexからの応答", thread_id: str = "thread-1") -> None:
        self.response_text = response_text
        self.thread_id = thread_id
        self.calls: list[tuple[str, str | None]] = []

    def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
        self.calls.append((prompt, thread_id))
        return BackendResponse(self.response_text, self.thread_id)

    def ask_stream(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> Any:
        self.calls.append((prompt, thread_id))
        yield from ()


class ErrorBackend:
    def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
        raise CodexAppServerError("test failure")


class FakeTranscriber:
    def record_and_transcribe(self) -> str:
        return "オービット、予定を確認して"


class FakeRpcClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any], int]] = []
        self.responses: list[tuple[int, dict[str, Any]]] = []
        self.messages = [
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "hello"},
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": " world"},
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "turn": {"id": "turn-1"}},
            },
        ]

    def request(self, method: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
        self.requests.append((method, params, timeout))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        raise AssertionError(method)

    def read_message(self, timeout: int) -> dict[str, Any]:
        return self.messages.pop(0)

    def respond(self, request_id: int, result: dict[str, Any]) -> None:
        self.responses.append((request_id, result))


@pytest.fixture
def mvp_context() -> tuple[MemoryStore, SessionManager]:
    with tempfile.TemporaryDirectory() as tempdir:
        db_path = Path(tempdir) / "test.sqlite3"
        store = MemoryStore(db_path)
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        )
        yield store, manager


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


def test_proactive_policy_uses_open_tasks(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()

    assert decision.allowed
    assert "請求書の確認" in decision.candidate.permission_text


def test_proactive_check_interval_config_defaults_and_clamps() -> None:
    assert proactive_check_interval_seconds({}) == DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    assert (
        proactive_check_interval_seconds({"check_interval_seconds": "bad"})
        == DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    )
    assert proactive_check_interval_seconds({"check_interval_seconds": 0}) == 1
    assert proactive_check_interval_seconds({"check_interval_seconds": "5"}) == 5


def test_autonomy_default_is_suggest_only() -> None:
    config = parse_autonomy_config(None)

    assert config.enabled is True
    assert config.level == AutonomyLevel.SUGGEST_ONLY
    assert config.effective_level == AutonomyLevel.SUGGEST_ONLY
    assert config.allows_proactive_suggestions() is True
    assert config.can_run_after_permission("create_task") is False


def test_autonomy_disabled_is_effectively_off() -> None:
    config = parse_autonomy_config({"autonomy": {"enabled": False, "level": "ask_then_act"}})

    assert config.enabled is False
    assert config.level == AutonomyLevel.ASK_THEN_ACT
    assert config.effective_level == AutonomyLevel.OFF
    assert config.allows_proactive_suggestions() is False


def test_autonomy_unknown_level_falls_back_to_safe_default() -> None:
    config = parse_autonomy_config({"autonomy": {"level": "run_everything", "allow_local_actions": True}})

    assert config.level == AutonomyLevel.SUGGEST_ONLY
    assert config.effective_level == AutonomyLevel.SUGGEST_ONLY
    assert config.can_run_after_permission("create_task") is False


def test_load_autonomy_config_reads_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "autonomy.json").write_text(
        json.dumps({"autonomy": {"level": "off"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.config.loader.CONFIG_DIR", tmp_path)

    config = load_autonomy_config()

    assert config.effective_level == AutonomyLevel.OFF


def test_load_autonomy_config_invalid_file_falls_back_to_safe_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "autonomy.json").write_text("[", encoding="utf-8")
    monkeypatch.setattr("app.config.loader.CONFIG_DIR", tmp_path)

    config = load_autonomy_config()

    assert config.effective_level == AutonomyLevel.SUGGEST_ONLY


def test_autonomy_ask_then_act_requires_permission_and_local_action_opt_in() -> None:
    config = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["create_task"],
            }
        }
    )

    assert config.can_run_after_permission("create_task") is True
    assert config.can_run_after_permission("write_memory") is False
    assert config.requires_permission("create_task") is True


def test_permission_policy_allows_known_normal_action_for_ask_then_act() -> None:
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["create_task"],
            }
        }
    )

    decision = evaluate_permission("create_task", autonomy)

    assert decision == PermissionDecision.ALLOW


def test_permission_policy_suggest_only_does_not_auto_allow_execution() -> None:
    autonomy = parse_autonomy_config({"autonomy": {"level": "suggest_only", "allow_local_actions": True}})

    decision = evaluate_permission("create_task", autonomy)

    assert decision == PermissionDecision.ASK


def test_permission_policy_off_and_unknown_action_are_safe() -> None:
    autonomy = parse_autonomy_config(
        {"autonomy": {"level": "off", "allow_local_actions": True, "require_permission_for": ["create_task"]}}
    )

    assert evaluate_permission("create_task", autonomy) == PermissionDecision.DENY
    assert evaluate_permission("delete_everything", autonomy) == PermissionDecision.DENY


def test_permission_policy_ask_then_act_high_risk_still_asks() -> None:
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["write_memory"],
            }
        }
    )

    decision = evaluate_permission("write_memory", autonomy, risk_level="high")

    assert decision == PermissionDecision.ASK


def test_permission_policy_requires_local_action_opt_in_before_allowing() -> None:
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": False,
                "require_permission_for": ["create_task"],
            }
        }
    )

    decision = evaluate_permission("create_task", autonomy)

    assert decision == PermissionDecision.ASK


def test_permission_policy_default_rules_are_safe() -> None:
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["create_task", "snooze_task", "run_local_check"],
            }
        }
    )

    assert evaluate_permission("create_task", autonomy) == PermissionDecision.ALLOW
    assert evaluate_permission("snooze_task", autonomy) == PermissionDecision.ASK
    assert evaluate_permission("run_local_check", autonomy) == PermissionDecision.DENY


def test_permission_policy_deny_rule_is_not_upgraded_to_ask() -> None:
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": False,
                "require_permission_for": ["run_local_check"],
            }
        }
    )

    assert evaluate_permission("run_local_check", autonomy) == PermissionDecision.DENY


def test_permission_policy_rules_config_is_reflected() -> None:
    policy = parse_permission_policy_config(
        {
            "permission_policy": {
                "default": "ask",
                "rules": {
                    "snooze_task": "allow",
                    "run_local_check": "deny",
                },
            }
        }
    )
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["snooze_task", "run_local_check"],
            }
        }
    )

    assert evaluate_permission("snooze_task", autonomy, policy=policy) == PermissionDecision.ALLOW
    assert evaluate_permission("run_local_check", autonomy, policy=policy) == PermissionDecision.DENY


def test_permission_policy_default_applies_to_unspecified_actions() -> None:
    policy = parse_permission_policy_config(
        {
            "permission_policy": {
                "default": "deny",
                "rules": {},
            }
        }
    )
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["create_task"],
            }
        }
    )

    assert evaluate_permission("create_task", autonomy, policy=policy) == PermissionDecision.DENY


def test_permission_policy_invalid_or_unsafe_values_are_capped() -> None:
    policy = parse_permission_policy_config(
        {
            "permission_policy": {
                "unknown_action": "allow",
                "actions": {
                    "create_task": {
                        "normal": "allow",
                        "high": "allow",
                    }
                },
            }
        }
    )
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["create_task"],
            }
        }
    )

    assert (
        evaluate_permission("create_task", autonomy, risk_level="unexpected", policy=policy)
        == PermissionDecision.ASK
    )
    assert evaluate_permission("unknown_action", autonomy, policy=policy) == PermissionDecision.DENY


def test_permission_policy_off_denies_unknown_action_before_policy_default() -> None:
    policy = parse_permission_policy_config({"permission_policy": {"unknown_action": "ask"}})
    autonomy = parse_autonomy_config({"autonomy": {"level": "off"}})

    assert evaluate_permission("unexpected_action", autonomy, policy=policy) == PermissionDecision.DENY


def test_load_permission_policy_config_invalid_file_falls_back_to_safe_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "permission_policy.json").write_text("[", encoding="utf-8")
    monkeypatch.setattr("app.config.loader.CONFIG_DIR", tmp_path)
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["mark_task_done"],
            }
        }
    )

    policy = load_permission_policy_config()

    assert evaluate_permission("mark_task_done", autonomy, policy=policy) == PermissionDecision.ASK
    assert evaluate_permission("unexpected_action", autonomy, policy=policy) == PermissionDecision.DENY


def test_autonomy_off_disables_proactive_suggestions(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, _ = mvp_context
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        autonomy_config=parse_autonomy_config({"autonomy": {"level": "off"}}),
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
    )
    store.add_task("請求書の確認", "open_loop", source_session_id="previous")
    manager.idle_since = datetime.now(UTC) - timedelta(seconds=181)

    decision = manager.check_proactive()

    assert not decision.allowed
    assert decision.reason == "autonomy off"


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

    monkeypatch.setattr("app.main.sys.stdin", fake_stdin)
    monkeypatch.setattr("app.main.select.select", fake_select)

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


def test_app_server_backend_builds_requests_and_collects_deltas() -> None:
    rpc_client = FakeRpcClient()
    backend = AppServerCodexBackend(rpc_client=rpc_client)

    response = backend.ask("hello", timeout=1)

    assert response == BackendResponse(text="hello world", thread_id="thread-1")
    assert rpc_client.requests[0][0] == "thread/start"
    assert "model" not in rpc_client.requests[0][1]
    assert rpc_client.requests[0][1]["sandbox"] == "read-only"
    assert rpc_client.requests[0][1]["approvalPolicy"] == "never"
    assert rpc_client.requests[0][1]["ephemeral"] is False
    assert rpc_client.requests[0][1]["cwd"] is None
    assert rpc_client.requests[0][1]["runtimeWorkspaceRoots"] == []
    assert rpc_client.requests[0][1]["environments"] == []
    assert rpc_client.requests[1][0] == "turn/start"
    assert rpc_client.requests[1][1]["threadId"] == "thread-1"
    assert "model" not in rpc_client.requests[1][1]


def test_app_server_backend_streams_deltas_in_order() -> None:
    rpc_client = FakeRpcClient()
    backend = AppServerCodexBackend(rpc_client=rpc_client)

    events = list(backend.ask_stream("hello", timeout=1))

    assert [event.kind for event in events] == ["delta", "delta", "completed"]
    assert [event.text for event in events[:2]] == ["hello", " world"]
    assert events[-1].text == "hello world"


def test_app_server_backend_resumes_existing_thread() -> None:
    rpc_client = FakeRpcClient()
    rpc_client.messages = [
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-existing", "turnId": "turn-1", "delta": "resumed"},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-existing", "turnId": "turn-1", "turn": {"id": "turn-1"}},
        },
    ]
    backend = AppServerCodexBackend(rpc_client=rpc_client)

    response = backend.ask("hello", thread_id="thread-existing", timeout=1)

    assert response.thread_id == "thread-existing"
    assert rpc_client.requests[0][0] == "thread/resume"
    assert rpc_client.requests[0][1]["threadId"] == "thread-existing"
    assert rpc_client.requests[0][1]["cwd"] is None
    assert rpc_client.requests[0][1]["runtimeWorkspaceRoots"] == []
    assert rpc_client.requests[0][1]["environments"] == []
    assert "model" not in rpc_client.requests[0][1]
    assert rpc_client.requests[1][0] == "turn/start"
    assert rpc_client.requests[1][1]["threadId"] == "thread-existing"


def test_app_server_backend_declines_server_requests_without_hanging() -> None:
    rpc_client = FakeRpcClient()
    rpc_client.messages = [
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "mcpServer/elicitation/request",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "done"},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "turn": {"id": "turn-1"}},
        },
    ]
    backend = AppServerCodexBackend(model="gpt-5-nano", rpc_client=rpc_client)

    response = backend.ask("hello", timeout=1)

    assert response.text == "done"
    assert rpc_client.responses == [(99, {"action": "decline", "content": None})]


def test_app_server_backend_uses_completed_agent_message_when_no_delta() -> None:
    rpc_client = FakeRpcClient()
    rpc_client.messages = [
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "id": "item-1", "text": "final text"},
            },
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "turn": {"id": "turn-1"}},
        },
    ]
    backend = AppServerCodexBackend(model="gpt-5-nano", rpc_client=rpc_client)

    response = backend.ask("hello", timeout=1)

    assert response.text == "final text"


def test_app_server_backend_finishes_on_idle_when_turn_completed_is_missing() -> None:
    rpc_client = FakeRpcClient()
    rpc_client.messages = [
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "pong"},
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "id": "item-1", "text": "pong", "phase": "final_answer"},
            },
        },
        {
            "method": "thread/status/changed",
            "params": {"threadId": "thread-1", "status": {"type": "idle"}},
        },
    ]
    backend = AppServerCodexBackend(rpc_client=rpc_client)

    response = backend.ask("hello", timeout=1)

    assert response.text == "pong"


def test_app_server_backend_can_opt_into_project_binding() -> None:
    rpc_client = FakeRpcClient()
    backend = AppServerCodexBackend(model="gpt-5-nano", cwd=Path("/tmp/work"), rpc_client=rpc_client)

    params = backend.build_thread_start_params()

    assert params["cwd"] == "/tmp/work"
    assert params["runtimeWorkspaceRoots"] == []
    assert params["environments"] == []
    assert params["model"] == "gpt-5-nano"


def test_response_agent_saves_and_reuses_codex_thread(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, _ = mvp_context
    backend = FakeBackend()
    agent = ResponseAgent(backend=backend)  # type: ignore[arg-type]

    first = agent.respond({}, [], "THINKING", [], "hello", session_id="local-1", store=store)
    second = agent.respond({}, [], "THINKING", [], "again", session_id="local-1", store=store)

    assert first == "Codexからの応答"
    assert second == "Codexからの応答"
    assert store.get_codex_thread_id("local-1") == "thread-1"
    assert backend.calls[0][1] is None
    assert backend.calls[1][1] == "thread-1"


def test_response_agent_returns_error_without_fallback(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, _ = mvp_context
    agent = ResponseAgent(backend=ErrorBackend())  # type: ignore[arg-type]

    text = agent.respond({}, [], "THINKING", [], "hello", session_id="local-1", store=store)

    assert text.startswith(CODEX_ERROR_PREFIX)
    assert "test failure" in text


def test_surrogate_input_is_sanitized_before_sqlite_write(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    store, manager = mvp_context

    manager.handle_input("オービットさん。こんにちは")
    output = manager.handle_input("メール\udce3ボックスで受け取ってるメールを要約して")

    assert output.text is not None
    saved = store.get_session_messages(manager.session_id_or_raise())
    assert any("メール�ボックス" in message.content for message in saved)


def test_sanitize_text_replaces_invalid_surrogates() -> None:
    assert sanitize_text("abc\udce3def") == "abc�def"


def test_voice_io_extracts_last_non_empty_transcript_line() -> None:
    stdout = "Listening... speak now.\n\nオービットおはよう\n"

    assert VoiceIO._extract_transcript(stdout) == "オービットおはよう"


def test_voice_input_empty_transcript_does_not_fallback_to_text_input(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=args, returncode=0, stdout="\n", stderr=""),
    )

    assert voice.read_text() == ""


def test_voice_input_can_use_inprocess_transcriber() -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_backend="faster_whisper_inprocess", output_enabled=False)
    voice = VoiceIO(config, transcriber=FakeTranscriber())  # type: ignore[arg-type]

    assert voice.read_text() == "オービット、予定を確認して"


def test_voice_config_reads_latency_related_voice_settings() -> None:
    config = VoiceConfig.from_profile(load_profile())

    assert config.blocking_playback is True
    assert config.input_backend == "command"
    assert config.stt_config.min_seconds == 0.5
    assert config.stt_config.silence_seconds == 0.45


def test_voice_stop_speaking_without_process_is_noop() -> None:
    voice = VoiceIO(VoiceConfig.from_profile(load_profile()))

    voice.stop_speaking()


def test_recording_state_keeps_only_pre_roll_before_speech() -> None:
    state = RecordingState(pre_roll_blocks=2)
    silence = np.zeros((2, 1), dtype=np.float32)
    speech = np.ones((2, 1), dtype=np.float32)

    state.add_chunk(silence, silence_threshold=0.5)
    state.add_chunk(silence, silence_threshold=0.5)
    state.add_chunk(speech, silence_threshold=0.5)

    chunks = list(state.recorded_chunks())
    assert len(chunks) == 2
    assert chunks[0] is silence
    assert chunks[-1] is speech


def test_sentence_chunker_flushes_sentence_and_keeps_short_prefix() -> None:
    chunker = SentenceChunker(min_chars=5, max_chars=20)

    assert chunker.add("短い") == []
    assert chunker.add("文章です。次") == ["短い文章です。"]
    assert chunker.flush() == "次"


def test_latency_logger_disabled_does_not_write_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    logger = LatencyLogger(False)

    logger.event("voice.read_text.start")

    assert capsys.readouterr().err == ""


def test_latency_logger_writes_jsonl_with_turn_context(tmp_path: Path) -> None:
    log_path = tmp_path / "latency.jsonl"
    logger = LatencyLogger(enabled=True, log_path=log_path)

    logger.start_turn(session_id="session-1")
    logger.event("voice.read_text.start", source="test")

    event = json.loads(log_path.read_text(encoding="utf-8"))
    assert event["event"] == "voice.read_text.start"
    assert event["session_id"] == "session-1"
    assert isinstance(event["turn_id"], str)
    assert isinstance(event["elapsed_ms"], int | float)
    assert event["source"] == "test"


def test_latency_logger_from_profile_reads_log_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORBIT_AI_LATENCY_LOG", raising=False)
    monkeypatch.delenv("ORBIT_AI_LATENCY_LOG_PATH", raising=False)

    logger = LatencyLogger.from_profile({"latency": {"enabled": True, "log_path": "data/latency.jsonl"}})

    assert logger.enabled is True
    assert logger.log_path == Path("data/latency.jsonl")


def test_latency_logger_env_enabled_uses_default_jsonl_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORBIT_AI_LATENCY_LOG", "1")
    monkeypatch.delenv("ORBIT_AI_LATENCY_LOG_PATH", raising=False)

    logger = LatencyLogger.from_profile({"latency": {"enabled": False}})

    assert logger.enabled is True
    assert logger.log_path == DEFAULT_LATENCY_LOG_PATH


def test_latency_logger_env_log_path_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORBIT_AI_LATENCY_LOG", "1")
    monkeypatch.setenv("ORBIT_AI_LATENCY_LOG_PATH", "env-latency.jsonl")

    logger = LatencyLogger.from_profile({"latency": {"enabled": True, "log_path": "profile-latency.jsonl"}})

    assert logger.enabled is True
    assert logger.log_path == Path("env-latency.jsonl")


def test_latency_logger_span_writes_duration_ms(tmp_path: Path) -> None:
    logger = LatencyLogger(enabled=True, log_path=tmp_path / "latency.jsonl")

    with logger.span("voice.synthesis", session_id="session-1"):
        pass

    events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
    end_event = events[-1]
    assert end_event["event"] == "voice.synthesis.end"
    assert end_event["session_id"] == "session-1"
    assert isinstance(end_event["duration_ms"], int | float)


def test_latency_logger_calculates_duration_for_start_end_events(tmp_path: Path) -> None:
    logger = LatencyLogger(enabled=True, log_path=tmp_path / "latency.jsonl")

    logger.event("voice.synthesis.start")
    logger.event("voice.synthesis.end")

    events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
    end_event = events[-1]
    assert end_event["event"] == "voice.synthesis.end"
    assert isinstance(end_event["duration_ms"], int | float)


def test_latency_logger_warns_once_on_jsonl_write_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = LatencyLogger(enabled=True, log_path=tmp_path)

    logger.event("voice.read_text.start")
    logger.event("voice.read_text.end")

    stderr = capsys.readouterr().err
    assert stderr.count("latency jsonl write failed") == 1


def test_session_manager_binds_latency_session_id(tmp_path: Path) -> None:
    logger = LatencyLogger(enabled=True, log_path=tmp_path / "latency.jsonl")
    store = MemoryStore(tmp_path / "test.sqlite3")
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        latency=logger,
    )

    logger.start_turn()
    output = manager.handle_input("オービット、相談したい")
    logger.event("manager.handle_input.end")

    event = json.loads(logger.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert output.session_id is not None
    assert event["session_id"] == output.session_id


def test_latency_session_id_is_cleared_after_session_close(tmp_path: Path) -> None:
    logger = LatencyLogger(enabled=True, log_path=tmp_path / "latency.jsonl")
    store = MemoryStore(tmp_path / "test.sqlite3")
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        latency=logger,
    )

    logger.start_turn(session_id=manager.session_id)
    opened = manager.handle_input("オービット、相談したい")
    logger.bind_session(opened.session_id)
    logger.event("manager.handle_input.end")

    manager.handle_input("ありがとう")
    closed = manager.handle_input("うん")
    logger.bind_session(closed.session_id)
    logger.start_turn(session_id=manager.session_id)
    logger.event("voice.read_text.start")

    event = json.loads(logger.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert closed.session_id is None
    assert event["session_id"] is None


def test_latency_summary_reads_events_and_uses_linear_percentile(tmp_path: Path) -> None:
    log_path = tmp_path / "latency.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"event": "voice.read_text.end", "elapsed_ms": 10}),
                json.dumps({"event": "voice.read_text.end", "elapsed_ms": 20}),
                json.dumps({"event": "voice.read_text.end", "elapsed_ms": 30}),
                json.dumps({"event": "voice.read_text.end", "duration_ms": 40}),
            ]
        ),
        encoding="utf-8",
    )

    events = read_events(log_path, "elapsed_ms")

    assert events["voice.read_text.end"] == [10.0, 20.0, 30.0]
    assert percentile(events["voice.read_text.end"], 0.90) == pytest.approx(28.0)
