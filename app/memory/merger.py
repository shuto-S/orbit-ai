import re
from dataclasses import dataclass

SECRET_MARKERS = (
    "api key",
    "apikey",
    "access token",
    "secret",
    "password",
    "private key",
    "client secret",
    "パスワード",
    "秘密鍵",
    "認証トークン",
    "アクセストークン",
)

CONTRADICTION_PAIRS = (
    ("短め", "詳しく"),
    ("簡潔", "詳しく"),
    ("好き", "嫌い"),
    ("好み", "苦手"),
    ("prefer concise", "prefer detailed"),
)


@dataclass(frozen=True)
class MemoryMergeDecision:
    duplicate_id: int | None = None
    archive_ids: tuple[int, ...] = ()
    should_store: bool = True
    reason: str = ""


def normalize_memory_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def is_sensitive_text(text: str) -> bool:
    normalized = normalize_memory_text(text)
    return any(marker in normalized for marker in SECRET_MARKERS)


def looks_contradictory(existing_text: str, new_text: str) -> bool:
    existing = normalize_memory_text(existing_text)
    new = normalize_memory_text(new_text)
    for left, right in CONTRADICTION_PAIRS:
        if left in existing and right in new:
            return True
        if right in existing and left in new:
            return True
    return False
