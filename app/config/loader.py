import json
from pathlib import Path
from typing import Any

from app.config.autonomy import AutonomyConfig, parse_autonomy_config
from app.config.permission_policy import PermissionPolicyConfig, parse_permission_policy_config
from app.paths import CONFIG_DIR


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def load_profile() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "profile.json")


def load_proactive_config() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "proactive.json")


def load_autonomous_config() -> dict[str, Any]:
    path = CONFIG_DIR / "autonomous.json"
    default = {
        "enabled": True,
        "tick_interval_seconds": 30,
        "default_timezone": "Asia/Tokyo",
        "delivery_mode": "speak_when_idle",
        "catch_up_missed_jobs": True,
        "retry_after_seconds": 300,
    }
    if not path.exists():
        return default
    try:
        loaded = load_json(path)
    except (json.JSONDecodeError, ValueError):
        return default
    return {**default, **loaded}


def load_autonomy_config(profile: dict[str, Any] | None = None) -> AutonomyConfig:
    path = CONFIG_DIR / "autonomy.json"
    if path.exists():
        try:
            return parse_autonomy_config(load_json(path))
        except (json.JSONDecodeError, ValueError):
            return parse_autonomy_config(None)
    if profile is not None:
        return parse_autonomy_config(profile)
    return parse_autonomy_config(None)


def load_permission_policy_config() -> PermissionPolicyConfig:
    path = CONFIG_DIR / "permission_policy.json"
    if path.exists():
        try:
            return parse_permission_policy_config(load_json(path))
        except (json.JSONDecodeError, ValueError):
            return parse_permission_policy_config(None)
    return parse_permission_policy_config(None)
