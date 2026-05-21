import re
from typing import Any


_URL_QUERY_RE = re.compile(r"(https?://[^\s'\"<>?]+)\?[^ \t\r\n'\"<>)]*")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(cookie|authorization|po[_-]?token|visitor[_-]?data)=([^&\s'\"<>)]*)"
)


def sanitize_log_message(value: Any) -> str:
    text = str(value)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
