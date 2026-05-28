from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.config.autonomy import DEFAULT_PERMISSION_ACTIONS, AutonomyConfig, AutonomyLevel


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class RiskLevel(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True)
class ActionPermissionPolicy:
    normal: PermissionDecision = PermissionDecision.ALLOW
    high: PermissionDecision = PermissionDecision.ASK


@dataclass(frozen=True)
class PermissionPolicyConfig:
    actions: dict[str, ActionPermissionPolicy]
    unknown_action: PermissionDecision = PermissionDecision.DENY


DEFAULT_ACTION_POLICIES = {
    "create_task": ActionPermissionPolicy(normal=PermissionDecision.ALLOW, high=PermissionDecision.ASK),
    "snooze_task": ActionPermissionPolicy(normal=PermissionDecision.ASK, high=PermissionDecision.ASK),
    "mark_task_done": ActionPermissionPolicy(normal=PermissionDecision.ASK, high=PermissionDecision.ASK),
    "write_memory": ActionPermissionPolicy(normal=PermissionDecision.ASK, high=PermissionDecision.ASK),
    "run_local_check": ActionPermissionPolicy(normal=PermissionDecision.DENY, high=PermissionDecision.DENY),
}


def default_permission_policy_config() -> PermissionPolicyConfig:
    return PermissionPolicyConfig(
        actions={
            action: DEFAULT_ACTION_POLICIES.get(action, ActionPermissionPolicy(normal=PermissionDecision.ASK))
            for action in DEFAULT_PERMISSION_ACTIONS
        },
        unknown_action=PermissionDecision.DENY,
    )


def parse_permission_policy_config(config: dict[str, Any] | None) -> PermissionPolicyConfig:
    raw = config or {}
    policy = raw.get("permission_policy", raw)
    if not isinstance(policy, dict):
        return default_permission_policy_config()

    default_policy = default_permission_policy_config()
    default_decision = _parse_decision(policy.get("default"), PermissionDecision.ASK)
    simple_rules = policy.get("rules")
    raw_actions = policy.get("actions")

    actions: dict[str, ActionPermissionPolicy] = {}
    for action in DEFAULT_PERMISSION_ACTIONS:
        action_config = raw_actions.get(action) if isinstance(raw_actions, dict) else None
        if action_config is None and isinstance(simple_rules, dict):
            action_config = simple_rules.get(action)
        if isinstance(action_config, dict):
            actions[action] = ActionPermissionPolicy(
                normal=_parse_decision(action_config.get("normal"), default_decision),
                high=_parse_decision(action_config.get("high"), PermissionDecision.ASK),
            )
        elif action_config is not None:
            normal = _parse_decision(action_config, default_decision)
            actions[action] = ActionPermissionPolicy(normal=normal, high=_high_decision_for(normal))
        elif "default" in policy or isinstance(simple_rules, dict) or isinstance(raw_actions, dict):
            actions[action] = ActionPermissionPolicy(normal=default_decision, high=_high_decision_for(default_decision))
        else:
            actions[action] = default_policy.actions[action]

    return PermissionPolicyConfig(
        actions=actions,
        unknown_action=_parse_unknown_action_decision(policy.get("unknown_action")),
    )


def evaluate_permission(
    action: str,
    autonomy: AutonomyConfig,
    risk_level: str | RiskLevel = RiskLevel.NORMAL,
    policy: PermissionPolicyConfig | None = None,
) -> PermissionDecision:
    permission_policy = policy or default_permission_policy_config()
    if autonomy.effective_level == AutonomyLevel.OFF:
        return PermissionDecision.DENY

    action_policy = permission_policy.actions.get(action)
    if action_policy is None:
        return permission_policy.unknown_action

    risk = _parse_risk_level(risk_level)
    if risk == RiskLevel.HIGH:
        return _cap_high_risk_decision(action_policy.high)

    if action_policy.normal == PermissionDecision.DENY:
        return PermissionDecision.DENY

    if autonomy.effective_level == AutonomyLevel.SUGGEST_ONLY:
        return _cap_suggest_only_decision(action_policy.normal)

    if not autonomy.allow_local_actions or not autonomy.requires_permission(action):
        return PermissionDecision.ASK

    return action_policy.normal


def _parse_decision(value: object, default: PermissionDecision) -> PermissionDecision:
    try:
        return PermissionDecision(str(value))
    except ValueError:
        return default


def _parse_unknown_action_decision(value: object) -> PermissionDecision:
    decision = _parse_decision(value, PermissionDecision.DENY)
    if decision == PermissionDecision.ALLOW:
        return PermissionDecision.DENY
    return decision


def _high_decision_for(normal: PermissionDecision) -> PermissionDecision:
    if normal == PermissionDecision.DENY:
        return PermissionDecision.DENY
    return PermissionDecision.ASK


def _parse_risk_level(value: str | RiskLevel) -> RiskLevel:
    try:
        return RiskLevel(str(value))
    except ValueError:
        return RiskLevel.HIGH


def _cap_suggest_only_decision(decision: PermissionDecision) -> PermissionDecision:
    if decision == PermissionDecision.ALLOW:
        return PermissionDecision.ASK
    return decision


def _cap_high_risk_decision(decision: PermissionDecision) -> PermissionDecision:
    if decision == PermissionDecision.ALLOW:
        return PermissionDecision.ASK
    return decision
