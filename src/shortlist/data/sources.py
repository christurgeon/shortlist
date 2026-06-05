from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import os
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .._util import first as _first
from .._util import from_millions as _mm
from .._util import pct as _pct
from ..cache import get_default_cache
from ..env import redact_secrets
from ..providers._fmp_insider import is_buy, tx_value
from ..stats import avg_roic, median_pe
from .models import (
    Analyst,
    Events,
    FilingEvent,
    Fundamentals,
    Insider,
    InsiderTxn,
    Price,
    Profile,
    ShortInterest,
    SourceResult,
    Statements,
    TickerSnapshot,
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

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *, cache=None):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise RuntimeError("FMP_API_KEY not set")
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = cache

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key

        async def fetch():
            r = await self._client.get(f"{self.BASE}/{path}", params=params)
            r.raise_for_status()
            return r.json()

        cache = self._cache or get_default_cache()
        return await cache.aget_or_fetch("fmp", path, params, fetch)

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
        await _fetch_sections(res, self._get, sections)
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
        ratios, km = ratios or {}, km or {}
        ratios_hist = raw.get("ratios_annual")
        km_hist = raw.get("key_metrics_annual")
        snap.fundamentals = Fundamentals(
            pe_ttm=ratios.get("priceToEarningsRatioTTM"),
            pe_median_5y=median_pe(
                [r.get("priceToEarningsRatio") for r in ratios_hist]
            ) if isinstance(ratios_hist, list) else None,
            peg=ratios.get("priceToEarningsGrowthRatioTTM"),
            roe=km.get("returnOnEquityTTM"),
            roic=km.get("returnOnInvestedCapitalTTM"),
            roic_5y_avg=avg_roic(
                [r.get("returnOnInvestedCapital") for r in km_hist]
            ) if isinstance(km_hist, list) else None,
            gross_margin=ratios.get("grossProfitMarginTTM"),
            net_margin=ratios.get("netProfitMarginTTM"),
            operating_margin=ratios.get("operatingProfitMarginTTM"),
            debt_to_equity=ratios.get("debtToEquityRatioTTM"),
            interest_coverage=ratios.get("interestCoverageRatioTTM"),
            current_ratio=ratios.get("currentRatioTTM"),
            fcf_yield=km.get("freeCashFlowYieldTTM"),
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
        pt, grades = pt or {}, grades or {}
        snap.analyst = Analyst(
            target_median=pt.get("targetMedian") or pt.get("targetConsensus"),
            target_high=pt.get("targetHigh"),
            target_low=pt.get("targetLow"),
            buy=(grades.get("strongBuy") or 0) + (grades.get("buy") or 0) or None,
            hold=grades.get("hold"),
            sell=(grades.get("sell") or 0) + (grades.get("strongSell") or 0) or None,
            consensus=grades.get("consensus"),
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
            val = tx_value(tx)
            buy = is_buy(tx)
            net += val if buy else -val
            buys += buy
            sells += not buy
            if len(recent) < 10:
                recent.append(InsiderTxn(
                    date=tx.get("transactionDate"), name=tx.get("reportingName"),
                    role=tx.get("typeOfOwner"), kind="buy" if buy else "sell",
                    shares=tx.get("securitiesTransacted"), price=tx.get("price"), value=val,
                ))
        snap.insider = Insider(net_value_6m=net, buy_count=buys, sell_count=sells, recent=recent)
    return snap


# --- Finnhub: complements with insider sentiment + recommendation trend ----

class FinnhubSource(Source):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *, cache=None):
        self.key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        import httpx
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = cache

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        params["token"] = self.key

        async def fetch():
            r = await self._client.get(f"{self.BASE}/{path}", params=params)
            r.raise_for_status()
            return r.json()

        cache = self._cache or get_default_cache()
        return await cache.aget_or_fetch("finnhub", path, params, fetch)

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
        await _fetch_sections(res, self._get, calls)
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


# form prefix (upper) -> Events boolean attribute. Prefix match absorbs /A amendments;
# both the "SC 13x" and SEC's "SCHEDULE 13x" spellings are handled (exact-match fetch
# can return either; see spec §3.1).
_EVENT_FORM_PREFIXES = (
    ("SC 13D", "activist_13d"), ("SCHEDULE 13D", "activist_13d"),
    ("SC 13G", "passive_13g"), ("SCHEDULE 13G", "passive_13g"),
    ("8-K", "recent_8k"),
    ("144", "planned_insider_sale_144"),
)


def classify_event_form(form: str) -> Optional[str]:
    """Map a filing form string to its Events flag attribute, or None if not an
    event form. Case-insensitive prefix match (captures /A amendments)."""
    f = (form or "").strip().upper()
    for prefix, attr in _EVENT_FORM_PREFIXES:
        if f.startswith(prefix):
            return attr
    return None


def build_events_section(records: list[dict], lookback_days: int,
                         today: date) -> Optional[Events]:
    """Pure: filter records to the lookback window, classify, and build an Events.
    Returns None when there are no in-window event filings — NEVER an all-falsy
    Events (load-bearing for the merge's _has_data check; spec §4)."""
    cutoff = today - timedelta(days=lookback_days)
    kept: list[tuple[str, FilingEvent]] = []
    for r in records:
        attr = classify_event_form(r.get("form", ""))
        if attr is None:
            continue
        filed = r.get("filed")
        try:
            if date.fromisoformat(filed) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        kept.append((attr, FilingEvent(
            form=r.get("form", ""), filed=filed,
            accession=r.get("accession"), url=r.get("url"))))
    if not kept:
        return None
    kept.sort(key=lambda p: p[1].filed, reverse=True)   # newest-first
    ev = Events(recent=[fe for _, fe in kept])
    for attr, _ in kept:
        setattr(ev, attr, True)
    return ev


class EdgarSource(Source):
    """Authoritative SEC Form 4 insider data plus annual financials. Free, but the
    blocking `edgartools` work runs in a worker thread (the harness is async) and
    is rate-limited via a shared semaphore. `sentiment_mspr` is Finnhub's signal
    and is composed in by the custom insider merger. Financials failures are
    isolated — they never drop a successfully fetched insider result."""

    name = "edgar"

    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183,
                 config: Optional[dict] = None):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        ev = (config or {}).get("edgar_events", {})
        self._event_forms = ev.get(
            "forms", ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G"])
        self._event_lookback_days = ev.get("lookback_days", 90)
        self._index_limit = ev.get("index_limit", 40)
        self._conviction = ((config or {}).get("insider") or {}).get("conviction")
        from edgar import set_identity  # lazy: edgartools is an optional dep
        set_identity(self.identity)  # process-global mutable state — set once, here

    async def fetch(self, ticker: str) -> SourceResult:
        async with _edgar_semaphore():
            return await asyncio.to_thread(self._fetch_sync, ticker)

    def _fetch_insider(self, ticker: str) -> SourceResult:
        """Fetch Form 4 insider data. Always returns a SourceResult with a
        non-None res.partial (on all branches, including the except branch)."""
        from edgar import Company

        from ..providers._form4 import aggregate_form4

        res = SourceResult(source=self.name)
        cutoff = date.today() - timedelta(days=self.lookback_days)
        try:
            summary = aggregate_form4(
                Company(ticker).get_filings(form="4").latest(40), cutoff, self._conviction)
        except Exception as e:
            res.errors.append(f"edgar: {e}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res

        if summary.found:
            res.raw = {"form4_trades": [dataclasses.asdict(t) for t in summary.txns]}
            ins = Insider(
                net_value_6m=summary.net_value,
                buy_count=summary.buy_count,
                sell_count=summary.sell_count,
                recent=[InsiderTxn(
                    date=t.date, name=t.name, role=t.role, kind=t.kind,
                    shares=t.shares, price=t.price, value=t.value,
                ) for t in summary.txns[:10]],
            )
            if self._conviction is not None:
                ins.distinct_buyers = summary.distinct_buyers
                ins.role_weighted_buy_value = summary.role_weighted_buy_value
                ins.planned_sell_value = summary.planned_sell_value
            res.partial = TickerSnapshot(ticker=ticker, insider=ins)
        else:
            res.partial = TickerSnapshot(ticker=ticker)
        return res

    def _fetch_financials_object(self, ticker: str):
        """Seam for mocking: returns an edgartools Financials (or raises)."""
        from edgar import Company
        return Company(ticker).get_financials()

    def _build_financials_snapshot(self, ticker: str, fin) -> TickerSnapshot:
        """Map an edgartools Financials onto a Statements-only snapshot. Pure given
        `fin`. Values are absolute USD (no scaling)."""
        from ..providers._edgar_facts import extract_financials
        try:
            shares = fin.get_shares_outstanding_diluted()
        except Exception:
            shares = None
        ef = extract_financials(
            fin.income_statement().to_dataframe(),
            fin.cashflow_statement().to_dataframe(),
            shares_diluted=shares,
        )
        snap = TickerSnapshot(ticker=ticker)
        if ef.fiscal_period_end:
            snap.statements = Statements(
                fiscal_years=[int(d[:4]) for d in ef.fiscal_period_end],
                fiscal_period_end=ef.fiscal_period_end,
                revenue=ef.revenue,
                net_income=ef.net_income,
                operating_cash_flow=ef.operating_cash_flow,
                free_cash_flow=ef.free_cash_flow,
                diluted_eps=ef.diluted_eps,
            )
        # gross_profit/total_debt/total_equity aren't in EdgarFinancials; the merge layer fills them from FMP when available.
        return snap

    def _fetch_sic(self, ticker: str) -> Optional[str]:
        """Network seam (mockable): best-effort SIC off an edgartools Company.
        EdgarSource has no reusable Company handle in its assembly path, so this is
        one extra lightweight SEC request per ticker, bounded by the module
        concurrency semaphore. Returns a 4-digit string or None."""
        from edgar import Company

        from ..sectors import extract_sic
        return extract_sic(Company(ticker))

    def _raw_filings(self, ticker: str):
        """Network seam (mockable): the filtered edgartools filings object."""
        from edgar import Company
        return Company(ticker).get_filings(form=self._event_forms)

    def _fetch_filings_index(self, ticker: str) -> list[dict]:
        """Normalize the edgartools result (None | single EntityFiling | collection)
        into a plain list of {form, filed, accession, url} dicts."""
        res = self._raw_filings(ticker)
        if res is None:
            return []
        items = res if hasattr(res, "__iter__") and not hasattr(res, "form") else [res]
        out: list[dict] = []
        for f in list(items)[: self._index_limit]:
            fd = getattr(f, "filing_date", None)
            out.append({
                "form": getattr(f, "form", "") or "",
                "filed": fd.isoformat() if hasattr(fd, "isoformat") else (fd or ""),
                "accession": getattr(f, "accession_no", None),
                "url": getattr(f, "url", None),
            })
        return out

    def _build_events_from_records(self, records: list[dict]):
        return build_events_section(records, self._event_lookback_days, date.today())

    def _fetch_sync(self, ticker: str) -> SourceResult:
        res = self._fetch_insider(ticker)        # always sets res.partial (existing branches)
        # SIC is isolated: a failure must never drop insider/statements/events. We
        # emit a PARTIAL Profile carrying only sic; _merge_flat fills the rest from
        # FMP/Finnhub, so SIC survives even when those gate the symbol's profile.
        try:
            sic = self._fetch_sic(ticker)
            if sic:
                if res.partial.profile is None:
                    res.partial.profile = Profile(sic=sic)
                else:
                    res.partial.profile.sic = sic
        except Exception as e:
            res.errors.append(f"edgar-sic: {e}")
        # Financials are isolated: a failure here must never drop the insider result.
        try:
            fin_snap = self._build_financials_snapshot(ticker, self._fetch_financials_object(ticker))
            if fin_snap.statements is not None:
                res.partial.statements = fin_snap.statements
        except Exception as e:
            res.errors.append(f"edgar-financials: {e}")
        # Events are isolated: a failure here must never drop insider/statements.
        try:
            ev = self._build_events_from_records(self._fetch_filings_index(ticker))
            if ev is not None:
                res.partial.events = ev
        except Exception as e:
            res.errors.append(f"edgar-events: {e}")
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
            f"{self.BASE}/{symbol}", params={"range": "5y", "interval": "1d"})
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
            raw = await self._get_chart(ticker)
            closes = _closes_from_chart(raw)
            spy = await self._spy()
            res.partial = _normalize_yahoo(ticker, closes, spy)
            if res.partial.price is not None:
                res.partial.price.monthly_closes = _monthly_closes_from_chart(raw)
            res.raw = {"close_count": len(closes)}
        except Exception as e:
            res.errors.append(f"yahoo: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
        return res


# --- FINRA: keyless consolidated short interest ----------------------------

class FinraSource(Source):
    """Keyless FINRA ConsolidatedShortInterest. Bulk-loads the latest bi-monthly
    cycle ONCE per run (the YahooSource fetch-once-reuse precedent), indexes by
    normalized symbol, and serves per-ticker lookups as O(1) dict hits. Disk-cached
    by SETTLEMENT DATE so the cache survives the ~2 weeks until the next cycle."""

    name = "finra"
    DATA = "https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest"
    PARTS = "https://api.finra.org/partitions/group/otcMarket/name/ConsolidatedShortInterest"
    PAGE = 5000   # FINRA record-max-limit

    def __init__(self, timeout: float = 30.0, cache_dir: str = ".cache/finra"):
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})
        self._cache_dir = Path(cache_dir)
        self._index: Optional[dict] = None
        self._settlement: Optional[str] = None
        self._load_error: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_partitions(self) -> Any:
        r = await self._client.get(self.PARTS)
        r.raise_for_status()
        return r.json()

    async def _fetch_page(self, settlement: str, offset: int) -> list:
        body = {"limit": self.PAGE, "offset": offset,
                "compareFilters": [{"fieldName": "settlementDate",
                                    "fieldValue": settlement, "compareType": "EQUAL"}]}
        r = await self._client.post(self.DATA, json=body)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def _cache_path(self, settlement: str) -> Path:
        return self._cache_dir / f"{settlement}.json"

    def _read_cache(self, settlement: str):
        cp = self._cache_path(settlement)
        try:
            if cp.exists():
                return json.loads(cp.read_text())
        except Exception:
            pass  # corrupt cache -> refetch
        return None

    def _write_cache(self, settlement: str, rows: list) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(settlement).write_text(json.dumps(rows))
        except Exception:
            pass  # cache write failure is non-fatal

    async def _load(self) -> None:
        """Discover the latest cycle and build the symbol index once."""
        if self._index is not None or self._load_error is not None:
            return
        try:
            settlement = _finra_latest_partition(await self._fetch_partitions())
            if not settlement:
                self._index = {}
                return
            rows = self._read_cache(settlement)
            if rows is None:
                rows, offset = [], 0
                while True:
                    page = await self._fetch_page(settlement, offset)
                    rows.extend(page)
                    if len(page) < self.PAGE:
                        break
                    offset += self.PAGE
                self._write_cache(settlement, rows)
            self._index = _finra_index(rows)
            self._settlement = settlement
        except Exception as e:
            self._load_error = redact_secrets(str(e))
            self._index = {}

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        await self._load()
        snap = TickerSnapshot(ticker=ticker)
        if self._load_error:
            res.errors.append(f"finra: {self._load_error}")
            res.partial = snap
            return res
        row = (self._index or {}).get(_finra_norm_symbol(ticker))
        if row is not None:
            snap.short_interest = _finra_row_to_si(row)
        # raw carries the cycle + whether THIS symbol matched (visible, not silent)
        res.raw = {"settlement_date": self._settlement, "matched": row is not None}
        res.partial = snap
        return res


