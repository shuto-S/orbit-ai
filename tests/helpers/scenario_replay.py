import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.loader import load_proactive_config
from app.memory.store import MemoryStore
from app.session.proactive_policy import ProactiveDecision, ProactivePolicy


@dataclass(frozen=True)
class Scenario:
    name: str
    now: datetime
    idle_since: datetime | None
    tasks: list[dict[str, Any]]
    summaries: list[dict[str, Any]]
    recent_proactive_events: list[dict[str, Any]]
    expected: dict[str, Any]


@dataclass(frozen=True)
class ReplayResult:
    scenario: Scenario
    selected_open_loops: list[str]
    daily_review_candidate: str | None
    proactive_decision: ProactiveDecision


def scenario_paths(fixtures_dir: Path) -> list[Path]:
    return sorted(fixtures_dir.glob("*.json"))


def load_scenario(path: Path) -> Scenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: scenario must be a JSON object")

    _require_type(path, raw, "name", str)
    _require_type(path, raw, "now", str)
    _require_type(path, raw, "expected", dict)

    idle_since = raw.get("idle_since")
    if idle_since is not None and not isinstance(idle_since, str):
        raise ValueError(f"{path}: idle_since must be a string or null")

    tasks = raw.get("tasks", [])
    summaries = raw.get("summaries", [])
    recent_proactive_events = raw.get("recent_proactive_events", [])
    _ensure_list(path, "tasks", tasks)
    _ensure_list(path, "summaries", summaries)
    _ensure_list(path, "recent_proactive_events", recent_proactive_events)

    expected = raw["expected"]
    if "allowed" not in expected or not isinstance(expected["allowed"], bool):
        raise ValueError(f"{path}: expected.allowed must be a boolean")
    if "reason" in expected and not isinstance(expected["reason"], str):
        raise ValueError(f"{path}: expected.reason must be a string")
    if "candidate_contains" in expected and not isinstance(expected["candidate_contains"], str):
        raise ValueError(f"{path}: expected.candidate_contains must be a string")

    return Scenario(
        name=raw["name"],
        now=_parse_datetime(path, "now", raw["now"]),
        idle_since=_parse_datetime(path, "idle_since", idle_since) if idle_since else None,
        tasks=tasks,
        summaries=summaries,
        recent_proactive_events=recent_proactive_events,
        expected=expected,
    )


@contextmanager
def replay_scenario(path: Path) -> Iterator[ReplayResult]:
    scenario = load_scenario(path)
    with tempfile.TemporaryDirectory() as tempdir:
        store = MemoryStore(Path(tempdir) / "scenario.sqlite3")
        build_store_from_scenario(store, scenario)
        selected_open_loops = store.latest_open_loops()
        decision = ProactivePolicy(load_proactive_config(), store).evaluate(scenario.idle_since, now=scenario.now)
        yield ReplayResult(
            scenario,
            selected_open_loops,
            selected_open_loops[0] if selected_open_loops else None,
            decision,
        )


def build_store_from_scenario(store: MemoryStore, scenario: Scenario) -> None:
    for summary in scenario.summaries:
        if not isinstance(summary, dict):
            raise ValueError(f"{scenario.name}: summaries entries must be objects")
        store.add_summary(
            session_id=str(summary.get("session_id", "scenario-summary")),
            summary=str(summary.get("summary", "")),
            open_loops=_string_list(scenario.name, "summary.open_loops", summary.get("open_loops", [])),
            decisions=_string_list(scenario.name, "summary.decisions", summary.get("decisions", [])),
            follow_up_candidates=_string_list(
                scenario.name,
                "summary.follow_up_candidates",
                summary.get("follow_up_candidates", []),
            ),
        )

    for task in scenario.tasks:
        _insert_task(store, scenario.name, task, scenario.now)

    for event in scenario.recent_proactive_events:
        _insert_proactive_event(store, scenario.name, event)


def assert_replay_matches_expected(result: ReplayResult) -> None:
    expected = result.scenario.expected
    decision = result.proactive_decision

    assert decision.allowed is expected["allowed"], result.scenario.name
    if "reason" in expected:
        assert decision.reason == expected["reason"], result.scenario.name
    if "candidate_contains" in expected:
        assert expected["candidate_contains"] in decision.candidate.permission_text, result.scenario.name
    if "selected_open_loops" in expected:
        assert result.selected_open_loops == expected["selected_open_loops"], result.scenario.name
    if "daily_review_candidate" in expected:
        assert result.daily_review_candidate == expected["daily_review_candidate"], result.scenario.name


def _insert_task(store: MemoryStore, scenario_name: str, task: dict[str, Any], now: datetime) -> None:
    if not isinstance(task, dict):
        raise ValueError(f"{scenario_name}: tasks entries must be objects")
    title = task.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{scenario_name}: task.title must be a non-empty string")
    status = str(task.get("status", "open"))
    if status not in {"open", "done", "snoozed", "cancelled"}:
        raise ValueError(f"{scenario_name}: task.status is invalid: {status}")
    now_iso = now.isoformat()
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO tasks
            (title, description, status, priority, due_at, source, source_session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                task.get("description"),
                status,
                float(task.get("priority", 0.5)),
                task.get("due_at"),
                task.get("source", "scenario"),
                task.get("source_session_id"),
                str(task.get("created_at", now_iso)),
                str(task.get("updated_at", now_iso)),
            ),
        )


def _insert_proactive_event(store: MemoryStore, scenario_name: str, event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        raise ValueError(f"{scenario_name}: recent_proactive_events entries must be objects")
    proposed_text = event.get("proposed_text")
    outcome = event.get("outcome")
    created_at = event.get("created_at")
    if not isinstance(proposed_text, str) or not proposed_text.strip():
        raise ValueError(f"{scenario_name}: event.proposed_text must be a non-empty string")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ValueError(f"{scenario_name}: event.outcome must be a non-empty string")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError(f"{scenario_name}: event.created_at must be a non-empty string")
    _parse_datetime(Path(scenario_name), "event.created_at", created_at)
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO proactive_events (memory_id, proposed_text, user_response, outcome, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.get("memory_id"),
                proposed_text,
                event.get("user_response"),
                outcome,
                created_at,
            ),
        )


def _require_type(path: Path, raw: dict[str, Any], key: str, expected_type: type) -> None:
    if key not in raw or not isinstance(raw[key], expected_type):
        raise ValueError(f"{path}: {key} must be {expected_type.__name__}")


def _ensure_list(path: Path, key: str, value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path}: {key} must be a list")


def _string_list(scenario_name: str, key: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{scenario_name}: {key} must be a list")
    return [str(item) for item in value]


def _parse_datetime(path: Path, key: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path}: {key} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
