# ruff: noqa: F401,I001
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import numpy as np
import pytest

from app.actions import ActionRequest, create_default_dispatcher
from app.ai.backend_factory import create_llm_backend
from app.ai.app_server_backend import AppServerCodexBackend, BackendResponse, CodexAppServerError
from app.ai.backends.base import LlmBackendError
from app.ai.ollama_backend import OllamaBackend
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


def test_backend_factory_defaults_to_app_server() -> None:
    backend = create_llm_backend({"assistant": {}})

    assert isinstance(backend, AppServerCodexBackend)


def test_backend_factory_creates_ollama_backend() -> None:
    backend = create_llm_backend(
        {
            "assistant": {
                "llm_backend": {
                    "type": "ollama",
                    "base_url": "http://localhost:11434",
                    "model": "llama3.2:latest",
                    "timeout_seconds": 42,
                    "options": {"temperature": 0.2},
                }
            }
        }
    )

    assert isinstance(backend, OllamaBackend)
    assert backend.model == "llama3.2:latest"
    assert backend.base_url == "http://localhost:11434"
    assert backend.timeout_seconds == 42
    assert backend.options == {"temperature": 0.2}


def test_backend_factory_rejects_missing_ollama_model() -> None:
    with pytest.raises(LlmBackendError, match="model is required"):
        create_llm_backend({"assistant": {"llm_backend": {"type": "ollama"}}})


class FakeOllamaResponse:
    def __init__(self, lines: list[bytes] | None = None, body: bytes = b"") -> None:
        self.lines = lines or []
        self.body = body
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


class FakeOllamaUrlOpen:
    def __init__(self, response: FakeOllamaResponse) -> None:
        self.response = response
        self.requests: list[tuple[object, int]] = []

    def __call__(self, request: object, timeout: int) -> FakeOllamaResponse:
        self.requests.append((request, timeout))
        return self.response


def request_json(request: Any) -> dict[str, Any]:
    data = request.data
    assert isinstance(data, bytes)
    return json.loads(data.decode("utf-8"))


def test_ollama_backend_ask_sends_chat_request_and_collects_stream() -> None:
    response = FakeOllamaResponse(
        lines=[
            b'{"message":{"content":"hello"},"done":false}\n',
            b'{"message":{"content":" world"},"done":false}\n',
            b'{"done":true}\n',
        ]
    )
    urlopen = FakeOllamaUrlOpen(response)
    backend = OllamaBackend(
        model="llama3.2:latest",
        base_url="http://127.0.0.1:11434/",
        options={"temperature": 0.2, "num_ctx": 8192},
        urlopen=urlopen,
    )

    result = backend.ask("こんにちは", timeout=7)

    assert result == BackendResponse("hello world", backend.thread_id)
    request, timeout = urlopen.requests[0]
    assert timeout == 7
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert request_json(request) == {
        "model": "llama3.2:latest",
        "messages": [{"role": "user", "content": "こんにちは"}],
        "stream": True,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    assert response.closed is True


def test_ollama_backend_streams_deltas_in_order() -> None:
    response = FakeOllamaResponse(
        lines=[
            b'{"message":{"content":"a"},"done":false}\n',
            b'{"message":{"content":"b"},"done":false}\n',
            b'{"done":true}\n',
        ]
    )
    backend = OllamaBackend(model="llama3.2:latest", urlopen=FakeOllamaUrlOpen(response))

    events = list(backend.ask_stream("prompt", thread_id="ollama:existing", timeout=1))

    assert [event.kind for event in events] == ["delta", "delta", "completed"]
    assert [event.text for event in events] == ["a", "b", "ab"]
    assert {event.thread_id for event in events} == {"ollama:existing"}


def test_ollama_backend_non_stream_response() -> None:
    response = FakeOllamaResponse(body=b'{"message":{"content":"done"}}')
    backend = OllamaBackend(model="llama3.2:latest", stream=False, urlopen=FakeOllamaUrlOpen(response))

    result = backend.ask("prompt", timeout=1)

    assert result.text == "done"


def test_ollama_backend_connection_error_is_readable() -> None:
    def failing_urlopen(*_args: object, **_kwargs: object) -> FakeOllamaResponse:
        raise urllib.error.URLError("connection refused")

    backend = OllamaBackend(model="llama3.2:latest", urlopen=failing_urlopen)

    with pytest.raises(LlmBackendError, match="ollama serve"):
        backend.ask("prompt", timeout=1)


def test_ollama_backend_http_error_suggests_pull() -> None:
    def failing_urlopen(*_args: object, **_kwargs: object) -> FakeOllamaResponse:
        raise urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/chat",
            404,
            "not found",
            {},
            BytesIO(b'{"error":"model not found"}'),
        )

    backend = OllamaBackend(model="missing-model", urlopen=failing_urlopen)

    with pytest.raises(LlmBackendError, match="ollama pull missing-model"):
        backend.ask("prompt", timeout=1)


def test_ollama_backend_invalid_json_is_readable() -> None:
    backend = OllamaBackend(model="llama3.2:latest", urlopen=FakeOllamaUrlOpen(FakeOllamaResponse([b"not-json\n"])))

    with pytest.raises(LlmBackendError, match="invalid JSON"):
        list(backend.ask_stream("prompt", timeout=1))


def test_response_agent_handles_generic_backend_errors(mvp_context: tuple[MemoryStore, SessionManager]) -> None:
    class GenericErrorBackend:
        def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
            raise LlmBackendError("generic failure")

    store, _ = mvp_context
    agent = ResponseAgent(backend=GenericErrorBackend())  # type: ignore[arg-type]

    text = agent.respond({}, [], "THINKING", [], "hello", session_id="local-1", store=store)

    assert text.startswith("LLM backendで処理できませんでした。")
    assert "generic failure" in text
