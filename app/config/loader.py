import json
from pathlib import Path
from typing import Any

from app.config.autonomy import AutonomyConfig, parse_autonomy_config
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
