from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from abc import ABC, abstractmethod
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from ..env import redact_secrets
from ..stats import avg_roic, median_pe
from .models import (
    Analyst, Fundamentals, Insider, InsiderTxn, Price, Profile,
    SourceResult, Statements, TickerSnapshot,
)


class Source(ABC):
    """Fetches everything a source can offer for one ticker, returning both the
    verbatim raw payloads (for point-in-time audit) and a normalized partial."""

    name: str = "base"

    @abstractmethod
    async def fetch(self, ticker: str) -> SourceResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


# --- FMP: primary, broad coverage ----------------------------------------

class FMPSource(Source):
    name = "fmp"
    # FMP's `/stable/` API; the legacy `/v3`–`/v4` endpoints were retired for
    # new keys on 2025-08-31. Every endpoint takes `?symbol=`.
    BASE = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise RuntimeError("FMP_API_KEY not set")
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key
        r = await self._client.get(f"{self.BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        # Define every section we want and how to fetch it; capture raw verbatim.
        sections = {
            "profile": ("profile", {"symbol": ticker}),
            "quote": ("quote", {"symbol": ticker}),
            "ratios_ttm": ("ratios-ttm", {"symbol": ticker}),
            "ratios_annual": ("ratios", {"symbol": ticker, "period": "annual", "limit": 5}),
            "key_metrics_ttm": ("key-metrics-ttm", {"symbol": ticker}),
            "key_metrics_annual": ("key-metrics", {"symbol": ticker, "period": "annual", "limit": 5}),
            "income": ("income-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "balance": ("balance-sheet-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "cashflow": ("cash-flow-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "price_target": ("price-target-consensus", {"symbol": ticker}),
            "grades": ("grades-consensus", {"symbol": ticker}),
            "insider": ("insider-trading/search", {"symbol": ticker, "page": 0, "limit": 100}),
            "price_change": ("stock-price-change", {"symbol": ticker}),
        }
        for name, (path, params) in sections.items():
            try:
                res.raw[name] = await self._get(path, **params)
            except Exception as e:
                res.errors.append(f"fmp.{name}: {redact_secrets(e)}")
        res.partial = _normalize_fmp(ticker, res.raw)
        return res


def _normalize_fmp(ticker: str, raw: dict[str, Any]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    p = _first(raw.get("profile"))
    if p:
        snap.profile = Profile(
            name=p.get("companyName"), sector=p.get("sector"),
            industry=p.get("industry"), exchange=p.get("exchange"),
            currency=p.get("currency"), country=p.get("country"),
            market_cap=p.get("marketCap"), beta=p.get("beta"),
            description=p.get("description"),
        )
    # PE/PEG come from ratios and ROE/ROIC from key-metrics in the /stable/ API.
    ratios = _first(raw.get("ratios_ttm"))
    km = _first(raw.get("key_metrics_ttm"))
    if ratios or km:
        ratios_hist = raw.get("ratios_annual")
        km_hist = raw.get("key_metrics_annual")
        snap.fundamentals = Fundamentals(
            pe_ttm=(ratios or {}).get("priceToEarningsRatioTTM"),
            pe_median_5y=median_pe(
                [r.get("priceToEarningsRatio") for r in ratios_hist]
            ) if isinstance(ratios_hist, list) else None,
            peg=(ratios or {}).get("priceToEarningsGrowthRatioTTM"),
            roe=(km or {}).get("returnOnEquityTTM"),
            roic=(km or {}).get("returnOnInvestedCapitalTTM"),
            roic_5y_avg=avg_roic(
                [r.get("returnOnInvestedCapital") for r in km_hist]
            ) if isinstance(km_hist, list) else None,
            gross_margin=(ratios or {}).get("grossProfitMarginTTM"),
            net_margin=(ratios or {}).get("netProfitMarginTTM"),
            operating_margin=(ratios or {}).get("operatingProfitMarginTTM"),
            debt_to_equity=(ratios or {}).get("debtToEquityRatioTTM"),
            interest_coverage=(ratios or {}).get("interestCoverageRatioTTM"),
            current_ratio=(ratios or {}).get("currentRatioTTM"),
            fcf_yield=(km or {}).get("freeCashFlowYieldTTM"),
        )
    inc, bal, cf = raw.get("income"), raw.get("balance"), raw.get("cashflow")
    if isinstance(inc, list) and inc:
        snap.statements = Statements(
            fiscal_years=[_year(r.get("date")) for r in inc],
            revenue=[r.get("revenue") for r in inc],
            gross_profit=[r.get("grossProfit") for r in inc],
            net_income=[r.get("netIncome") for r in inc],
            operating_cash_flow=[(_match(cf, r) or {}).get("operatingCashFlow") for r in inc],
            free_cash_flow=[(_match(cf, r) or {}).get("freeCashFlow") for r in inc],
            total_debt=[(_match(bal, r) or {}).get("totalDebt") for r in inc],
            total_equity=[(_match(bal, r) or {}).get("totalStockholdersEquity") for r in inc],
        )
    pt = _first(raw.get("price_target"))
    grades = _first(raw.get("grades"))
    if pt or grades:
        snap.analyst = Analyst(
            target_median=(pt or {}).get("targetMedian") or (pt or {}).get("targetConsensus"),
            target_high=(pt or {}).get("targetHigh"),
            target_low=(pt or {}).get("targetLow"),
            buy=((grades or {}).get("strongBuy") or 0) + ((grades or {}).get("buy") or 0) or None,
            hold=(grades or {}).get("hold"),
            sell=((grades or {}).get("sell") or 0) + ((grades or {}).get("strongSell") or 0) or None,
            consensus=(grades or {}).get("consensus"),
        )
    q = _first(raw.get("quote")) or {}
    chg = _first(raw.get("price_change")) or {}
    if q or chg:
        snap.price = Price(
            price=q.get("price"), ma50=q.get("priceAvg50"), ma200=q.get("priceAvg200"),
            year_high=q.get("yearHigh"), year_low=q.get("yearLow"),
            ret_1m=_pct(chg.get("1M")), ret_3m=_pct(chg.get("3M")),
            ret_6m=_pct(chg.get("6M")), ret_12m=_pct(chg.get("1Y")),
        )
    insiders = raw.get("insider")
    if isinstance(insiders, list) and insiders:
        net = buys = sells = 0
        recent = []
        for tx in insiders[:60]:
            val = (tx.get("securitiesTransacted") or 0) * (tx.get("price") or 0)
            is_buy = (tx.get("transactionType") or "").upper().startswith("P")
            net += val if is_buy else -val
            buys += is_buy
            sells += not is_buy
            if len(recent) < 10:
                recent.append(InsiderTxn(
                    date=tx.get("transactionDate"), name=tx.get("reportingName"),
                    role=tx.get("typeOfOwner"), kind="buy" if is_buy else "sell",
                    shares=tx.get("securitiesTransacted"), price=tx.get("price"), value=val,
                ))
        snap.insider = Insider(net_value_6m=net, buy_count=buys, sell_count=sells, recent=recent)
    return snap


# --- Finnhub: complements with insider sentiment + recommendation trend ----

class FinnhubSource(Source):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
        self.key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        import httpx
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        params["token"] = self.key
        r = await self._client.get(f"{self.BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        today = date.today()
        calls = {
            "profile": ("stock/profile2", {"symbol": ticker}),
            "quote": ("quote", {"symbol": ticker}),
            "metric": ("stock/metric", {"symbol": ticker, "metric": "all"}),
            "recommendation": ("stock/recommendation", {"symbol": ticker}),
            "insider_sentiment": ("stock/insider-sentiment", {
                "symbol": ticker,
                "from": (today - timedelta(days=183)).isoformat(), "to": today.isoformat()}),
        }
        for name, (path, params) in calls.items():
            try:
                res.raw[name] = await self._get(path, **params)
            except Exception as e:
                res.errors.append(f"finnhub.{name}: {redact_secrets(e)}")
        res.partial = _normalize_finnhub(ticker, res.raw)
        return res


def _normalize_finnhub(ticker: str, raw: dict[str, Any]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    p = raw.get("profile") or {}
    if p:
        snap.profile = Profile(
            name=p.get("name"), industry=p.get("finnhubIndustry"),
            exchange=p.get("exchange"), currency=p.get("currency"),
            country=p.get("country"), market_cap=_mm(p.get("marketCapitalization")),
        )
    m = (raw.get("metric") or {}).get("metric", {})
    if m:
        snap.fundamentals = Fundamentals(
            roe=_pct(m.get("roeTTM")), roic=_pct(m.get("roiTTM")),
            gross_margin=_pct(m.get("grossMarginTTM")),
            net_margin=_pct(m.get("netProfitMarginTTM")),
            debt_to_equity=m.get("totalDebt/totalEquityQuarterly"),
            current_ratio=m.get("currentRatioQuarterly"),
        )
    rows = (raw.get("insider_sentiment") or {}).get("data") or []
    msprs = [r.get("mspr") for r in rows if r.get("mspr") is not None]
    if msprs:
        snap.insider = Insider(sentiment_mspr=max(-1.0, min(1.0, (sum(msprs) / len(msprs)) / 100.0)))
    trend = raw.get("recommendation")
    if isinstance(trend, list) and trend:
        t = trend[0]
        snap.analyst = Analyst(
            buy=(t.get("strongBuy") or 0) + (t.get("buy") or 0),
            hold=t.get("hold"),
            sell=(t.get("sell") or 0) + (t.get("strongSell") or 0),
        )
    q = raw.get("quote") or {}
    if q.get("c"):
        snap.price = Price(price=q["c"])
    return snap


# --- SEC EDGAR: authoritative insider Form 4 -------------------------------

# SEC enforces ~10 req/s fair-access per IP, and each ticker pulls many filings.
# The collector's per-ticker semaphore doesn't bound SEC request *rate* (EDGAR's
# calls happen inside a worker thread, invisible to it), so all EdgarSource work
# is funnelled through this shared gate — well under the limit. Re-created if the
# event loop changes (e.g. a second collect() call) to stay loop-bound-safe.
_EDGAR_MAX_CONCURRENCY = 3
_edgar_gate: dict = {}


def _edgar_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _edgar_gate.get("loop") is not loop:
        _edgar_gate.update(loop=loop, sem=asyncio.Semaphore(_EDGAR_MAX_CONCURRENCY))
    return _edgar_gate["sem"]


class EdgarSource(Source):
    """Authoritative SEC Form 4 insider data. Free, but the blocking `edgartools`
    work runs in a worker thread (the harness is async) and is rate-limited via a
    shared semaphore. Supplies only the insider transaction facts — `sentiment_mspr`
    is Finnhub's signal and is composed in by the custom insider merger."""

    name = "edgar"

    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        from edgar import set_identity  # lazy: edgartools is an optional dep
        set_identity(self.identity)  # process-global mutable state — set once, here

    async def fetch(self, ticker: str) -> SourceResult:
        async with _edgar_semaphore():
            return await asyncio.to_thread(self._fetch_sync, ticker)

    def _fetch_sync(self, ticker: str) -> SourceResult:
        from edgar import Company
        from ..providers._form4 import aggregate_form4

        res = SourceResult(source=self.name)
        cutoff = date.today() - timedelta(days=self.lookback_days)
        try:
            summary = aggregate_form4(
                Company(ticker).get_filings(form="4").latest(40), cutoff)
        except Exception as e:
            res.errors.append(f"edgar: {e}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res

        if summary.found:
            res.raw = {"form4_trades": [dataclasses.asdict(t) for t in summary.txns]}
            res.partial = TickerSnapshot(ticker=ticker, insider=Insider(
                net_value_6m=summary.net_value,
                buy_count=summary.buy_count,
                sell_count=summary.sell_count,
                recent=[InsiderTxn(
                    date=t.date, name=t.name, role=t.role, kind=t.kind,
                    shares=t.shares, price=t.price, value=t.value,
                ) for t in summary.txns[:10]],
            ))
        else:
            res.partial = TickerSnapshot(ticker=ticker)
        return res


# --- Mock: offline demo (illustrative, not verified) ----------------------

class MockSource(Source):
    name = "mock"

    async def fetch(self, ticker: str) -> SourceResult:
        from .mockdata import SAMPLE
        data = SAMPLE.get(ticker.upper())
        res = SourceResult(source=self.name)
        if not data:
            res.errors.append(f"mock: no sample for {ticker}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res
        res.raw = {"sample": data["raw_echo"]}
        res.partial = data["snapshot"](ticker)
        return res


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

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol.upper()}-{date.today().isoformat()}.json"

    async def _get_chart(self, symbol: str) -> Any:
        """Raw chart payload, day-cached on disk. Override target in tests."""
        cp = self._cache_path(symbol)
        try:
            if cp.exists():
                return json.loads(cp.read_text())
        except Exception:
            pass  # corrupt cache -> refetch
        r = await self._client.get(
            f"{self.BASE}/{symbol}", params={"range": "2y", "interval": "1d"})
        r.raise_for_status()
        raw = r.json()
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(raw))
        except Exception:
            pass  # cache write failure is non-fatal
        return raw

    async def _closes(self, symbol: str) -> list[float]:
        return _closes_from_chart(await self._get_chart(symbol))

    async def _spy(self) -> list[float]:
        if self._spy_closes is None:
            self._spy_closes = await self._closes("SPY")
        return self._spy_closes

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        try:
            closes = await self._closes(ticker)
            spy = await self._spy()
            res.partial = _normalize_yahoo(ticker, closes, spy)
            res.raw = {"close_count": len(closes)}
        except Exception as e:
            res.errors.append(f"yahoo: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
        return res


# --- helpers --------------------------------------------------------------

def _first(x: Any) -> Optional[dict]:
    if isinstance(x, list) and x:
        return x[0]
    if isinstance(x, dict):
        return x
    return None


def _pct(x: Any) -> Optional[float]:
    return x / 100.0 if isinstance(x, (int, float)) else None


def _mm(x: Any) -> Optional[float]:
    return x * 1e6 if isinstance(x, (int, float)) else None  # Finnhub mktcap is in $M


def _year(d: Any) -> Optional[int]:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


def _match(rows: Any, income_row: dict) -> Optional[dict]:
    if not isinstance(rows, list):
        return None
    for r in rows:
        if r.get("date") == income_row.get("date"):
            return r
    return None


# --- Yahoo price math (pure, unit-tested) ---------------------------------

_YH_SIX_MONTHS = 126   # ~trading days in 6 months
_YH_VOL_WINDOW = 252   # ~trading days in 1 year


def _yh_sma(xs: list[float], n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _yh_ret_over(xs: list[float], n: int) -> Optional[float]:
    return xs[-1] / xs[-1 - n] - 1.0 if len(xs) > n and xs[-1 - n] else None


def _yh_annualized_vol(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    rets = [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs)) if xs[i - 1]][-window:]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5)


def _yh_max_drawdown(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    s = xs[-window:]
    if len(s) < 2:
        return None
    peak = s[0]
    mdd = 0.0
    for px in s:
        peak = max(peak, px)
        if peak:
            mdd = min(mdd, px / peak - 1.0)
    return mdd


def _closes_from_chart(raw: Any) -> list[float]:
    try:
        result = raw["chart"]["result"][0]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    return [c for c in series if isinstance(c, (int, float))]


def _normalize_yahoo(ticker: str, closes: list[float], spy_closes: list[float]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    if not closes:
        return snap
    stock_6m = _yh_ret_over(closes, _YH_SIX_MONTHS)
    spy_6m = _yh_ret_over(spy_closes, _YH_SIX_MONTHS) if spy_closes else None
    rel = stock_6m - spy_6m if (stock_6m is not None and spy_6m is not None) else None
    snap.price = Price(
        price=closes[-1],
        ma200=_yh_sma(closes, 200),
        ret_6m=stock_6m,
        rel_strength_6m=rel,
        realized_vol=_yh_annualized_vol(closes),
        max_drawdown=_yh_max_drawdown(closes),
    )
    return snap


def snapshot_from_closes(ticker: str, closes: list[float],
                         spy_closes: list[float]) -> TickerSnapshot:
    """Public seam: build a point-in-time Price snapshot from a close series,
    delegating to the same math the live Yahoo source uses. Pass closes already
    truncated to the as-of date for a look-ahead-free reconstruction."""
    return _normalize_yahoo(ticker, closes, spy_closes)


_REGISTRY = {
    "yahoo": YahooSource,
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource, "mock": MockSource,
}


def build_sources(names: list[str]) -> list[Source]:
    out, skipped = [], []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown source '{n}'. Known: {list(_REGISTRY)}")
        try:
            out.append(_REGISTRY[n]())
        except Exception as e:
            skipped.append(f"{n} ({e})")
    if skipped:
        print(f"  ! skipped sources: {', '.join(skipped)}")
    return out
