SENTENCE_ENDINGS = ("。", "！", "？", "!", "?")


class SentenceChunker:
    def __init__(self, min_chars: int = 20, max_chars: int = 100) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.buffer = ""

    def add(self, text: str) -> list[str]:
        self.buffer += text
        chunks: list[str] = []
        while True:
            flush_index = self._find_flush_index()
            if flush_index is None:
                break
            chunk = self.buffer[:flush_index].strip()
            self.buffer = self.buffer[flush_index:].lstrip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self) -> str:
        chunk = self.buffer.strip()
        self.buffer = ""
        return chunk

    def _find_flush_index(self) -> int | None:
        if len(self.buffer) >= self.max_chars:
            return len(self.buffer)
        for index, char in enumerate(self.buffer, start=1):
            if index >= self.min_chars and char in SENTENCE_ENDINGS:
                return index
        return None
