from collections.abc import Iterator
from typing import Any

from app.ai.app_server_backend import AppServerCodexBackend
from app.ai.app_server_rpc import CodexAppServerError
from app.ai.backends.base import LlmBackend, LlmBackendError
from app.ai.prompt_builder import PromptBuilder
from app.latency import LatencyLogger
from app.memory.store import Memory, MemoryStore, Message

LLM_ERROR_PREFIX = "LLM backendで処理できませんでした。"
CODEX_ERROR_PREFIX = "Codex app-serverで処理できませんでした。"


class ResponseAgent:
    def __init__(
        self,
        backend: LlmBackend | None = None,
        prompt_builder: PromptBuilder | None = None,
        model: str | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.backend = backend or AppServerCodexBackend(model=model, latency=latency)
        self.prompt_builder = prompt_builder or PromptBuilder()

    def respond(
        self,
        profile: dict[str, Any],
        memories: list[Memory],
        session_state: str,
        recent_messages: list[Message],
        user_text: str,
        session_id: str,
        store: MemoryStore,
    ) -> str:
        prompt = self.prompt_builder.build_response_prompt(
            profile=profile,
            memories=memories,
            session_state=session_state,
            recent_messages=recent_messages,
            user_text=user_text,
        )
        try:
            response = self.backend.ask(prompt, thread_id=store.get_codex_thread_id(session_id))
        except LlmBackendError as exc:
            return f"{backend_error_prefix(exc)}理由: {exc}"
        store.set_codex_thread_id(session_id, response.thread_id)
        return response.text

    def respond_stream(
        self,
        profile: dict[str, Any],
        memories: list[Memory],
        session_state: str,
        recent_messages: list[Message],
        user_text: str,
        session_id: str,
        store: MemoryStore,
    ) -> Iterator[str]:
        prompt = self.prompt_builder.build_response_prompt(
            profile=profile,
            memories=memories,
            session_state=session_state,
            recent_messages=recent_messages,
            user_text=user_text,
        )
        try:
            for event in self.backend.ask_stream(prompt, thread_id=store.get_codex_thread_id(session_id)):
                store.set_codex_thread_id(session_id, event.thread_id)
                if event.kind == "delta":
                    yield event.text
        except LlmBackendError as exc:
            yield f"{backend_error_prefix(exc)}理由: {exc}"


def backend_error_prefix(error: LlmBackendError) -> str:
    if isinstance(error, CodexAppServerError):
        return CODEX_ERROR_PREFIX
    return LLM_ERROR_PREFIX
