from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from ..diskcache import read_json_cache, write_json_cache
from ..models import SourceResult, TickerSnapshot
from .base import Source
from .yahoo_prices import (
    _closes_from_chart,
    _dates_from_chart,
    _monthly_closes_from_chart,
    _normalize_yahoo,
)

# --- Yahoo: keyless OHLCV -> we compute momentum/risk ourselves ------------

class YahooSource(Source):
    """Keyless Yahoo chart OHLCV. Computes momentum/risk (rel strength vs SPY,
    realized vol, max drawdown, 200dma) ourselves so the signals are auditable
    and immune to FMP's per-symbol gating. Day-cached on disk; the SPY benchmark
    is fetched once per run and reused across tickers."""

    name = "yahoo"
    BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) shortlist/0.1"

    def __init__(self, timeout: float = 15.0, cache_dir: str = ".cache/yahoo"):
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": self.UA})
        self._cache_dir = Path(cache_dir)
        self._spy_closes: Optional[list[float]] = None
        self._spy_dates: Optional[list[date]] = None
        # Guards the load-once SPY fetch: without it ~8 concurrent cold-cache
        # tickers would each fire the full SPY chart request (thundering herd).
        self._load_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol.upper()}-{date.today().isoformat()}.json"

    async def _get_chart(self, symbol: str) -> Any:
        """Raw chart payload, day-cached on disk. Override target in tests."""
        cp = self._cache_path(symbol)
        cached = read_json_cache(cp)
        if cached is not None:
            return cached
        r = await self._client.get(
            f"{self.BASE}/{symbol}", params={"range": "5y", "interval": "1d"})
        r.raise_for_status()
        raw = r.json()
        write_json_cache(cp, raw)
        return raw

    async def _closes(self, symbol: str) -> list[float]:
        return _closes_from_chart(await self._get_chart(symbol))

    async def _spy(self) -> list[float]:
        """SPY closes, fetched once per run. Also populates `_spy_dates` from the same
        payload so the residual-momentum leg can date-inner-join stock vs SPY."""
        if self._spy_closes is None:
            async with self._load_lock:
                if self._spy_closes is None:   # re-check: another task may have loaded
                    raw = await self._get_chart("SPY")
                    self._spy_dates = _dates_from_chart(raw)
                    self._spy_closes = _closes_from_chart(raw)
        return self._spy_closes

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        try:
            raw = await self._get_chart(ticker)
            closes = _closes_from_chart(raw)
            spy = await self._spy()
            # Plumb the date-aligned stock + SPY series so residual_momentum computes on
            # the live path (PREDICTIVE_SIGNALS §2). _dates_from_chart drops to [] on a
            # date-less / misaligned payload; _normalize_yahoo then leaves residual_momentum
            # None (the leg abstains) rather than crashing the screen.
            dates = _dates_from_chart(raw)
            spy_dates = self._spy_dates
            if not dates or not spy_dates:
                dates = spy_dates = None  # fall back to the date-less (None-residual) path
            res.partial = _normalize_yahoo(ticker, closes, spy, dates, spy_dates)
            if res.partial.price is not None:
                res.partial.price.monthly_closes = _monthly_closes_from_chart(raw)
            res.raw = {"close_count": len(closes)}
        except Exception as e:
            res.errors.append(f"yahoo: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
        return res
