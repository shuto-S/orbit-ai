from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.backends.base import BackendResponse
from app.ai.prompt_builder import PromptBuilder
from app.cli.commands import handle_memory_command
from app.cli.display import show_tasks
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
    tables = {row["name"] for row in store.connect().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "open_loops" in tables
    assert store.list_memories()[0].content == "古いDBの記憶"
    assert store.add_memory("manual", "新しい記憶") is not None


def test_task_schema_migrates_legacy_table_without_description(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "legacy_tasks.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              priority REAL DEFAULT 0.5,
              due_at TEXT,
              source TEXT,
              source_session_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (title, status, created_at, updated_at)
            VALUES ('古いDBのタスク', 'open', '2026-06-04T00:00:00+00:00', '2026-06-04T00:00:00+00:00')
            """
        )

    store = MemoryStore(db_path)

    columns = {row["name"] for row in store.connect().execute("PRAGMA table_info(tasks)").fetchall()}
    assert "description" in columns
    tasks = store.list_tasks(statuses=("open",), limit=20)
    assert [task.title for task in tasks] == ["古いDBのタスク"]
    assert tasks[0].description is None
    assert store.add_task("新しいタスク", "manual", description="説明") is not None

    show_tasks(store)

    output = capsys.readouterr().out
    assert "古いDBのタスク" in output
    assert "新しいタスク" in output


def test_open_loop_store_roundtrip_status_and_touch(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")

    loop_id = store.add_open_loop(
        title="Orbit AIの起動後自立動作設計を詰める",
        summary="起動後に何を自律的に再開するか未確定",
        source_session_id="session-1",
        source_message_id=42,
        suggested_next_step="StartupBriefingServiceから実装する",
        importance=2.0,
        confidence=-1.0,
        metadata={"source": "test"},
    )

    assert loop_id is not None
    assert store.add_open_loop("Orbit AIの起動後自立動作設計を詰める") is None
    loop = store.get_open_loop(loop_id)
    assert loop is not None
    assert loop.title == "Orbit AIの起動後自立動作設計を詰める"
    assert loop.importance == 1.0
    assert loop.confidence == 0.0
    assert loop.suggested_next_step == "StartupBriefingServiceから実装する"
    assert loop.metadata == {"source": "test"}

    touched_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert store.touch_open_loop(loop_id, touched_at)
    assert store.get_open_loop(loop_id).last_discussed_at == touched_at.isoformat()

    assert [loop.id for loop in store.list_open_loops()] == [loop_id]
    assert store.resolve_open_loop(loop_id)
    assert store.list_open_loops() == []
    assert store.get_open_loop(loop_id).status == "resolved"

    archive_id = store.add_open_loop("AI秘書サービス名の候補を比較する")
    assert archive_id is not None
    assert store.archive_open_loop(archive_id)
    assert store.get_open_loop(archive_id).status == "archived"


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


def test_prompt_builder_includes_agentic_behavior_rules() -> None:
    prompt = PromptBuilder().build_response_prompt(
        profile={},
        memories=[],
        session_state="THINKING",
        recent_messages=[],
        user_text="明日までにREADMEを整えないとな",
    )

    assert "## Agentic Behavior" in prompt
    assert "likely next action" in prompt
    assert "Ask at most one question" in prompt
    assert "Never claim that a task or memory was saved unless the runtime explicitly saved it." in prompt
