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

