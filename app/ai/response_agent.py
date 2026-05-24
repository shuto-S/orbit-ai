from typing import Any

from app.ai.app_server_backend import AppServerCodexBackend, CodexAppServerError
from app.ai.prompt_builder import PromptBuilder
from app.memory.store import Memory, MemoryStore, Message

CODEX_ERROR_PREFIX = "Codex app-serverで処理できませんでした。"


class ResponseAgent:
    def __init__(
        self,
        backend: AppServerCodexBackend | None = None,
        prompt_builder: PromptBuilder | None = None,
        model: str | None = None,
    ) -> None:
        self.backend = backend or AppServerCodexBackend(model=model)
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
            response = self.backend.ask(prompt, thread_id=store.get_codex_thread_id(session_id), timeout=120)
        except CodexAppServerError as exc:
            return f"{CODEX_ERROR_PREFIX}理由: {exc}"
        store.set_codex_thread_id(session_id, response.thread_id)
        return response.text
