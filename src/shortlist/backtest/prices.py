"""Dated daily price history from Yahoo (keyless) and forward-return math.

Uses period1=0 epoch params for full daily history. NEVER range=max (it silently
degrades to quarterly bars). Parses timestamp PAIRED with adjclose so a null close
never desynchronizes dates from closes; also parses the UNADJUSTED quote[0].close
into an aligned `nominal_closes` series, so point-in-time market_cap/PE score off the
nominal price a live observer saw (not a retro split-adjusted one), while returns and
momentum keep using the adjusted `closes`.
"""
from __future__ import annotations

import calendar
import json
import math
from bisect import bisect_right
from dataclasses import dataclass, field
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


def parse_chart(raw: Any) -> tuple[list[date], list[float], list[Optional[float]]]:
    """Return (dates, closes, nominal_closes) PAIRED — drop a row only when its ADJUSTED
    close is non-numeric. `closes` are split/dividend-ADJUSTED (for returns/momentum);
    `nominal_closes` are the UNADJUSTED `quote[0].close` (for point-in-time market_cap/PE),
    aligned 1:1 to `dates`, with None where the unadjusted value is absent/non-numeric."""
    try:
        result = raw["chart"]["result"][0]
        ts = result["timestamp"]
        adj = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return [], [], []
    try:
        quote = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        quote = []
    dates: list[date] = []
    closes: list[float] = []
    nominal: list[Optional[float]] = []
    for i, (t, c) in enumerate(zip(ts, adj, strict=False)):
        if _is_finite_number(c) and _is_finite_number(t):
            dates.append(datetime.fromtimestamp(t, tz=timezone.utc).date())
            closes.append(float(c))
            q = quote[i] if i < len(quote) else None
            nominal.append(float(q) if _is_finite_number(q) else None)
    return dates, closes, nominal


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
    nominal_closes: list[Optional[float]] = field(default_factory=list)

    def _idx_asof(self, d: date) -> Optional[int]:
        """Index of the latest trading day with dates[i] <= d, else None."""
        i = bisect_right(self.dates, d) - 1
        return i if i >= 0 else None

    def close_asof(self, d: date) -> Optional[float]:
        i = self._idx_asof(d)
        return self.closes[i] if i is not None else None

    def nominal_close_asof(self, d: date) -> Optional[float]:
        """Latest UNADJUSTED close with dates[i] <= d — for point-in-time market_cap/PE
        (never adjusted, so a post-as_of split can't retro-shrink it). None if absent."""
        i = self._idx_asof(d)
        if i is None or i >= len(self.nominal_closes):
            return None
        return self.nominal_closes[i]

    def closes_through(self, d: date) -> list[float]:
        i = self._idx_asof(d)
        return self.closes[: i + 1] if i is not None else []

    def through(self, d: date) -> tuple[list[date], list[float]]:
        """(dates, closes) PAIRED and truncated to dates <= d — the dated counterpart
        of closes_through, for the date-aligned residual-momentum regression (§2)."""
        i = self._idx_asof(d)
        if i is None:
            return [], []
        return self.dates[: i + 1], self.closes[: i + 1]

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

    def nominal_price_on(self, target: date, tol_days: int = 5) -> Optional[float]:
        """Nearest-trading-day UNADJUSTED close within +/- tol_days (nominal counterpart
        of price_on, for the per-year pe_median_5y join)."""
        if not self.dates or not self.nominal_closes:
            return None
        anchor = bisect_right(self.dates, target)
        lo = max(0, anchor - tol_days - 2)
        hi = min(len(self.dates), anchor + tol_days + 2)
        best_i, best_gap = None, None
        for i in range(lo, hi):
            gap = abs((self.dates[i] - target).days)
            if gap <= tol_days and (best_gap is None or gap < best_gap):
                best_i, best_gap = i, gap
        if best_i is None or best_i >= len(self.nominal_closes):
            return None
        return self.nominal_closes[best_i]

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
    dates, closes, nominal = parse_chart(raw)
    return PriceHistory(symbol.upper(), dates, closes, nominal_closes=nominal)
