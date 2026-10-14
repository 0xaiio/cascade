from app.log_safety import sanitize_log_message


def test_sanitize_log_message_redacts_url_queries_and_token_assignments() -> None:
    message = (
        "failed https://rr1---sn.googlevideo.com/videoplayback?expire=1&po_token=SECRET "
        "visitor_data=VISITOR cookie=SID"
    )

    sanitized = sanitize_log_message(message)

    assert "https://rr1---sn.googlevideo.com/videoplayback?<redacted>" in sanitized
    assert "expire=1" not in sanitized
    assert "SECRET" not in sanitized
    assert "VISITOR" not in sanitized
    assert "SID" not in sanitized


def test_sanitize_log_message_redacts_json_shaped_secrets() -> None:
    """B站 的接口正文是 JSON，令牌在里面是 `"name":"value"`，不是 `name=value`。

    011 之前代码从不把带令牌的响应体交给日志，所以赋值式正则够用；引入 TV 端令牌交换后
    `access_token` 第一次成为「代码手里真实持有并且可能出现在正文里」的值，
    这条兜底必须一起覆盖，否则「日志不含凭据」这个既有承诺就有了一个实测缺口。
    """
    message = 'data={"token_info":{"access_token":"LEAK-TOKEN","refresh_token":"LEAK-REFRESH"}}'

    assert sanitize_log_message(message) == (
        'data={"token_info":{"access_token":"<redacted>","refresh_token":"<redacted>"}}'
    )


def test_sanitize_log_message_keeps_non_secret_json_fields_intact() -> None:
    """只吃掉引号包着的凭据键，别把同一行里正常字段也一起抹掉 —— 日志是用来排查的。"""
    message = '{"code":-400,"message":"请求错误","mid":479141149,"cookie_name":"x"}'

    sanitized = sanitize_log_message(message)

    assert '"code":-400' in sanitized
    assert "请求错误" in sanitized
    assert '"mid":479141149' in sanitized
    assert '"cookie_name":"x"' in sanitized
