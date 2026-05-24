import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from app.ai.app_server_backend import AppServerCodexBackend, BackendResponse, CodexAppServerError
from app.ai.response_agent import CODEX_ERROR_PREFIX, ResponseAgent
from app.config.loader import load_proactive_config, load_profile
from app.io.voice import VoiceConfig, VoiceIO
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text


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


class ErrorBackend:
    def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
        raise CodexAppServerError("test failure")


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
        summary="未完了論点がある",
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
