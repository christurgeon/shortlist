from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Query-string secrets that must never appear in logs or error messages.
_SECRET_PARAMS = ("apikey", "token", "api_key")
_SECRET_RE = re.compile(rf"((?:{'|'.join(_SECRET_PARAMS)})=)[^&\s]+", re.IGNORECASE)

# Bare API tokens that may appear in CLI/subprocess output (not as URL params).
_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

# Telegram bot token embedded in a URL path: /bot<token>/... or /bot<token> at the
# end of the string (an httpx error's str() can end right after the token, with no
# trailing path segment — the old regex required one and left that case unredacted).
_TELEGRAM_BOT_RE = re.compile(r"/bot[^/\s]+/?")


def _redact_telegram_match(m: "re.Match[str]") -> str:
    return "/bot<redacted>/" if m.group(0).endswith("/") else "/bot<redacted>"


def redact_secrets(text: object) -> str:
    """Strip API keys/tokens from a string (e.g. an HTTP error containing a URL,
    or a leaked Anthropic token in subprocess output)."""
    s = _SECRET_RE.sub(r"\1<redacted>", str(text))
    s = _TOKEN_RE.sub("<redacted>", s)
    return _TELEGRAM_BOT_RE.sub(_redact_telegram_match, s)


def load_env(path: Optional[str] = None) -> Optional[str]:
    """Load API keys from a .env file into the environment, if one exists.

    Searches upward from the current working directory for a `.env` file (or
    uses `path` if given). Real environment variables already set take
    precedence — an explicit `export` always wins over the file, so secrets in
    your shell are never silently overridden.

    `python-dotenv` is imported lazily and treated as optional: if it isn't
    installed, this is a no-op and keys must come from the environment directly.
    Returns the path of the .env file that was loaded, or None.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return None

    dotenv_path = path or find_dotenv(usecwd=True)
    if not dotenv_path or not Path(dotenv_path).is_file():
        return None
    load_dotenv(dotenv_path, override=False)
    return dotenv_path
