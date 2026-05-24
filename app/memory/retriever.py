from app.memory.store import Memory, MemoryStore


class MemoryRetriever:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def relevant(self, user_text: str, limit: int = 6) -> list[Memory]:
        return self.store.search_memories(user_text, limit=limit)
