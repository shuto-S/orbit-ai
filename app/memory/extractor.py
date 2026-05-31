from __future__ import annotations

import json
from dataclasses import dataclass, field
from string import Template
from typing import Any

from app.ai.backends.base import LlmBackend, LlmBackendError
from app.memory.merger import is_sensitive_text
from app.memory.store import Message
from app.paths import PROMPTS_DIR
from app.text import sanitize_text

ALLOWED_MEMORY_KINDS = {
    "preference",
    "profile",
    "project",
    "decision",
    "open_loop",
    "relationship",
    "manual",
}


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    content: str
    priority: float = 0.5
    confidence: float = 0.8
    source_message_ids: list[int] = field(default_factory=list)
    sensitivity: str = "normal"
    expires_at: str | None = None
    reason: str = ""


class MemoryExtractor:
    def __init__(
        self,
        backend: LlmBackend | None = None,
        timeout_seconds: int = 60,
        max_messages: int = 20,
    ) -> None:
        self.backend = backend
        self.timeout_seconds = timeout_seconds
        self.max_messages = max_messages

    def extract(self, messages: list[Message]) -> list[ExtractedMemory]:
        if self.backend is not None:
            llm_result = self._extract_with_llm(messages)
            if llm_result is not None:
                return llm_result
        return self._extract_with_heuristics(messages)

    def _extract_with_llm(self, messages: list[Message]) -> list[ExtractedMemory] | None:
        prompt = self._build_prompt(messages)
        try:
            response = self.backend.ask(prompt, timeout=self.timeout_seconds)
        except LlmBackendError:
            return None
        payload = _parse_json_object(response.text)
        if payload is None:
            return None
        raw_memories = payload.get("memories")
        if not isinstance(raw_memories, list):
            return None
        extracted: list[ExtractedMemory] = []
        for item in raw_memories:
            memory = self._memory_from_payload(item)
            if memory is not None:
                extracted.append(memory)
        return self._dedupe(extracted)

    def _build_prompt(self, messages: list[Message]) -> str:
        template = (PROMPTS_DIR / "memory_extraction.md").read_text(encoding="utf-8")
        recent = messages[-self.max_messages :]
        formatted = "\n".join(
            f"[{message.id if message.id is not None else index}] {message.role}: {message.content}"
            for index, message in enumerate(recent, start=1)
        )
        return Template(template).safe_substitute({"messages": formatted or "なし"})

    def _memory_from_payload(self, item: Any) -> ExtractedMemory | None:
        if not isinstance(item, dict):
            return None
        if item.get("should_remember") is False:
            return None
        content = sanitize_text(str(item.get("content", ""))).strip()
        if not content or is_sensitive_text(content):
            return None
        sensitivity = str(item.get("sensitivity", "normal")).strip().lower()
        if sensitivity != "normal":
            return None
        kind = str(item.get("kind", "project")).strip().lower()
        if kind not in ALLOWED_MEMORY_KINDS:
            kind = "project"
        confidence = _bounded_float(item.get("confidence"), default=0.7)
        if confidence < 0.45:
            return None
        priority = _bounded_float(item.get("priority"), default=0.5)
        return ExtractedMemory(
            kind=kind,
            content=content,
            priority=priority,
            confidence=confidence,
            source_message_ids=_message_ids(item.get("source_message_ids")),
            sensitivity="normal",
            expires_at=_optional_text(item.get("expires_at")),
            reason=_optional_text(item.get("reason")) or "",
        )

    def _extract_with_heuristics(self, messages: list[Message]) -> list[ExtractedMemory]:
        extracted: list[ExtractedMemory] = []
        for message in messages:
            if message.role != "user":
                continue
            text = message.content.strip()
            if not text or is_sensitive_text(text):
                continue
            source_message_ids = [message.id] if message.id is not None else []
            if any(keyword in text for keyword in ("好み", "好き", "嫌い", "短め", "詳しく", "実装寄り")):
                extracted.append(
                    ExtractedMemory(
                        "preference",
                        f"Preference inferred from user utterance: {text}",
                        0.7,
                        0.7,
                        source_message_ids=source_message_ids,
                    )
                )
            if any(keyword in text for keyword in ("MVP", "アプリ", "プロジェクト", "実装")):
                extracted.append(
                    ExtractedMemory(
                        "project",
                        f"Current work context: {text}",
                        0.6,
                        0.7,
                        source_message_ids=source_message_ids,
                    )
                )
            if any(keyword in text for keyword in ("あとで", "後で", "続き", "未定", "確認したい")):
                extracted.append(
                    ExtractedMemory(
                        "open_loop",
                        f"Open issue: {text}",
                        0.8,
                        0.75,
                        source_message_ids=source_message_ids,
                    )
                )
        return self._dedupe(extracted)

    @staticmethod
    def _dedupe(memories: list[ExtractedMemory]) -> list[ExtractedMemory]:
        seen: set[str] = set()
        result: list[ExtractedMemory] = []
        for memory in memories:
            key = f"{memory.kind}:{memory.content}"
            if key not in seen:
                seen.add(key)
                result.append(memory)
        return result


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _message_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = sanitize_text(str(value)).strip()
    return text or None
