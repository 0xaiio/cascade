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
# 同一批名字在 JSON 里是 `"name":"value"` 这种**冒号 + 引号**的形式，赋值式正则抓不到，
# 而 B站 的接口正文恰恰全是 JSON。投稿令牌（`access_token` / `refresh_token`）就是从这个
# 形态里取出来的，所以这条兜底必须覆盖它 —— 实测 `{"access_token":"X"}` 在只有上面那条
# 正则时会被原样放行。这里只认「引号包着的键」，避免把散文里出现的同名词一起吃掉。
_SECRET_JSON_RE = re.compile(rf"(?i)([\"'])({_SECRET_NAMES})\1\s*:\s*([\"'])(.*?)\3")


def sanitize_log_message(value: Any) -> str:
    text = str(value)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    text = _SECRET_JSON_RE.sub(r"\1\2\1:\3<redacted>\3", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
