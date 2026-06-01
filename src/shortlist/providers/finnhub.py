from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Optional

import requests

from ..models import StockMetrics
from .base import Provider

BASE = "https://finnhub.io/api/v1"


class FinnhubProvider(Provider):
    """Complements FMP with two things it does better: a clean insider-sentiment
    signal (MSPR / monthly net share change) and recommendation-trend deltas that
    approximate analyst revision direction. Also a free real-time quote."""

    name = "finnhub"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 15):
        self.key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        self.timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        params["token"] = self.key
        r = requests.get(f"{BASE}/{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch(self, ticker: str) -> StockMetrics:
        m = StockMetrics(ticker=ticker)

        q = self._get("quote", symbol=ticker)
        if isinstance(q, dict) and q.get("c"):
            m.price = q["c"]

        metric = self._get("stock/metric", symbol=ticker, metric="all").get("metric", {})
        if metric:
            m.market_cap = _millions(metric.get("marketCapitalization"))
            m.roe = _pct(metric.get("roeTTM"))
            m.roic = _pct(metric.get("roiTTM"))
            m.gross_margin = _pct(metric.get("grossMarginTTM"))
            m.net_margin = _pct(metric.get("netProfitMarginTTM"))
            m.debt_to_equity = metric.get("totalDebt/totalEquityQuarterly")

        # Insider sentiment: aggregate MSPR over the trailing 6 months. Positive
        # MSPR = net buying; negative = net selling. Normalize to roughly -1..1.
        today = date.today()
        sent = self._get(
            "stock/insider-sentiment",
            symbol=ticker,
            **{"from": (today - timedelta(days=183)).isoformat(), "to": today.isoformat()},
        )
        rows = sent.get("data") if isinstance(sent, dict) else None
        if rows:
            msprs = [r.get("mspr") for r in rows if r.get("mspr") is not None]
            if msprs:
                m.insider_sentiment = max(-1.0, min(1.0, (sum(msprs) / len(msprs)) / 100.0))

        # Recommendation trend delta -> coarse proxy for revision direction.
        trend = self._get("stock/recommendation", symbol=ticker)
        if isinstance(trend, list) and trend:
            latest = trend[0]
            m.rating_buy = (latest.get("strongBuy") or 0) + (latest.get("buy") or 0)
            m.rating_hold = latest.get("hold")
            m.rating_sell = (latest.get("strongSell") or 0) + (latest.get("sell") or 0)
            if len(trend) >= 2:
                total = max(1, _rec_total(latest))
                m.eps_revision = (_rec_net(latest) - _rec_net(trend[1])) / total

        return self._tag(
            m, "price", "market_cap", "roe", "roic", "gross_margin", "net_margin",
            "debt_to_equity", "insider_sentiment", "rating_buy", "rating_hold",
            "rating_sell", "eps_revision",
        )


def _rec_net(row: dict) -> int:
    """Net bullishness of a recommendation-trend row (buys minus sells)."""
    return (row.get("strongBuy", 0) + row.get("buy", 0)
            - row.get("sell", 0) - row.get("strongSell", 0))


def _rec_total(row: dict) -> int:
    """Total analyst count across all recommendation buckets in a trend row."""
    return (row.get("strongBuy", 0) + row.get("buy", 0) + row.get("hold", 0)
            + row.get("sell", 0) + row.get("strongSell", 0))


def _pct(x: Optional[float]) -> Optional[float]:
    """Finnhub returns margins/returns as percentages; convert to fractions."""
    return x / 100.0 if x is not None else None


def _millions(x: Optional[float]) -> Optional[float]:
    """Finnhub reports market cap in millions of USD; store absolute dollars to
    match FMP's quote.marketCap (the gate and net-insider ratio expect dollars)."""
    return x * 1.0e6 if x is not None else None
