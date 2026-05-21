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
