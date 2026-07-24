from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

from ..._util import retry_after_seconds as _retry_after_seconds
from ...cache import get_default_cache
from ...env import redact_secrets
from ..models import SourceResult


class Source(ABC):
    """Fetches everything a source can offer for one ticker, returning both the
    verbatim raw payloads (for point-in-time audit) and a normalized partial."""

    name: str = "base"

    @abstractmethod
    async def fetch(self, ticker: str) -> SourceResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class _KeyedHttpSource(Source):
    """Shared scaffolding for keyed JSON-over-HTTP sources (FMP, Finnhub): env-key
    resolution, a lazily-built httpx ``AsyncClient``, and a cache-delegating GET with
    optional Retry-After-aware backoff. Subclasses set ``BASE`` / ``_AUTH_PARAM`` /
    ``_ENV_VAR`` / ``_PROVIDER`` and implement ``fetch``; the default ``_max_retries``
    of 0 means a single attempt (no retry) — a subclass opts in by raising it. Keeping
    the request/cache path in one place means the cacheability + redaction invariants
    don't drift between the two sources."""

    BASE: str = ""
    _AUTH_PARAM: str = ""    # query param that carries the key ("apikey" / "token")
    _ENV_VAR: str = ""       # env var holding the key
    _PROVIDER: str = ""      # cache provider tag

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *, cache=None):
        self.key = api_key or os.environ.get(self._ENV_VAR)
        if not self.key:
            raise RuntimeError(f"{self._ENV_VAR} not set")
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = cache
        self._max_retries = 0   # subclasses opt into retry

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        params[self._AUTH_PARAM] = self.key

        async def fetch():
            # _retry_after_backoff retries transient throttling (429) and 5xx,
            # Retry-After-aware, capped when _max_retries > 0; 402 gating and other
            # 4xx are NOT retried. With _max_retries == 0 this is a single attempt
            # (the no-retry default).
            for attempt in range(self._max_retries + 1):
                r = await self._client.get(f"{self.BASE}/{path}", params=params)
                if await _retry_after_backoff(r, attempt, self._max_retries):
                    continue
                r.raise_for_status()
                return r.json()

        cache = self._cache or get_default_cache()
        return await cache.aget_or_fetch(self._PROVIDER, path, params, fetch)


async def _fetch_sections(
    res: SourceResult,
    get: Callable[..., Awaitable[Any]],
    sections: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    """Fetch each named section into `res.raw`, recording per-section failures as
    redacted `"<source>.<section>: <err>"` strings. One failed section never aborts
    the rest."""
    for name, (path, params) in sections.items():
        try:
            res.raw[name] = await get(path, **params)
        except Exception as e:
            res.errors.append(f"{res.source}.{name}: {redact_secrets(e)}")


async def _retry_after_backoff(r: Any, attempt: int, max_retries: int) -> bool:
    """Shared Retry-After-aware backoff decision for the keyed-HTTP retry loops
    (`_KeyedHttpSource._get`, `LobbyingSource._get_json`): 429 and 5xx are
    retriable, 402 gating and other 4xx are NOT. Sleeps (Retry-After header, else
    exponential 2**attempt) and returns True when the caller should retry;
    returns False once not-retriable or attempts are exhausted, so the caller
    proceeds to `raise_for_status()`/`return r.json()`."""
    retriable = r.status_code == 429 or 500 <= r.status_code < 600
    if retriable and attempt < max_retries:
        await asyncio.sleep(_retry_after_seconds(r.headers.get("Retry-After"), 2 ** attempt))
        return True
    return False
