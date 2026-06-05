from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Optional

import requests

from .._util import first as _first
from ..cache import get_default_cache
from ..models import StockMetrics
from ..stats import cagr, gross_margin_stability, growth_persistence, median_pe
from . import _fmp_insider
from .base import Provider

BASE = "https://financialmodelingprep.com/stable"

# 429 backoff: honor a Retry-After header when present, else exponential from this
# base, capped here. Free tier is ~5 calls/min, so a second or two usually clears
# a per-minute burst; daily-quota 429s won't clear and are surfaced after the cap.
_RETRY_BASE_S = 1.0
_RETRY_MAX_S = 10.0


class FMPProvider(Provider):
    """Broadest single-source coverage: ratios, key metrics, price-target
    consensus, and analyst grades in a handful of calls. Treated as the primary
    fundamentals source.

    Uses FMP's `/stable/` API (the legacy `/v3`–`/v4` endpoints were retired for
    new keys on 2025-08-31). Every endpoint takes `?symbol=`."""

    name = "fmp"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 15,
        *,
        fetch_insider: bool = False,
        max_retries: int = 2,
        cache=None,
    ):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise RuntimeError("FMP_API_KEY not set")
        self.timeout = timeout
        # The insider-trading endpoint is paid (402 on free plans) and EDGAR is the
        # authoritative free insider source, so skip it by default — fetching it just
        # burns a guaranteed-to-fail request against the daily quota. Enable only on a
        # paid tier (config: fmp.fetch_insider).
        self.fetch_insider = fetch_insider
        self.max_retries = max_retries
        self._cache = cache
        self._session = requests.Session()
        self._spy_6m: Optional[float] = None

    def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key
        url = f"{BASE}/{path}"

        def fetch():
            for attempt in range(self.max_retries + 1):
                r = self._session.get(url, params=params, timeout=self.timeout)
                # 429 is a transient throttle; back off and retry a bounded number of
                # times. On the final attempt (or any non-429), let raise_for_status
                # surface the outcome so an exhausted daily quota isn't spun on forever.
                if r.status_code != 429 or attempt == self.max_retries:
                    r.raise_for_status()
                    return r.json()
                time.sleep(_retry_after_seconds(r, attempt))
            raise AssertionError("unreachable: the final attempt always returns or raises")

        cache = self._cache or get_default_cache()
        return cache.get_or_fetch("fmp", path, params, fetch)

    def _change_6m(self, ticker: str) -> Optional[float]:
        data = self._get("stock-price-change", symbol=ticker)
        if isinstance(data, list) and data:
            pct = data[0].get("6M")
            return pct / 100.0 if pct is not None else None  # API returns percent
        return None

    def fetch(self, ticker: str) -> StockMetrics:
        m = StockMetrics(ticker=ticker)
        errors: list[requests.HTTPError] = []

        def leg(call: Any) -> Any:
            """Run one fetch leg in isolation. An HTTP failure on a single endpoint
            (e.g. a 429 mid-fetch) is recorded but does not discard the legs that
            already succeeded; if EVERY leg fails, fetch() re-raises below so the
            coverage layer can classify the outcome instead of returning a blank card."""
            try:
                return call()
            except requests.HTTPError as e:
                errors.append(e)
                return None

        quote = _first(leg(lambda: self._get("quote", symbol=ticker)))
        if quote:
            m.name = quote.get("name")
            m.price = quote.get("price")
            m.market_cap = quote.get("marketCap")
            ma200 = quote.get("priceAvg200")
            if m.price and ma200:
                m.price_vs_200dma = m.price / ma200 - 1.0

        # PE and PEG now live in ratios-ttm (the /stable/ quote no longer carries pe).
        ratios = _first(leg(lambda: self._get("ratios-ttm", symbol=ticker)))
        if ratios:
            m.pe_ttm = ratios.get("priceToEarningsRatioTTM")
            m.gross_margin = ratios.get("grossProfitMarginTTM")
            m.net_margin = ratios.get("netProfitMarginTTM")
            m.debt_to_equity = ratios.get("debtToEquityRatioTTM")
            m.interest_coverage = ratios.get("interestCoverageRatioTTM")
            m.peg = ratios.get("priceToEarningsGrowthRatioTTM")

        # ROE/ROIC moved from ratios to key-metrics in the /stable/ API.
        km = _first(leg(lambda: self._get("key-metrics-ttm", symbol=ticker)))
        if km:
            m.roe = km.get("returnOnEquityTTM")
            m.roic = km.get("returnOnInvestedCapitalTTM")
            m.fcf_yield = km.get("freeCashFlowYieldTTM")

        # 5-year median PE for pe_vs_history() in value scoring.
        hist_ratios = leg(lambda: self._get("ratios", symbol=ticker, period="annual", limit=5))
        if isinstance(hist_ratios, list):
            m.pe_median_5y = median_pe([r.get("priceToEarningsRatio") for r in hist_ratios])

        # Margin stability + recent profitability from annual history (moat proxies).
        income = leg(lambda: self._get("income-statement", symbol=ticker, period="annual", limit=5))
        if isinstance(income, list) and len(income) >= 3:
            margins = [
                row["grossProfit"] / row["revenue"]
                for row in income
                if row.get("revenue")
            ]
            m.gross_margin_stability = gross_margin_stability(margins)
            m.fcf_positive = all(
                (row.get("netIncome") or 0) > 0 for row in income[:2]
            ) or None
            # Growth legs from the same annual history (no extra call). income is
            # newest-first. fcf_cagr needs the cash-flow statement we don't fetch
            # here (quota) -> left None; the scorer redistributes its weight.
            revenues = [row.get("revenue") for row in income]
            m.revenue_cagr = cagr(revenues)
            m.eps_cagr = cagr([row.get("netIncome") for row in income])
            m.revenue_growth_persistence = growth_persistence(revenues)
            # piotroski_f is intentionally NOT populated here: the Core-6 F-score
            # needs OCF (cash-flow statement) + balance-sheet debt, which the lean
            # screener path does not fetch (quota). It stays None and the value_trap
            # refinement abstains — an accepted screener-only gap, like fcf_cagr. The
            # default --engine harness + XBRL backtest populate it.

        consensus = _first(leg(lambda: self._get("price-target-consensus", symbol=ticker)))
        if consensus:
            m.target_median = consensus.get("targetMedian") or consensus.get("targetConsensus")

        grades = _first(leg(lambda: self._get("grades-consensus", symbol=ticker)))
        if grades:
            m.rating_buy = (grades.get("strongBuy") or 0) + (grades.get("buy") or 0)
            m.rating_hold = grades.get("hold")
            m.rating_sell = (grades.get("sell") or 0) + (grades.get("strongSell") or 0)

        with contextlib.suppress(Exception):
            m.rel_strength_6m = _rel_strength(self, ticker)

        # Insider transactions are a paid /stable/ endpoint (402 on free plans), so
        # this is opt-in (fmp.fetch_insider) — on the free tier EDGAR supplies insider
        # data and fetching here would only waste a guaranteed-402 call. Skip quietly
        # on failure even when enabled.
        if self.fetch_insider:
            try:
                insiders = self._get("insider-trading/search", symbol=ticker, page=0, limit=100)
                if isinstance(insiders, list) and insiders:
                    m.insider_net_6m = _fmp_insider.net_value(insiders)
            except Exception:
                pass

        tagged = self._tag(
            m, "name", "price", "market_cap", "pe_ttm", "pe_median_5y", "price_vs_200dma",
            "roe", "gross_margin", "net_margin", "debt_to_equity",
            "interest_coverage", "peg", "roic", "fcf_yield",
            "gross_margin_stability", "fcf_positive", "target_median",
            "revenue_cagr", "eps_cagr", "revenue_growth_persistence",
            "rating_buy", "rating_hold", "rating_sell", "rel_strength_6m",
            "insider_net_6m",
        )
        # If not a single leg yielded a field AND something failed, this symbol got
        # nothing usable from FMP — re-raise so the screener classifies it (gated 402,
        # rate-limited 429, …) rather than silently merging an empty card.
        if not m.sources and errors:
            raise _select_error(errors)
        return tagged


