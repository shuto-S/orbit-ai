from __future__ import annotations

from pathlib import Path

import pytest

from app.actions import ActionRequest, create_default_dispatcher, create_store_approval_sink
from app.cli.commands import handle_approval_command
from app.config.permission_policy import PermissionDecision
from app.memory.store import MemoryStore


def test_approval_requests_create_list_approve_and_reject(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    first_id = store.add_approval_request(
        action="create_task",
        payload={"title": "READMEを整える"},
        reason="turn analysis task candidate",
        source_session_id="session-1",
    )
    second_id = store.add_approval_request(
        action="save_memory",
        payload={"content": "Orbit AIを改善中"},
        reason="turn analysis memory candidate",
    )

    pending = store.list_approval_requests()

    assert [request.id for request in pending] == [first_id, second_id]
    assert pending[0].payload == {"title": "READMEを整える"}
    assert pending[0].status == "pending"

    approved = store.approve_request(first_id)
    rejected = store.reject_request(second_id)

    assert approved is not None
    assert approved.status == "approved"
    assert rejected is not None
    assert rejected.status == "rejected"
    assert store.list_approval_requests() == []
    assert store.approve_request(999) is None


def test_approval_commands_show_approve_reject_and_invalid_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    request_id = store.add_approval_request(
        action="create_task",
        payload={"title": "READMEを整える"},
        reason="detected task candidate",
    )

    handle_approval_command(store, "/approvals")
    handle_approval_command(store, f"/approve {request_id}")
    handle_approval_command(store, "/reject not-a-number")
    handle_approval_command(store, "/reject 999")

    output = capsys.readouterr().out
    assert "Pending approvals:" in output
    assert f"#{request_id} [normal] create_task: READMEを整える" in output
    assert f"Approval #{request_id} approved." in output
    assert "approval id must be a number." in output
    assert "Approval #999 was not found." in output


def test_action_dispatcher_ask_can_enqueue_approval_request(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    dispatcher = create_default_dispatcher(
        store,
        permission_hook=lambda _request: PermissionDecision.ASK,
        approval_sink=create_store_approval_sink(store),
    )

    result = dispatcher.execute(
        ActionRequest(
            action="create_task",
            payload={"title": "確認待ちタスク"},
            request_id="req-1",
            session_id="session-1",
            source="turn_analysis",
        )
    )

    approvals = store.list_approval_requests()
    assert result.ok is False
    assert result.error_type == "approval_required"
    assert result.permission_decision == PermissionDecision.ASK
    assert result.data["approval_request_id"] == approvals[0].id
    assert approvals[0].action == "create_task"
    assert approvals[0].payload == {"title": "確認待ちタスク"}
    assert approvals[0].source_session_id == "session-1"
    assert store.list_tasks() == []


def test_action_dispatcher_ask_without_queue_keeps_existing_behavior(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    dispatcher = create_default_dispatcher(store, permission_hook=lambda _request: PermissionDecision.ASK)

    result = dispatcher.execute(ActionRequest(action="create_task", payload={"title": "確認待ちタスク"}))

    assert result.ok is False
    assert result.error_type == "permission_not_allowed"
    assert result.data == {}
    assert store.list_approval_requests() == []
