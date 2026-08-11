from __future__ import annotations

import asyncio
from typing import Optional

from ..models import SocialSentiment, SourceResult, TickerSnapshot
from .base import Source


class WsbSource(Source):
    """Keyless WSB social mentions via ApeWisdom. Bulk-loads the top WSB tickers
    ONCE per run (the FinraSource fetch-once-reuse precedent), indexes by normalized
    symbol, and serves per-ticker lookups as O(1) dict hits. Disk-cached by fetch date.
    No API key, no config — cache_dir is shared across consumers."""

    name = "wsb"

    def __init__(self, timeout: float = 20.0, cache_dir: str = ".cache/apewisdom"):
        self._timeout = timeout
        self._cache_dir = cache_dir
        self._index: Optional[dict] = None
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # bulk load fires once, not per ticker

    async def _load(self) -> None:
        if self._index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_locked()

    async def _load_locked(self) -> None:
        if self._index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        from .. import apewisdom
        idx, err = await asyncio.to_thread(
            apewisdom.fetch_wsb_mentions, self._cache_dir, self._timeout)
        if err:
            self._load_error = err
            self._index = {}
        else:
            self._index = idx

    async def fetch(self, ticker: str) -> SourceResult:
        from .. import apewisdom
        res = SourceResult(source=self.name)
        await self._load()
        snap = TickerSnapshot(ticker=ticker)
        if self._load_error:
            res.errors.append(f"wsb: {self._load_error}")
            res.partial = snap
            return res
        wm = (self._index or {}).get(apewisdom.norm_symbol(ticker))
        if wm is not None:
            snap.social = SocialSentiment(
                as_of=wm.as_of, mentions=wm.mentions, mentions_24h_ago=wm.mentions_24h_ago,
                upvotes=wm.upvotes, rank=wm.rank, rank_24h_ago=wm.rank_24h_ago)
        res.raw = {"matched": wm is not None}
        res.partial = snap
        return res
