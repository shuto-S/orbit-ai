from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.ai.app_server_rpc import AppServerJsonRpcClient, CodexAppServerError, JsonRpcClient
from app.ai.app_server_stream import CodexStreamEvent, CodexTurnStreamer, extract_thread_id
from app.ai.backends.base import BackendResponse
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger


class AppServerCodexBackend:
    def __init__(
        self,
        model: str | None = None,
        cwd: Path | None = None,
        rpc_client: JsonRpcClient | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.model = model
        self.cwd = cwd
        self.rpc_client = rpc_client or AppServerJsonRpcClient()
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.turn_streamer = CodexTurnStreamer(self.rpc_client, self.latency)

    def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
        chunks: list[str] = []
        completed_thread_id = thread_id
        for event in self.ask_stream(prompt, thread_id=thread_id, timeout=timeout):
            completed_thread_id = event.thread_id
            if event.kind == "delta":
                chunks.append(event.text)
            elif event.kind == "completed" and not chunks:
                chunks.append(event.text)
        text = "".join(chunks).strip()
        if not text:
            raise CodexAppServerError("Codex app-server returned an empty response")
        if not completed_thread_id:
            raise CodexAppServerError("Codex app-server did not return a thread id")
        return BackendResponse(text=text, thread_id=completed_thread_id)

    def ask_stream(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> Iterator[CodexStreamEvent]:
        active_thread_id = self._resume_thread(thread_id, timeout) if thread_id else self._start_thread(timeout)
        yield from self._start_turn_stream(active_thread_id, prompt, timeout)

    def build_thread_start_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": None,
            "runtimeWorkspaceRoots": [],
            "environments": [],
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": False,
            "sessionStartSource": "startup",
            "threadSource": "user",
        }
        if self.model:
            params["model"] = self.model
        if self.cwd is not None:
            params["cwd"] = str(self.cwd)
        return params

    def build_turn_start_params(self, thread_id: str, prompt: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if self.model:
            params["model"] = self.model
        return params

    def _start_thread(self, timeout: int) -> str:
        result = self.rpc_client.request("thread/start", self.build_thread_start_params(), timeout)
        thread_id = extract_thread_id(result)
        if not thread_id:
            raise CodexAppServerError("thread/start response did not include a thread id")
        return thread_id

    def _resume_thread(self, thread_id: str | None, timeout: int) -> str:
        if not thread_id:
            return self._start_thread(timeout)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": None,
            "runtimeWorkspaceRoots": [],
            "environments": [],
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "excludeTurns": True,
        }
        if self.model:
            params["model"] = self.model
        if self.cwd is not None:
            params["cwd"] = str(self.cwd)
        result = self.rpc_client.request("thread/resume", params, timeout)
        return extract_thread_id(result) or thread_id

    def _start_turn(self, thread_id: str, prompt: str, timeout: int) -> str:
        chunks: list[str] = []
        completed_text = ""
        for event in self._start_turn_stream(thread_id, prompt, timeout):
            if event.kind == "delta":
                chunks.append(event.text)
            elif event.kind == "completed":
                completed_text = event.text
        return "".join(chunks) or completed_text

    def _start_turn_stream(self, thread_id: str, prompt: str, timeout: int) -> Iterator[CodexStreamEvent]:
        yield from self.turn_streamer.stream(thread_id, self.build_turn_start_params(thread_id, prompt), timeout)