def _retry_after_seconds(response: Any, attempt: int) -> float:
    """How long to wait before retrying a 429: honor a numeric Retry-After header
    when FMP sets one, else exponential backoff capped at _RETRY_MAX_S."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _RETRY_MAX_S)
        except ValueError:
            pass
    return min(_RETRY_BASE_S * (2 ** attempt), _RETRY_MAX_S)


def _select_error(errors: list[requests.HTTPError]) -> requests.HTTPError:
    """When every leg failed, surface the most diagnostic error: a 402 (gating) or
    429 (rate limit) in preference to a generic failure, so the coverage layer can
    label the symbol's status precisely."""
    for status in (402, 429):
        for e in errors:
            if getattr(getattr(e, "response", None), "status_code", None) == status:
                return e
    return errors[0]


def _rel_strength(p: FMPProvider, ticker: str) -> Optional[float]:
    if p._spy_6m is None:
        # Cache the benchmark once per run. On failure (e.g. a 429) pin it to 0.0 so a
        # rate-limited SPY call isn't re-fired — with full retries — on every later
        # ticker in the batch; 0.0 makes rel-strength fall back to the stock's own 6m
        # change, matching the existing `or 0.0` missing-benchmark semantics.
        try:
            p._spy_6m = p._change_6m("SPY") or 0.0
        except requests.HTTPError:
            p._spy_6m = 0.0
    stock = p._change_6m(ticker)
    return stock - p._spy_6m if stock is not None else None
