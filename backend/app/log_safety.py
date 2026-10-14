import re
from typing import Any


_URL_QUERY_RE = re.compile(r"(https?://[^\s'\"<>?]+)\?[^ \t\r\n'\"<>)]*")
# B站 凭据与 YouTube 的凭据同属「等同账号密码」的一类，必须一起清洗。
# biliup 的输出与 B站 的 JSON 响应里都可能带上它们，而上传日志会被原样落到 data/logs/app.log。
_SECRET_NAMES = (
    "cookie|authorization|po[_-]?token|visitor[_-]?data|"
    "sessdata|bili_jct|dedeuserid|buvid3|access[_-]?token|refresh[_-]?token|csrf"
)
_SECRET_ASSIGNMENT_RE = re.compile(rf"(?i)\b({_SECRET_NAMES})=([^&\s'\"<>)]*)")


def sanitize_log_message(value: Any) -> str:
    text = str(value)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
