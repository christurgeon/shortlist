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
    # False when the source fills only auxiliary sections: it cannot explain a missing
    # sub-score, so the prompt's data-gaps line never names it.
    feeds_axes: bool = True
    # True when having nothing for a symbol is this source's usual outcome. Coverage then
    # does not call it a gap, so such a source must also have `feeds_axes = False`.
    no_data_is_normal: bool = False

    @abstractmethod
    async def fetch(self, ticker: str) -> SourceResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class _KeyedHttpSource(Source):
    """Shared scaffolding for keyed JSON-over-HTTP sources (FMP, Finnhub): env-key
    resolution, a lazy httpx ``AsyncClient``, and a cache-delegating GET with optional
    Retry-After-aware backoff. Subclasses set ``BASE`` / ``_AUTH_PARAM`` / ``_ENV_VAR``
    / ``_PROVIDER`` and implement ``fetch``. ``_max_retries`` defaults to 0 (one
    attempt); a subclass opts in by raising it."""

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
    """Fetch each named section into `res.raw` concurrently. Failures become redacted
    `"<source>.<section>: <err>"` strings in `res.errors`. One failed section never
    aborts the others."""
    async def _one(name: str, path: str, params: dict[str, Any]) -> tuple[str, Any, Optional[str]]:
        try:
            return name, await get(path, **params), None
        except Exception as e:
            return name, None, f"{res.source}.{name}: {redact_secrets(e)}"

    for name, value, err in await asyncio.gather(
            *(_one(name, path, params) for name, (path, params) in sections.items())):
        if err is not None:
            res.errors.append(err)
        else:
            res.raw[name] = value


async def _retry_after_backoff(r: Any, attempt: int, max_retries: int) -> bool:
    """Backoff decision for `_KeyedHttpSource._get`. 429 and 5xx are retriable; 402
    gating and other 4xx are not. Sleeps (Retry-After, else 2**attempt) and returns
    True to retry, False once not retriable or attempts are exhausted."""
    retriable = r.status_code == 429 or 500 <= r.status_code < 600
    if retriable and attempt < max_retries:
        await asyncio.sleep(_retry_after_seconds(r.headers.get("Retry-After"), 2 ** attempt))
        return True
    return False
