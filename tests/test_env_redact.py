from shortlist.env import redact_secrets


def test_redacts_url_query_secrets():
    assert "<redacted>" in redact_secrets("GET https://x?apikey=ABC123&p=1")
    assert "ABC123" not in redact_secrets("GET https://x?apikey=ABC123&p=1")


def test_redacts_bare_anthropic_token():
    out = redact_secrets("claude failed: sk-ant-api03-DEADbeef_tok-AA used")
    assert "sk-ant-api03-DEADbeef_tok-AA" not in out
    assert "<redacted>" in out


def test_passes_through_clean_text():
    assert redact_secrets("no secrets here") == "no secrets here"


def test_redacts_telegram_bot_token_in_url_path():
    raw = "connect error: api.telegram.org/bot123456:ABCdef/sendMessage failed"
    out = redact_secrets(raw)
    assert "123456:ABCdef" not in out
    assert "/bot<redacted>/" in out
