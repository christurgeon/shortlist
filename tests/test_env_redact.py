import os

from shortlist.env import load_env, redact_secrets


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


def test_redacts_telegram_bot_token_at_end_of_string_no_trailing_slash():
    # An httpx error message can embed the URL with nothing after the token —
    # the old regex required a trailing "/" and left this case unredacted.
    raw = "httpx.ConnectError: https://api.telegram.org/bot123456:ABCdef-token"
    out = redact_secrets(raw)
    assert "123456:ABCdef-token" not in out
    assert "<redacted>" in out


# --- load_env: an explicit `export` must always win over the .env file --------
#
# load_env() was documented (CLAUDE.md "Secrets") but had zero test coverage:
# `load_dotenv(dotenv_path, override=False)` is the one line enforcing "a real
# shell export always wins" -- flip that kwarg (or drop it) and secrets in a
# stale committed-adjacent .env could silently shadow a deliberately-set key,
# with no test catching it.

_TEST_VAR = "SHORTLIST_TEST_ENV_LOAD_VAR"


def test_load_env_real_export_wins_over_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setenv(_TEST_VAR, "from-shell")
    envfile = tmp_path / ".env"
    envfile.write_text(f"{_TEST_VAR}=from-file\n")
    load_env(str(envfile))
    assert os.environ[_TEST_VAR] == "from-shell"  # export beats the file, never overridden


def test_load_env_sets_var_from_file_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_VAR, raising=False)
    envfile = tmp_path / ".env"
    envfile.write_text(f"{_TEST_VAR}=from-file\n")
    try:
        load_env(str(envfile))
        assert os.environ[_TEST_VAR] == "from-file"
    finally:
        monkeypatch.delenv(_TEST_VAR, raising=False)  # load_dotenv sets os.environ directly


def test_load_env_missing_file_returns_none(tmp_path):
    assert load_env(str(tmp_path / "nope.env")) is None


def test_load_env_returns_loaded_path_on_success(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_VAR, raising=False)
    envfile = tmp_path / ".env"
    envfile.write_text(f"{_TEST_VAR}=x\n")
    try:
        assert load_env(str(envfile)) == str(envfile)
    finally:
        monkeypatch.delenv(_TEST_VAR, raising=False)
