from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ai.backends.base import BackendResponse
from app.ai.prompt_builder import PromptBuilder
from app.cli.commands import handle_memory_command
from app.memory.extractor import MemoryExtractor
from app.memory.models import Message
from app.memory.store import MemoryStore


class FakeMemoryBackend:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def ask(self, prompt: str, thread_id: str | None = None, timeout: int | None = None) -> BackendResponse:
        self.prompts.append(prompt)
        return BackendResponse(self.text, "memory-thread")


def test_memory_search_uses_query_and_updates_usage(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    relevant_id = store.add_memory("project", "メール確認の運用を優先する", priority=0.2, confidence=0.8)
    unrelated_id = store.add_memory("preference", "回答は短めが好き", priority=1.0, confidence=1.0)

    results = store.search_memories("メール", limit=10)

    assert [memory.id for memory in results] == [relevant_id]
    assert unrelated_id not in [memory.id for memory in results]
    reloaded = store.get_memory(int(relevant_id))
    assert reloaded is not None
    assert reloaded.use_count == 1
    assert reloaded.last_used_at is not None


def test_memory_add_archives_simple_contradiction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    first_id = store.add_memory("preference", "ユーザーは短めの回答が好き", priority=0.7, confidence=0.8)
    second_id = store.add_memory("preference", "ユーザーは詳しく説明する回答が好き", priority=0.7, confidence=0.8)

    assert first_id is not None
    assert second_id is not None
    first = store.get_memory(first_id)
    second = store.get_memory(second_id)
    assert first is not None
    assert second is not None
    assert first.status == "archived"
    assert second.status == "active"


def test_memory_schema_migrates_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE memories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              content TEXT NOT NULL,
              priority REAL DEFAULT 0.5,
              confidence REAL DEFAULT 0.8,
              last_used_at TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO memories (kind, content, priority, confidence, created_at)
            VALUES ('project', '古いDBの記憶', 0.5, 0.8, '2026-05-31T00:00:00+00:00')
            """
        )

    store = MemoryStore(db_path)

    columns = {row["name"] for row in store.connect().execute("PRAGMA table_info(memories)").fetchall()}
    assert {"status", "source_session_id", "source_message_ids", "updated_at", "use_count", "sensitivity"} <= columns
    assert store.list_memories()[0].content == "古いDBの記憶"
    assert store.add_memory("manual", "新しい記憶") is not None


def test_memory_extractor_uses_llm_json_and_skips_sensitive() -> None:
    backend = FakeMemoryBackend(
        """
        {
          "memories": [
            {
              "kind": "preference",
              "content": "ユーザーは回答を短めにするのが好き",
              "confidence": 0.91,
              "priority": 0.8,
              "should_remember": true,
              "sensitivity": "normal",
              "source_message_ids": [10],
              "reason": "explicit preference"
            },
            {
              "kind": "profile",
              "content": "API key is secret",
              "confidence": 1.0,
              "priority": 1.0,
              "should_remember": true,
              "sensitivity": "sensitive",
              "source_message_ids": [11]
            }
          ]
        }
        """
    )
    extractor = MemoryExtractor(backend=backend)

    memories = extractor.extract(
        [
            Message("s", "user", "短めに答えて", "now", id=10),
            Message("s", "user", "API key is secret", "now", id=11),
        ]
    )

    assert len(memories) == 1
    assert memories[0].kind == "preference"
    assert memories[0].source_message_ids == [10]
    assert backend.prompts


def test_memory_extractor_falls_back_on_invalid_json() -> None:
    extractor = MemoryExtractor(backend=FakeMemoryBackend("not json"))

    memories = extractor.extract([Message("s", "user", "このアプリの実装を続けたい", "now", id=7)])

    assert len(memories) == 1
    assert memories[0].kind == "project"
    assert memories[0].source_message_ids == [7]


def test_memory_commands_remember_search_and_forget(tmp_path: Path, capsys) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")

    handle_memory_command(store, "/remember メール確認を優先する")
    memory_id = store.list_memories()[0].id
    handle_memory_command(store, "/memory search メール")
    handle_memory_command(store, f"/forget {memory_id}")

    output = capsys.readouterr().out
    assert f"Memory #{memory_id} saved" in output
    assert "メール確認を優先する" in output
    assert f"Memory #{memory_id} forgotten" in output
    assert store.search_memories("メール") == []


def test_prompt_builder_limits_memory_budget(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    store.add_memory("project", "A" * 2000, priority=1.0, confidence=1.0)

    prompt = PromptBuilder().build_response_prompt(
        profile={"memory": {"retrieval": {"max_prompt_chars": 240}}},
        memories=store.list_memories(),
        session_state="THINKING",
        recent_messages=[],
        user_text="hello",
    )

    assert "... truncated" in prompt
