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
                "memories": self._format_memories(memories),
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
    def _format_memories(memories: list[Memory]) -> str:
        if not memories:
            return "なし"
        return "\n".join(f"- [{memory.kind}] {memory.content}" for memory in memories)
