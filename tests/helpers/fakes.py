from __future__ import annotations

from typing import Any

from app.ai.app_server_backend import BackendResponse, CodexAppServerError
from app.memory.store import MemoryStore


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
