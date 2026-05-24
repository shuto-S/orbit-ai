from dataclasses import dataclass

from app.memory.store import Message


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    content: str
    priority: float = 0.5
    confidence: float = 0.8


class MemoryExtractor:
    def extract(self, messages: list[Message]) -> list[ExtractedMemory]:
        extracted: list[ExtractedMemory] = []
        for message in messages:
            if message.role != "user":
                continue
            text = message.content.strip()
            if not text:
                continue
            if any(keyword in text for keyword in ("好み", "好き", "嫌い", "短め", "詳しく", "実装寄り")):
                extracted.append(
                    ExtractedMemory("preference", f"Preference inferred from user utterance: {text}", 0.7, 0.7)
                )
            if any(keyword in text for keyword in ("MVP", "アプリ", "プロジェクト", "実装")):
                extracted.append(ExtractedMemory("project", f"Current work context: {text}", 0.6, 0.7))
            if any(keyword in text for keyword in ("あとで", "後で", "続き", "未定", "確認したい")):
                extracted.append(ExtractedMemory("open_loop", f"Open issue: {text}", 0.8, 0.75))
        return self._dedupe(extracted)

    @staticmethod
    def _dedupe(memories: list[ExtractedMemory]) -> list[ExtractedMemory]:
        seen: set[str] = set()
        result: list[ExtractedMemory] = []
        for memory in memories:
            if memory.content not in seen:
                seen.add(memory.content)
                result.append(memory)
        return result
