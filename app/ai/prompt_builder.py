import json
from string import Template
from typing import Any

from app.memory.store import Memory, Message
from app.paths import PROMPTS_DIR


class PromptBuilder:
    def build_response_prompt(
        self,
        profile: dict[str, Any],
        memories: list[Memory],
        session_state: str,
        recent_messages: list[Message],
        user_text: str,
    ) -> str:
        return self._render(
            "response.md",
            {
                "profile": json.dumps(profile, ensure_ascii=False, indent=2),
                "memories": self._format_memories(memories, self._memory_budget(profile)),
                "session_state": session_state,
                "recent_messages": self._format_messages(recent_messages),
                "user_text": user_text,
            },
        )

    def _render(self, name: str, values: dict[str, str]) -> str:
        text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        # Templates use {{key}} to stay readable in markdown.
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return Template(text).safe_substitute(values)

    @staticmethod
    def _format_messages(messages: list[Message]) -> str:
        if not messages:
            return "なし"
        return "\n".join(f"{message.role}: {message.content}" for message in messages)

    @staticmethod
    def _format_memories(memories: list[Memory], max_chars: int = 1200) -> str:
        if not memories:
            return "なし"
        lines = [
            f"- #{memory.id} [{memory.kind} confidence={memory.confidence:.2f}] {memory.content}"
            for memory in memories
            if memory.status == "active"
        ]
        text = "\n".join(lines) if lines else "なし"
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 20)].rstrip() + "\n- ... truncated"

    @staticmethod
    def _memory_budget(profile: dict[str, Any]) -> int:
        memory = profile.get("memory", {})
        retrieval = memory.get("retrieval", {}) if isinstance(memory, dict) else {}
        if not isinstance(retrieval, dict):
            return 1200
        try:
            return max(200, int(retrieval.get("max_prompt_chars", 1200)))
        except (TypeError, ValueError):
            return 1200
