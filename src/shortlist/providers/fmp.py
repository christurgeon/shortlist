from __future__ import annotations

import os
from typing import Any, Optional

import requests

from ..models import StockMetrics
from .base import Provider

BASE = "https://financialmodelingprep.com/stable"


class FMPProvider(Provider):
    """Broadest single-source coverage: ratios, key metrics, price-target
    consensus, and analyst grades in a handful of calls. Treated as the primary
    fundamentals source.

    Uses FMP's `/stable/` API (the legacy `/v3`–`/v4` endpoints were retired for
    new keys on 2025-08-31). Every endpoint takes `?symbol=`."""

    name = "fmp"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 15):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise RuntimeError("FMP_API_KEY not set")
        self.timeout = timeout
        self._spy_6m: Optional[float] = None

    def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key
        r = requests.get(f"{BASE}/{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _change_6m(self, ticker: str) -> Optional[float]:
        data = self._get("stock-price-change", symbol=ticker)
        if isinstance(data, list) and data:
            pct = data[0].get("6M")
            return pct / 100.0 if pct is not None else None  # API returns percent
        return None

    def fetch(self, ticker: str) -> StockMetrics:
        m = StockMetrics(ticker=ticker)

        quote = _first(self._get("quote", symbol=ticker))
        if quote:
            m.name = quote.get("name")
            m.price = quote.get("price")
            m.market_cap = quote.get("marketCap")
            ma200 = quote.get("priceAvg200")
            if m.price and ma200:
                m.price_vs_200dma = m.price / ma200 - 1.0

        # PE and PEG now live in ratios-ttm (the /stable/ quote no longer carries pe).
        ratios = _first(self._get("ratios-ttm", symbol=ticker))
        if ratios:
            m.pe_ttm = ratios.get("priceToEarningsRatioTTM")
            m.gross_margin = ratios.get("grossProfitMarginTTM")
            m.net_margin = ratios.get("netProfitMarginTTM")
            m.debt_to_equity = ratios.get("debtToEquityRatioTTM")
            m.interest_coverage = ratios.get("interestCoverageRatioTTM")
            m.peg = ratios.get("priceToEarningsGrowthRatioTTM")

        # ROE/ROIC moved from ratios to key-metrics in the /stable/ API.
        km = _first(self._get("key-metrics-ttm", symbol=ticker))
        if km:
            m.roe = km.get("returnOnEquityTTM")
            m.roic = km.get("returnOnInvestedCapitalTTM")
            m.fcf_yield = km.get("freeCashFlowYieldTTM")

        # Margin stability + recent profitability from annual history (moat proxies).
        income = self._get("income-statement", symbol=ticker, period="annual", limit=5)
        if isinstance(income, list) and len(income) >= 3:
            margins = [
                row["grossProfit"] / row["revenue"]
                for row in income
                if row.get("revenue")
            ]
            if len(margins) >= 3:
                avg = mean_(margins)
                m.gross_margin_stability = max(0.0, 1.0 - (stdev_(margins) / avg)) if avg else None
            m.fcf_positive = all(
                (row.get("netIncome") or 0) > 0 for row in income[:2]
            ) or None

        consensus = _first(self._get("price-target-consensus", symbol=ticker))
        if consensus:
            m.target_median = consensus.get("targetMedian") or consensus.get("targetConsensus")

        grades = _first(self._get("grades-consensus", symbol=ticker))
        if grades:
            m.rating_buy = (grades.get("strongBuy") or 0) + (grades.get("buy") or 0)
            m.rating_hold = grades.get("hold")
            m.rating_sell = (grades.get("sell") or 0) + (grades.get("strongSell") or 0)

        try:
            m.rel_strength_6m = _rel_strength(self, ticker)
        except Exception:
            pass

        # Insider transactions are a paid /stable/ endpoint (402 on free plans).
        # Skip quietly — EDGAR is the authoritative, free insider source.
        try:
            insiders = self._get("insider-trading/search", symbol=ticker, page=0, limit=100)
            if isinstance(insiders, list) and insiders:
                net = 0.0
                for tx in insiders[:60]:  # ~trailing window
                    val = (tx.get("securitiesTransacted") or 0) * (tx.get("price") or 0)
                    code = (tx.get("transactionType") or "").upper()
                    net += val if code.startswith("P") else -val  # P-purchase, S-sale
                m.insider_net_6m = net
        except Exception:
            pass

        return self._tag(
            m, "name", "price", "market_cap", "pe_ttm", "price_vs_200dma",
            "roe", "gross_margin", "net_margin", "debt_to_equity",
            "interest_coverage", "peg", "roic", "fcf_yield",
            "gross_margin_stability", "fcf_positive", "target_median",
            "rating_buy", "rating_hold", "rating_sell", "rel_strength_6m",
            "insider_net_6m",
        )


def _rel_strength(p: FMPProvider, ticker: str) -> Optional[float]:
    if p._spy_6m is None:
        p._spy_6m = p._change_6m("SPY") or 0.0
    stock = p._change_6m(ticker)
    return stock - p._spy_6m if stock is not None else None


def _first(data: Any) -> Optional[dict]:
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def mean_(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def stdev_(xs: list[float]) -> float:
    mu = mean_(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
