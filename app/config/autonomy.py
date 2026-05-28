from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AutonomyLevel(StrEnum):
    OFF = "off"
    SUGGEST_ONLY = "suggest_only"
    ASK_THEN_ACT = "ask_then_act"


DEFAULT_PERMISSION_ACTIONS = (
    "create_task",
    "snooze_task",
    "mark_task_done",
    "write_memory",
    "run_local_check",
)


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool = True
    level: AutonomyLevel = AutonomyLevel.SUGGEST_ONLY
    allow_local_actions: bool = False
    require_permission_for: tuple[str, ...] = DEFAULT_PERMISSION_ACTIONS

    @property
    def effective_level(self) -> AutonomyLevel:
        if not self.enabled:
            return AutonomyLevel.OFF
        return self.level

    def allows_proactive_suggestions(self) -> bool:
        return self.effective_level in {AutonomyLevel.SUGGEST_ONLY, AutonomyLevel.ASK_THEN_ACT}

    def can_run_after_permission(self, action: str) -> bool:
        return (
            self.effective_level == AutonomyLevel.ASK_THEN_ACT
            and self.allow_local_actions
            and action in self.require_permission_for
        )

    def requires_permission(self, action: str) -> bool:
        return action in self.require_permission_for


def parse_autonomy_config(config: dict[str, Any] | None) -> AutonomyConfig:
    raw = config or {}
    autonomy = raw.get("autonomy", raw)
    if not isinstance(autonomy, dict):
        autonomy = {}

    enabled = _bool_or_default(autonomy.get("enabled"), True)
    level = _parse_level(autonomy.get("level"))
    allow_local_actions = _bool_or_default(autonomy.get("allow_local_actions"), False)
    require_permission_for = _parse_permission_actions(autonomy.get("require_permission_for"))

    return AutonomyConfig(
        enabled=enabled,
        level=level,
        allow_local_actions=allow_local_actions,
        require_permission_for=require_permission_for,
    )


def _parse_level(value: object) -> AutonomyLevel:
    try:
        return AutonomyLevel(str(value))
    except ValueError:
        return AutonomyLevel.SUGGEST_ONLY


def _bool_or_default(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _parse_permission_actions(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return DEFAULT_PERMISSION_ACTIONS
    actions = tuple(str(action) for action in value if str(action).strip())
    return actions or DEFAULT_PERMISSION_ACTIONS
