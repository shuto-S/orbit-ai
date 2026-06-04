from __future__ import annotations

import json
from pathlib import Path

from app.ai.turn_analysis_agent import TurnAnalysisAgent
from app.config.loader import load_proactive_config, load_profile
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from tests.helpers.fakes import ErrorBackend, FakeBackend, FakeResponseAgent


def test_turn_analysis_agent_parses_valid_json_candidates() -> None:
    backend = FakeBackend(
        json.dumps(
            {
                "task_candidates": [
                    {
                        "title": "READMEを整える",
                        "due_text": "明日",
                        "confidence": 0.82,
                        "needs_confirmation": True,
                        "source_text": "明日までにREADMEを整えないとな",
                    }
                ],
                "memory_candidates": [
                    {
                        "content": "ユーザーは回答を短めにしたい",
                        "kind": "preference",
                        "confidence": 0.91,
                        "sensitivity": "normal",
                        "needs_confirmation": False,
                        "source_text": "短めに答えて",
                    }
                ],
                "open_loop_candidates": [
                    {
                        "title": "起動後自立動作設計を詰める",
                        "summary": "起動後にどの文脈を再開するか未確定",
                        "suggested_next_step": "StartupBriefingServiceから実装する",
                        "confidence": 0.74,
                        "source_text": "起動後の自立動作を考えたい",
                    }
                ],
                "follow_up_candidates": [
                    {
                        "text": "明日の朝にREADME整備を確認する",
                        "due_text": "明日の朝",
                        "reason": "ユーザーが明日までと言及した",
                        "confidence": 0.7,
                    }
                ],
                "permission_required_actions": [
                    {"action": "create_task", "risk": "normal", "reason": "タスク候補がある"}
                ],
            },
            ensure_ascii=False,
        )
    )

    analysis = TurnAnalysisAgent(backend=backend).analyze(
        user_text="明日までにREADMEを整えないとな",
        assistant_text="README整備の観点を整理します。",
    )

    assert analysis.status == "ok"
    assert analysis.task_candidates[0].title == "READMEを整える"
    assert analysis.task_candidates[0].needs_confirmation is True
    assert analysis.memory_candidates[0].kind == "preference"
    assert analysis.memory_candidates[0].needs_confirmation is False
    assert analysis.open_loop_candidates[0].suggested_next_step == "StartupBriefingServiceから実装する"
    assert analysis.follow_up_candidates[0].reason == "ユーザーが明日までと言及した"
    assert analysis.permission_required_actions == [
        {"action": "create_task", "risk": "normal", "reason": "タスク候補がある"}
    ]
    prompt = backend.calls[0][0]
    assert "Latest user turn" in prompt
    assert "明日までにREADMEを整えないとな" in prompt
    assert "{{user_text}}" not in prompt


def test_turn_analysis_agent_returns_empty_on_invalid_json_and_backend_failure() -> None:
    invalid = TurnAnalysisAgent(backend=FakeBackend("not json")).analyze("こんにちは", "こんにちは。")
    failure = TurnAnalysisAgent(backend=ErrorBackend()).analyze("こんにちは", "こんにちは。")

    assert invalid.status == "invalid_json"
    assert invalid.has_candidates() is False
    assert failure.status == "backend_failure"
    assert failure.has_candidates() is False


def test_turn_analysis_agent_filters_sensitive_memory_and_action_content() -> None:
    backend = FakeBackend(
        json.dumps(
            {
                "task_candidates": [],
                "memory_candidates": [
                    {
                        "content": "API key is secret",
                        "kind": "project",
                        "confidence": 1.0,
                        "sensitivity": "normal",
                        "needs_confirmation": False,
                        "source_text": "API key is secret",
                    },
                    {
                        "content": "ユーザーは日本語で話したい",
                        "kind": "preference",
                        "confidence": 0.8,
                        "sensitivity": "normal",
                        "needs_confirmation": False,
                        "source_text": "日本語でお願い",
                    },
                ],
                "open_loop_candidates": [],
                "follow_up_candidates": [],
                "permission_required_actions": [
                    {"action": "write_memory", "value": "access token abc"},
                    {"action": "create_task", "title": "READMEを整える"},
                ],
            },
            ensure_ascii=False,
        )
    )

    analysis = TurnAnalysisAgent(backend=backend).analyze("日本語でお願い", "わかりました。")

    assert [candidate.content for candidate in analysis.memory_candidates] == ["ユーザーは日本語で話したい"]
    assert analysis.permission_required_actions == [{"action": "create_task", "title": "READMEを整える"}]


def test_session_manager_queues_turn_analysis_approval_without_creating_tasks_or_memories(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "test.sqlite3")
    turn_agent = TurnAnalysisAgent(
        backend=FakeBackend(
            json.dumps(
                {
                    "task_candidates": [
                        {
                            "title": "READMEを整える",
                            "due_text": "明日",
                            "confidence": 0.82,
                            "needs_confirmation": True,
                            "source_text": "明日までにREADMEを整えないとな",
                        }
                    ],
                    "memory_candidates": [],
                    "open_loop_candidates": [],
                    "follow_up_candidates": [],
                    "permission_required_actions": [],
                },
                ensure_ascii=False,
            )
        )
    )
    manager = SessionManager(
        load_profile(),
        load_proactive_config(),
        store,
        response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        turn_analysis_agent=turn_agent,
    )

    output = manager.handle_input("オービット、明日までにREADMEを整えないとな")

    assert output.text == "受け取りました。次に決めたいことを1つ教えてください。"
    logs = store.recent_decision_logs()
    assert logs[0].kind == "turn_analysis"
    assert logs[0].decision == "recorded"
    assert logs[0].reason == "ok"
    assert logs[0].candidate_text == "READMEを整える"
    assert logs[0].score == 0.82
    metadata = json.loads(logs[0].metadata_json or "{}")
    assert metadata["task_candidates"][0]["needs_confirmation"] is True
    approvals = store.list_approval_requests()
    assert len(approvals) == 1
    assert approvals[0].action == "create_task"
    assert approvals[0].payload["title"] == "READMEを整える"
    assert approvals[0].payload["source"] == "turn_analysis"
    assert store.list_tasks() == []
    assert store.list_memories() == []
