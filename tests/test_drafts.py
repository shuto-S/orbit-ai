from __future__ import annotations

from pathlib import Path

import pytest

from app.actions import ActionRequest, create_default_dispatcher
from app.cli.commands import handle_draft_command
from app.config.autonomy import parse_autonomy_config
from app.config.permission_policy import PermissionDecision, evaluate_permission
from app.memory.store import MemoryStore


def test_drafts_create_list_get_and_archive(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    draft_id = store.add_draft(
        kind="document",
        title="README改善案",
        body="READMEに起動方法を追記する。",
        source_session_id="session-1",
        metadata={"source": "test"},
    )
    assert draft_id is not None

    drafts = store.list_drafts()
    draft = store.get_draft(draft_id)
    archived = store.archive_draft(draft_id)

    assert [item.id for item in drafts] == [draft_id]
    assert draft is not None
    assert draft.kind == "document"
    assert draft.title == "README改善案"
    assert draft.body == "READMEに起動方法を追記する。"
    assert draft.metadata == {"source": "test"}
    assert archived is not None
    assert archived.status == "archived"
    assert store.list_drafts() == []
    assert store.archive_draft(999) is None


def test_draft_commands_show_archive_and_handle_invalid_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    draft_id = store.add_draft("document", "README改善案", "READMEに起動方法を追記する。")
    assert draft_id is not None

    handle_draft_command(store, "/drafts")
    handle_draft_command(store, f"/draft show {draft_id}")
    handle_draft_command(store, f"/draft archive {draft_id}")
    handle_draft_command(store, "/draft show not-a-number")
    handle_draft_command(store, "/draft archive 999")

    output = capsys.readouterr().out
    assert "Drafts:" in output
    assert f"#{draft_id} [document/draft] README改善案" in output
    assert "Draft detail:" in output
    assert "READMEに起動方法を追記する。" in output
    assert f"Draft #{draft_id} archived." in output
    assert "draft id must be a number." in output
    assert "Draft #999 was not found." in output


def test_create_text_draft_action_creates_local_draft(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    dispatcher = create_default_dispatcher(store)

    result = dispatcher.execute(
        ActionRequest(
            action="create_text_draft",
            payload={"title": "README改善案", "body": "READMEに起動方法を追記する。", "kind": "document"},
            request_id="req-1",
            session_id="session-1",
            user_explicit=True,
        )
    )

    draft = store.get_draft(result.data["draft_id"])
    assert result.ok is True
    assert result.message.startswith("Draft created:")
    assert draft is not None
    assert draft.title == "README改善案"
    assert draft.body == "READMEに起動方法を追記する。"
    assert draft.source_session_id == "session-1"
    assert draft.metadata["request_id"] == "req-1"


def test_create_text_draft_action_rejects_invalid_payload(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    dispatcher = create_default_dispatcher(store)

    result = dispatcher.execute(ActionRequest(action="create_text_draft", payload={"title": "本文なし"}))

    assert result.ok is False
    assert result.error_type == "invalid_payload"
    assert store.list_drafts() == []


def test_permission_policy_allows_explicit_draft_action_in_assistive_mode() -> None:
    autonomy = parse_autonomy_config({"autonomy": {"level": "assistive", "allow_local_actions": True}})

    assert evaluate_permission("create_text_draft", autonomy, user_explicit=True) == PermissionDecision.ALLOW
    assert evaluate_permission("create_text_draft", autonomy, user_explicit=False) == PermissionDecision.ASK
    assert (
        evaluate_permission("create_text_draft", autonomy, risk_level="high", user_explicit=True)
        == PermissionDecision.ASK
    )