# --- helpers --------------------------------------------------------------

async def _fetch_sections(
    res: SourceResult,
    get: Callable[..., Awaitable[Any]],
    sections: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    """Fetch each named section into `res.raw`, recording per-section failures as
    redacted `"<source>.<section>: <err>"` strings. One failed section never aborts
    the rest."""
    for name, (path, params) in sections.items():
        try:
            res.raw[name] = await get(path, **params)
        except Exception as e:
            res.errors.append(f"{res.source}.{name}: {redact_secrets(e)}")


def _year(d: Any) -> Optional[int]:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


# --- FINRA short interest (pure helpers) ----------------------------------

def _finra_latest_partition(payload: Any) -> Optional[str]:
    parts = (payload or {}).get("availablePartitions") or []
    dates = [p["partitions"][0] for p in parts if p.get("partitions")]
    return max(dates) if dates else None


def _finra_norm_symbol(sym: str) -> str:
    """Collapse separators so BRK.B / BRK-B / BRKB all match one key."""
    return (sym or "").upper().replace("-", "").replace(".", "")


def _finra_num(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    try:
        return float(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _finra_flag(row: dict, key: str) -> bool:
    return str(row.get(key, "")).strip().upper() in ("Y", "YES", "TRUE", "1")


def _finra_row_to_si(row: dict) -> ShortInterest:
    return ShortInterest(
        settlement_date=row.get("settlementDate"),
        short_shares=_finra_num(row, "currentShortPositionQuantity"),
        prev_short_shares=_finra_num(row, "previousShortPositionQuantity"),
        avg_daily_volume=_finra_num(row, "averageDailyVolumeQuantity"),
        days_to_cover=_finra_num(row, "daysToCoverQuantity"),
        split_flag=_finra_flag(row, "stockSplitFlag"),
        revised=_finra_flag(row, "revisionFlag"),
    )


def _finra_index(rows: list) -> dict:
    return {_finra_norm_symbol(r["symbolCode"]): r for r in rows if r.get("symbolCode")}


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


def _monthly_closes_from_chart(raw: Any) -> list[list]:
    """Pair the chart's timestamp + adjclose arrays and down-sample to ~one point
    per calendar month (last valid obs each month), oldest->newest as [iso, close].
    Returns [] if timestamps are absent (e.g. older cached 2y payloads lacking a timestamp array, or any malformed payload)."""
    try:
        result = raw["chart"]["result"][0]
        ts = result["timestamp"]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    if not ts or not series:
        return []
    by_month: dict[str, list] = {}
    for t, c in zip(ts, series, strict=False):
        if not isinstance(c, (int, float)):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date()
        by_month[f"{d.year}-{d.month:02d}"] = [d.isoformat(), float(c)]
    return [by_month[k] for k in sorted(by_month)]


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
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource,
    "finra": FinraSource, "mock": MockSource,
}


def build_sources(names: list[str], config: Optional[dict] = None) -> list[Source]:
    out, skipped = [], []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown source '{n}'. Known: {list(_REGISTRY)}")
        cls = _REGISTRY[n]
        try:
            # Only sources whose __init__ accepts `config` receive it; others stay zero-arg.
            if "config" in inspect.signature(cls.__init__).parameters:
                out.append(cls(config=config))
            else:
                out.append(cls())
        except Exception as e:
            skipped.append(f"{n} ({redact_secrets(str(e))})")
    if skipped:
        print(f"  ! skipped sources: {', '.join(skipped)}")
    return out
