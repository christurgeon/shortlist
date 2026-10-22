from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from ..diskcache import read_json_cache, write_json_cache
from ..entity_match import strip_sec_qualifiers
from ..models import SourceResult, TickerSnapshot
from .base import Source


async def _load_ticker_name_index(client: Any, cache_dir: str) -> tuple[dict, Optional[str]]:
    """Bulk-load SEC company_tickers.json into a ticker->name index, shared by
    GovContractsSource and LobbyingSource (both resolve ticker->entity name the
    same way via `backtest.xbrl`'s month-cached bulk fetch). Returns
    `(index, error)`: error is None on success; on failure index is `{}` and
    error is the redacted exception string."""
    try:
        from ...backtest import xbrl
        month = date.today().strftime("%Y-%m")
        raw = await xbrl.fetch_company_tickers_raw(client, cache_dir=cache_dir, month=month)
        return xbrl.build_name_index(raw), None
    except Exception as e:
        return {}, redact_secrets(e)


def _read_versioned_cache(path: Path, version: int) -> Optional[dict]:
    """Version-gated JSON-dict cache read, shared by GovContractsSource and
    LobbyingSource: a stale-shape / legacy un-versioned payload is treated as a
    miss so bumping `version` refetches rather than deserializing garbage."""
    payload = read_json_cache(path)
    if isinstance(payload, dict) and payload.get("v") == version:
        return payload
    return None


def _write_versioned_cache(path: Path, version: int, payload: dict) -> None:
    write_json_cache(path, {"v": version, **payload})


class _NameIndexedSource(Source):
    """Scaffolding for keyless sources that query by SEC entity name: the shared
    ticker->name index and a version-gated per-ticker day cache. Subclasses set
    ``_CACHE_PREFIX`` / ``_CACHE_V`` and implement ``fetch``."""

    _CACHE_PREFIX: str
    _CACHE_V: int

    def __init__(self, client: Any, cache_dir: Path):
        self._client = client
        self._cache_dir = cache_dir
        self._name_index: Optional[dict] = None
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # name index loads once, not per ticker

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _load_names(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_names_locked()

    async def _load_names_locked(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        self._name_index, self._load_error = await _load_ticker_name_index(
            self._client, str(self._cache_dir))

    def _cache_path(self, ticker: str, day: str) -> Path:
        return self._cache_dir / f"{self._CACHE_PREFIX}-{ticker.upper()}-{day}.json"

    def _read_cache(self, ticker: str, day: str) -> Optional[dict]:
        return _read_versioned_cache(self._cache_path(ticker, day), self._CACHE_V)

    def _write_cache(self, ticker: str, day: str, payload: dict) -> None:
        _write_versioned_cache(self._cache_path(ticker, day), self._CACHE_V, payload)

    async def _begin(self, ticker: str) -> tuple[SourceResult, TickerSnapshot, Optional[str]]:
        """A fresh result whose partial is the returned snapshot, plus the entity name
        to query and match on: the SEC title without its trailing qualifiers.
        ``res.raw["resolved_name"]`` keeps the SEC title. The name is None when the
        caller should return the result as is: the index failed to load (error
        recorded) or the ticker is unknown."""
        res = SourceResult(source=self.name)
        snap = TickerSnapshot(ticker=ticker)
        res.partial = snap
        await self._load_names()
        if self._load_error:
            res.errors.append(f"{self.name}: {self._load_error}")
            return res, snap, None
        title = (self._name_index or {}).get(ticker.upper())
        res.raw = {"resolved_name": title or None}
        if not title:
            return res, snap, None
        return res, snap, strip_sec_qualifiers(title) or title

    def _cached_model(self, ticker: str, day: str, model: type, key: str) -> tuple[Optional[dict], Any]:
        """``(payload, model(**payload[key]))`` from the day cache; the model is None
        for an unmatched payload. ``(None, None)`` on a miss, and a payload that
        fails to rebuild is a miss: the live path runs and, on success, overwrites it."""
        cached = self._read_cache(ticker, day)
        if cached is None:
            return None, None
        try:
            return cached, (model(**cached[key]) if cached.get("matched") else None)
        except Exception:
            return None, None
