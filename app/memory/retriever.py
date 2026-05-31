from app.memory.store import Memory, MemoryStore


class MemoryRetriever:
    def __init__(self, store: MemoryStore, default_limit: int = 6) -> None:
        self.store = store
        self.default_limit = default_limit

    def relevant(self, user_text: str, limit: int | None = None) -> list[Memory]:
        return self.store.search_memories(user_text, limit=limit or self.default_limit)
