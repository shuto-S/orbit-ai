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

