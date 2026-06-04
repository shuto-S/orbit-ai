import json
from datetime import UTC, datetime
from typing import Any

from app.text import sanitize_text


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_due_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def loads_review_items(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def dumps_dict(value: dict[str, Any]) -> str:
    return json.dumps(_clean_json_value(value), ensure_ascii=False, sort_keys=True)


def _clean_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, list):
        return [_clean_json_value(item) for item in value]
    if isinstance(value, dict):
        return {sanitize_text(str(key)).strip(): _clean_json_value(item) for key, item in value.items()}
    return sanitize_text(str(value))
