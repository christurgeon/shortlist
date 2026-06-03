"""Dated daily price history from Yahoo (keyless) and forward-return math.

Uses period1=0 epoch params for full daily history. NEVER range=max (it silently
degrades to quarterly bars). Parses timestamp PAIRED with adjclose so a null close
never desynchronizes dates from closes.
"""
from __future__ import annotations

import calendar
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) shortlist/0.1"


def _is_finite_number(x: Any) -> bool:
    """True for a real, finite int/float. Excludes bool (isinstance(True, int)) and
    NaN/Inf (json allows them) — a NaN close would survive `is not None` and
    silently corrupt the IC."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def parse_chart(raw: Any) -> tuple[list[date], list[float]]:
    """Return (dates, closes) PAIRED — drop a pair only when its close is non-numeric."""
    try:
        result = raw["chart"]["result"][0]
        ts = result["timestamp"]
        adj = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return [], []
    dates: list[date] = []
    closes: list[float] = []
    for t, c in zip(ts, adj):
        if _is_finite_number(c) and _is_finite_number(t):
            dates.append(datetime.fromtimestamp(t, tz=timezone.utc).date())
            closes.append(float(c))
    return dates, closes


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


@dataclass
class PriceHistory:
    ticker: str
    dates: list[date]          # ascending, aligned with closes
    closes: list[float]

    def _idx_asof(self, d: date) -> Optional[int]:
        """Index of the latest trading day with dates[i] <= d, else None."""
        i = bisect_right(self.dates, d) - 1
        return i if i >= 0 else None

    def close_asof(self, d: date) -> Optional[float]:
        i = self._idx_asof(d)
        return self.closes[i] if i is not None else None

    def closes_through(self, d: date) -> list[float]:
        i = self._idx_asof(d)
        return self.closes[: i + 1] if i is not None else []

    def price_on(self, target: date, tol_days: int = 5) -> Optional[float]:
        """Close on the nearest trading day to target, within +/- tol_days."""
        if not self.dates:
            return None
        anchor = bisect_right(self.dates, target)
        lo = max(0, anchor - tol_days - 2)
        hi = min(len(self.dates), anchor + tol_days + 2)
        best_i, best_gap = None, None
        for i in range(lo, hi):
            gap = abs((self.dates[i] - target).days)
            if gap <= tol_days and (best_gap is None or gap < best_gap):
                best_i, best_gap = i, gap
        return self.closes[best_i] if best_i is not None else None

    def forward_return(self, t: date, horizon_months: int,
                       tol_days: int = 5) -> Optional[float]:
        """Total return from close_asof(t) to the nearest trading day at
        t + horizon_months calendar months. None if either leg is unavailable
        (e.g. past series end) — never imputed."""
        start = self.close_asof(t)
        if start is None or start <= 0:
            return None
        target = _add_months(t, horizon_months)
        end = self.price_on(target, tol_days=tol_days)
        if end is None:
            return None
        return end / start - 1.0


def _cache_path(cache_dir: str, symbol: str, today: str) -> Path:
    return Path(cache_dir) / f"{symbol.upper()}-fullhist-{today}.json"


async def fetch_history(symbol: str, client, *, cache_dir: str, today: str,
                        period1: int = 0) -> PriceHistory:
    """Fetch full daily history (period1=0) with day-caching under a DISTINCT
    filename so it never collides with the 2y harness cache. `client` is an
    httpx.AsyncClient. `today` pins the fetch as-of for reproducibility."""
    path = _cache_path(cache_dir, symbol, today)
    raw: Any = None
    if path.exists():
        try:
            raw = json.loads(path.read_text())
        except (ValueError, OSError):
            raw = None
    if raw is None:
        now = int(datetime.now(tz=timezone.utc).timestamp())
        resp = await client.get(
            _BASE.format(sym=symbol),
            params={"period1": period1, "period2": now, "interval": "1d"},
            headers={"User-Agent": _UA},
            timeout=30.0,
        )
        resp.raise_for_status()
        raw = resp.json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
    dates, closes = parse_chart(raw)
    return PriceHistory(symbol.upper(), dates, closes)
