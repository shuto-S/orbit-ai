import json
from pathlib import Path
from typing import Any

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
