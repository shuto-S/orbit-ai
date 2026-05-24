def sanitize_text(value: str) -> str:
    """Replace invalid Unicode surrogates before DB writes or terminal output."""
    repaired = "".join("\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character for character in value)
    return repaired.encode("utf-8", errors="replace").decode("utf-8")
