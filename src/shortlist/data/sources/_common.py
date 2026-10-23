from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from ..diskcache import read_json_cache, write_json_cache
from ..entity_match import normalize_name, strip_sec_qualifiers
from ..models import SourceResult, TickerSnapshot
from .base import Source


async def _load_ticker_name_index(client: Any, cache_dir: str,
                                  ) -> tuple[dict, frozenset[str], Optional[str]]:
    """Bulk-load SEC company_tickers.json into a ticker->name index (month-cached via
    `backtest.xbrl`). Returns `(index, ambiguous, error)`: `ambiguous` is
    `_ambiguous_tickers(raw)`; error is None on success. On failure index is `{}`,
    ambiguous is empty and error is the redacted exception string."""
    try:
        from ...backtest import xbrl
        month = date.today().strftime("%Y-%m")
        raw = await xbrl.fetch_company_tickers_raw(client, cache_dir=cache_dir, month=month)
        return xbrl.build_name_index(raw), _ambiguous_tickers(raw), None
    except Exception as e:
        return {}, frozenset(), redact_secrets(e)


def _ambiguous_tickers(raw: dict) -> frozenset[str]:
    """Tickers whose title, once stripped of SEC qualifiers, matches another CIK's title
    (e.g. `INDEPENDENT BANK CORP` and `INDEPENDENT BANK CORP /MI/`): the strip made the
    two indistinguishable, so the sources abstain rather than misattribute. A collision
    between titles that carried no qualifier is left as it was. Share classes of one CIK
    are not ambiguous."""
    ciks: dict[str, set] = defaultdict(set)
    tickers: dict[str, set] = defaultdict(set)
    stripped: set[str] = set()
    for row in raw.values():
        tk, title = str(row.get("ticker", "")).upper(), row.get("title")
        if not tk or not title:
            continue
        bare = strip_sec_qualifiers(str(title))
        key = normalize_name(bare)
        ciks[key].add(row.get("cik_str"))
        tickers[key].add(tk)
        if bare != str(title):
            stripped.add(key)
    return frozenset(t for key, ts in tickers.items()
                     if len(ciks[key]) > 1 and key in stripped for t in ts)


def _read_versioned_cache(path: Path, version: int) -> Optional[dict]:
    """Version-gated JSON-dict cache read. A payload with another or no version is a
    miss, so bumping `version` refetches."""
    payload = read_json_cache(path)
    if isinstance(payload, dict) and payload.get("v") == version:
        return payload
    return None


def _write_versioned_cache(path: Path, version: int, payload: dict) -> None:
    write_json_cache(path, {"v": version, **payload})


def _parse_aliases(raw: Any) -> dict[str, tuple[str, ...]]:
    """``{TICKER: (name, ...)}`` from a config ``aliases`` mapping. A scalar value is
    one name; non-string keys (YAML reads an unquoted ON or NO as a bool) and blank
    or non-string names are dropped."""
    out: dict[str, tuple[str, ...]] = {}
    if not isinstance(raw, dict):
        return out
    for tk, vals in raw.items():
        if not isinstance(tk, str) or not tk.strip():
            continue
        vals = [vals] if isinstance(vals, str) else vals
        if not isinstance(vals, (list, tuple)):
            continue
        names = tuple(v.strip() for v in vals if isinstance(v, str) and v.strip())
        if names:
            out[tk.strip().upper()] = names
    return out


def alias_keys(names: list[str]) -> frozenset[str]:
    """Normalized forms that match a row exactly: every name after the SEC title.
    A curated alias already names the entity, so it gets no fuzzy band."""
    return frozenset(k for k in map(normalize_name, names[1:]) if k)


_DEFAULT_DEADLINE_S = 90.0


class _NameIndexedSource(Source):
    """Scaffolding for keyless sources that query by SEC entity name: the shared
    ticker->name index, a version-gated per-ticker day cache and one deadline over
    the whole fetch. Subclasses set ``_CACHE_PREFIX`` / ``_CACHE_V`` and implement
    ``_fetch``."""

    _CACHE_PREFIX: str
    _CACHE_V: int

    def __init__(self, client: Any, cache_dir: Path, deadline_s: float = _DEFAULT_DEADLINE_S,
                 aliases: Optional[dict] = None):
        self._client = client
        self._cache_dir = cache_dir
        self._deadline_s = deadline_s
        self._aliases = _parse_aliases(aliases)
        self._name_index: Optional[dict] = None
        self._ambiguous: frozenset[str] = frozenset()
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # name index loads once, not per ticker

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, ticker: str) -> SourceResult:
        """``_fetch`` under the deadline. On expiry: an empty partial, a
        ``"<name>: deadline exceeded"`` error and no cache write. Never raises."""
        try:
            async with asyncio.timeout(self._deadline_s):
                return await self._fetch(ticker)
        except TimeoutError:
            return SourceResult(source=self.name, partial=TickerSnapshot(ticker=ticker),
                                errors=[f"{self.name}: deadline exceeded"])

    async def _fetch(self, ticker: str) -> SourceResult:
        raise NotImplementedError

    async def _load_names(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_names_locked()

    async def _load_names_locked(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        self._name_index, self._ambiguous, self._load_error = await _load_ticker_name_index(
            self._client, str(self._cache_dir))

    def _cache_path(self, ticker: str, day: str) -> Path:
        return self._cache_dir / f"{self._CACHE_PREFIX}-{ticker.upper()}-{day}.json"

    def _read_cache(self, ticker: str, day: str) -> Optional[dict]:
        return _read_versioned_cache(self._cache_path(ticker, day), self._CACHE_V)

    def _write_cache(self, ticker: str, day: str, payload: dict) -> None:
        _write_versioned_cache(self._cache_path(ticker, day), self._CACHE_V, payload)

    async def _begin(self, ticker: str,
                     ) -> tuple[SourceResult, TickerSnapshot, Optional[list[str]]]:
        """A fresh result whose partial is the returned snapshot, plus the names to
        query: the SEC title without its trailing qualifiers, then the configured
        aliases of every ticker with that title. ``res.raw["resolved_name"]`` keeps
        the SEC title. The names are None when the caller should return the result
        as is: the index failed to load (error recorded), the ticker is unknown, or
        its name is ambiguous (``raw`` says so)."""
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
        if ticker.upper() in self._ambiguous:
            res.raw["ambiguous_name"] = True
            return res, snap, None
        names = [strip_sec_qualifiers(title) or title]
        for tk, extra in self._aliases.items():
            if self._name_index.get(tk) == title:   # share classes share one title
                names.extend(extra)
        # Deduped by spelling, not by normalized form: upstream search is
        # spelling-sensitive, so "ExxonMobil" and "ExxonMobil Holdings Corp" are two queries.
        unique: dict[str, str] = {}
        for n in names:
            unique.setdefault(n.casefold(), n)
        return res, snap, list(unique.values())

    def _cached_model(self, ticker: str, day: str, model: type, key: str,
                      names: list[str]) -> tuple[Optional[dict], Any]:
        """``(payload, model(**payload[key]))`` from the day cache; the model is None
        for an unmatched payload. ``(None, None)`` on a miss. A payload fetched for
        other ``names`` is a miss, and so is one that fails to rebuild: the live path
        runs and, on success, overwrites it."""
        cached = self._read_cache(ticker, day)
        if cached is None or cached.get("names") != names:
            return None, None
        try:
            return cached, (model(**cached[key]) if cached.get("matched") else None)
        except Exception:
            return None, None
