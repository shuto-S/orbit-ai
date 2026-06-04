from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.memory.store import MemoryStore
from app.text import sanitize_text

DEFAULT_TIMEZONE = "Asia/Tokyo"
_INTENT_WORDS = ("リマインド", "教えて", "知らせて", "通知")
_STRONG_INTENT_WORDS = ("リマインド", "知らせて", "通知")
_RELATIVE_PATTERN = re.compile(r"(?P<amount>\d+)\s*(?P<unit>分|時間|日)\s*後")
_DAY_TIME_PATTERN = re.compile(
    r"(?P<day>今日|本日|明日)\s*"
    r"(?P<hour>\d{1,2})"
    r"(?:(?:[:：](?P<minute_colon>\d{1,2}))|(?:時(?:(?P<minute_jp>\d{1,2})分?)?))?"
)
_ISO_PATTERN = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2}(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?)"
)


@dataclass(frozen=True)
class ReminderParseResult:
    due_at: datetime
    text: str
    matched_text: str
    timezone: str


def parse_reminder_request(
    text: str,
    now: datetime | None = None,
    default_timezone: str = DEFAULT_TIMEZONE,
    require_intent: bool = False,
) -> ReminderParseResult | None:
    safe_text = sanitize_text(text).strip()
    if not safe_text:
        return None
    if require_intent and not _has_reminder_intent(safe_text):
        return None

    zone = _zone(default_timezone)
    base = _local_now(now, zone)
    parsed = _parse_relative(safe_text, base, zone)
    if parsed is not None:
        return parsed
    parsed = _parse_day_time(safe_text, base, zone)
    if parsed is not None:
        return parsed
    return _parse_iso(safe_text, zone)


def create_reminder_job(
    store: MemoryStore,
    reminder: ReminderParseResult,
    source: str = "manual",
    source_session_id: str | None = None,
) -> int | None:
    if not reminder.text:
        return None
    due_at = reminder.due_at.astimezone(UTC).isoformat()
    return store.add_autonomous_job(
        kind="reminder",
        title=reminder.text,
        schedule_type="once",
        next_run_at=due_at,
        timezone=reminder.timezone,
        payload={
            "text": reminder.text,
            "matched_text": reminder.matched_text,
            "due_at": due_at,
        },
        source=source,
        source_session_id=source_session_id,
    )


def has_reminder_request_intent(text: str) -> bool:
    safe_text = sanitize_text(text).strip()
    if any(word in safe_text for word in _STRONG_INTENT_WORDS):
        return True
    return "教えて" in safe_text and (
        _RELATIVE_PATTERN.search(safe_text) is not None
        or _DAY_TIME_PATTERN.search(safe_text) is not None
        or _ISO_PATTERN.search(safe_text) is not None
    )


def has_strong_reminder_intent(text: str) -> bool:
    safe_text = sanitize_text(text).strip()
    return any(word in safe_text for word in _STRONG_INTENT_WORDS)


def _parse_relative(text: str, base: datetime, zone: ZoneInfo) -> ReminderParseResult | None:
    match = _RELATIVE_PATTERN.search(text)
    if match is None:
        return None
    amount = int(match.group("amount"))
    unit = match.group("unit")
    if unit == "分":
        delta = timedelta(minutes=amount)
    elif unit == "時間":
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(days=amount)
    due_at = base + delta
    message = _reminder_message(text, match.group(0))
    return ReminderParseResult(due_at=due_at, text=message, matched_text=match.group(0), timezone=str(zone.key))


def _parse_day_time(text: str, base: datetime, zone: ZoneInfo) -> ReminderParseResult | None:
    match = _DAY_TIME_PATTERN.search(text)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute_colon") or match.group("minute_jp") or 0)
    if hour > 23 or minute > 59:
        return None
    day = base.date()
    if match.group("day") == "明日":
        day += timedelta(days=1)
    due_at = datetime.combine(day, time(hour, minute), tzinfo=zone)
    message = _reminder_message(text, match.group(0))
    return ReminderParseResult(due_at=due_at, text=message, matched_text=match.group(0), timezone=str(zone.key))


def _parse_iso(text: str, zone: ZoneInfo) -> ReminderParseResult | None:
    match = _ISO_PATTERN.search(text)
    if match is None:
        return None
    raw = match.group("iso").replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    message = _reminder_message(text, match.group("iso"))
    return ReminderParseResult(
        due_at=parsed.astimezone(zone),
        text=message,
        matched_text=match.group("iso"),
        timezone=str(zone.key),
    )


def _reminder_message(text: str, matched_text: str) -> str:
    message = text.replace(matched_text, " ", 1)
    replacements = (
        "リマインドして",
        "リマインドする",
        "リマインド",
        "教えて",
        "知らせて",
        "通知して",
        "通知",
        "お願いします",
        "お願い",
    )
    for phrase in replacements:
        message = message.replace(phrase, " ")
    message = re.sub(r"^[\s、。:：にまでをと「『\"]+", "", message)
    message = re.sub(r"[\s、。:：にまでをと」』\"]+$", "", message)
    message = re.sub(r"\s+", " ", message)
    return sanitize_text(message).strip()


def _has_reminder_intent(text: str) -> bool:
    return any(word in text for word in _INTENT_WORDS)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _local_now(now: datetime | None, zone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)
