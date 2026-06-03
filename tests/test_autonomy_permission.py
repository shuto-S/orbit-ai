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

def test_action_dispatcher_can_use_permission_policy_config() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        autonomy = parse_autonomy_config(
            {
                "autonomy": {
                    "level": "ask_then_act",
                    "allow_local_actions": True,
                    "require_permission_for": ["create_task"],
                }
            }
        )
        dispatcher = create_default_dispatcher(store, autonomy=autonomy)

        result = dispatcher.execute(ActionRequest(action="create_task", payload={"title": "許可されたタスク"}))

        assert result.ok is True
        assert result.permission_decision == PermissionDecision.ALLOW
        assert store.list_tasks()[0].title == "許可されたタスク"


def test_autonomy_default_is_suggest_only() -> None:
    config = parse_autonomy_config(None)

    assert config.enabled is True
    assert config.level == AutonomyLevel.SUGGEST_ONLY
    assert config.effective_level == AutonomyLevel.SUGGEST_ONLY
    assert config.allows_proactive_suggestions() is True
    assert config.requires_permission("create_task") is True
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


def test_autonomy_assistive_level_is_valid_and_allows_suggestions() -> None:
    config = parse_autonomy_config({"autonomy": {"level": "assistive", "allow_local_actions": True}})

    assert config.level == AutonomyLevel.ASSISTIVE
    assert config.effective_level == AutonomyLevel.ASSISTIVE
    assert config.allows_proactive_suggestions() is True
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


def test_autonomy_explicit_empty_permission_actions_disables_local_actions() -> None:
    config = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": [],
            }
        }
    )

    assert config.require_permission_for == ()
    assert config.can_run_after_permission("create_task") is False
    assert config.requires_permission("create_task") is False


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


def test_permission_policy_assistive_matrix() -> None:
    off = parse_autonomy_config({"autonomy": {"level": "off", "allow_local_actions": True}})
    suggest_only = parse_autonomy_config({"autonomy": {"level": "suggest_only", "allow_local_actions": True}})
    assistive = parse_autonomy_config({"autonomy": {"level": "assistive", "allow_local_actions": True}})

    assert evaluate_permission("create_task", off, user_explicit=True) == PermissionDecision.DENY
    assert evaluate_permission("create_task", suggest_only, user_explicit=True) == PermissionDecision.ASK
    assert evaluate_permission("create_task", assistive, user_explicit=True) == PermissionDecision.ALLOW
    assert evaluate_permission("create_task", assistive, user_explicit=False) == PermissionDecision.ASK
    assert evaluate_permission("write_memory", assistive, user_explicit=True) == PermissionDecision.ALLOW
    assert evaluate_permission("run_local_check", assistive, user_explicit=True) == PermissionDecision.DENY
    assert evaluate_permission("unknown_action", assistive, user_explicit=True) == PermissionDecision.DENY
    assert (
        evaluate_permission("create_task", assistive, risk_level="high", user_explicit=True)
        == PermissionDecision.ASK
    )


def test_permission_policy_assistive_requires_local_action_opt_in() -> None:
    autonomy = parse_autonomy_config({"autonomy": {"level": "assistive", "allow_local_actions": False}})

    assert evaluate_permission("create_task", autonomy, user_explicit=True) == PermissionDecision.ASK


def test_action_dispatcher_passes_explicit_user_request_to_permission_policy() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "test.sqlite3")
        autonomy = parse_autonomy_config({"autonomy": {"level": "assistive", "allow_local_actions": True}})
        dispatcher = create_default_dispatcher(store, autonomy=autonomy)

        inferred = dispatcher.execute(ActionRequest(action="create_task", payload={"title": "推測タスク"}))
        explicit = dispatcher.execute(
            ActionRequest(action="create_task", payload={"title": "明示タスク"}, user_explicit=True)
        )

        assert inferred.ok is False
        assert inferred.permission_decision == PermissionDecision.ASK
        assert explicit.ok is True
        assert explicit.permission_decision == PermissionDecision.ALLOW
        assert [task.title for task in store.list_tasks()] == ["明示タスク"]


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


def test_permission_policy_empty_action_policy_defaults_to_ask() -> None:
    policy = PermissionPolicyConfig(actions={"write_memory": ActionPermissionPolicy()})
    autonomy = parse_autonomy_config(
        {
            "autonomy": {
                "level": "ask_then_act",
                "allow_local_actions": True,
                "require_permission_for": ["write_memory"],
            }
        }
    )

    assert evaluate_permission("write_memory", autonomy, policy=policy) == PermissionDecision.ASK


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
