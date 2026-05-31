from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Query-string secrets that must never appear in logs or error messages.
_SECRET_PARAMS = ("apikey", "token", "api_key")
_SECRET_RE = re.compile(rf"((?:{'|'.join(_SECRET_PARAMS)})=)[^&\s]+", re.IGNORECASE)


def redact_secrets(text: object) -> str:
    """Strip API keys/tokens from a string (e.g. an HTTP error containing a URL).

    Provider errors from requests/httpx embed the full request URL — including
    `?apikey=...` — so anything printed from them must pass through here first.
    """
    return _SECRET_RE.sub(r"\1<redacted>", str(text))


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
