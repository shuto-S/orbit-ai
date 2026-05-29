import unicodedata

GREETING_TERMS = (
    "こんにちは",
    "こんばんは",
    "こんばんわ",
    "おはよう",
    "hello",
    "hi",
)

WAKE_STRIP_CHARS = " 、,。.!！?？\t"


def strip_wake_word(text: str, wake_words: list[str]) -> str | None:
    normalized_text = normalize_wake_text(text)
    for word in sorted(wake_words, key=len, reverse=True):
        normalized_word = normalize_wake_text(word)
        if not normalized_word:
            continue
        index = normalized_text.find(normalized_word)
        if index >= 0:
            start = normalized_index_to_original(text, index)
            end = normalized_index_to_original(text, index + len(normalized_word))
            stripped = text[:start] + text[end:]
            return stripped.strip(WAKE_STRIP_CHARS)
    return None


def normalize_wake_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(hiragana_to_katakana(char) for char in normalized if not char.isspace())


def hiragana_to_katakana(char: str) -> str:
    codepoint = ord(char)
    if 0x3041 <= codepoint <= 0x3096:
        return chr(codepoint + 0x60)
    return char


def normalized_index_to_original(text: str, target_index: int) -> int:
    normalized_length = 0
    for index, char in enumerate(text):
        if normalized_length == target_index:
            return index
        normalized_length += len(normalize_wake_text(char))
        if normalized_length > target_index:
            return index + 1
    return len(text)


def is_wake_greeting(text: str) -> bool:
    normalized = text.strip().lower()
    return any(term in normalized for term in GREETING_TERMS)


def greeting_response(text: str) -> str:
    normalized = text.strip().lower()
    if "おはよう" in normalized:
        return "おはようございます。"
    if "こんばん" in normalized:
        return "こんばんは。"
    if "こんにちは" in normalized:
        return "こんにちは。"
    return "はい、聞いています。"
