from __future__ import annotations

import asyncio
import dataclasses
import inspect
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..._util import first as _first
from ..._util import from_millions as _mm
from ..._util import pct as _pct
from ...env import redact_secrets
from ...providers._fmp_insider import is_buy, tx_value
from ...stats import avg_roic, median_pe
from ...stats import residual_momentum as _stats_residual_momentum
from .. import finra as _finra
from ..diskcache import read_json_cache, write_json_cache
from ..models import (
    Analyst,
    Earnings,
    Events,
    FilingEvent,
    Fundamentals,
    Insider,
    InsiderTxn,
    NewsFlow,
    Price,
    Profile,
    SocialSentiment,
    SourceResult,
    Statements,
    TickerSnapshot,
)
from ._common import _load_ticker_name_index, _read_versioned_cache, _write_versioned_cache  # noqa: F401
from .base import Source, _fetch_sections, _KeyedHttpSource, _retry_after_backoff  # noqa: F401

# News-flow windows (fixed priors; only the flag thresholds in config are tunable).
_NEWS_LOOKBACK_DAYS = 30   # company-news fetch window
_NEWS_RECENT_DAYS = 7      # recent vs prior bucket size
# Finnhub's free company-news returns only the ~250 most-recent articles. For a
# high-volume name that cap can fall entirely inside the recent window, so the
# prior bucket is a false 0 -> we DETECT it and mark the window truncated.
_NEWS_TRUNCATE_AT = 240    # near the ~250 cap: a list this long is almost certainly capped


# --- FMP: primary, broad coverage ----------------------------------------

class FMPSource(_KeyedHttpSource):
    name = "fmp"
    # FMP's `/stable/` API; the legacy `/v3`–`/v4` endpoints were retired for
    # new keys on 2025-08-31. Every endpoint takes `?symbol=`.
    BASE = "https://financialmodelingprep.com/stable"
    _AUTH_PARAM = "apikey"
    _ENV_VAR = "FMP_API_KEY"
    _PROVIDER = "fmp"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *,
                 cache=None, config: Optional[dict] = None):
        super().__init__(api_key, timeout, cache=cache)
        # FMP opts into Retry-After-aware retry (per-minute 429s clear on backoff; a
        # recovered call caches a real 200, an exhausted 429 -> coverage rate_limited_429,
        # a persistent 5xx -> the generic "error" status). Daily-quota 429s won't clear,
        # so max_retries is deliberately small (config: fmp.max_retries, default 2).
        fmp_cfg = (config or {}).get("fmp") or {}
        self._max_retries = int(fmp_cfg.get("max_retries", 2))
        # The /stable/ insider endpoint is PAID (402 on free plans) — config.yaml
        # ships fetch_insider: false so the guaranteed-to-fail request stops
        # burning ~1 of the ~13 quota calls/ticker. Default True: an absent key
        # keeps the historical fetch-everything behavior.
        self._fetch_insider = bool(fmp_cfg.get("fetch_insider", True))

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
        if not self._fetch_insider:
            del sections["insider"]     # paid endpoint disabled -> save the quota call
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
            operating_income=[r.get("operatingIncome") for r in inc],
            dep_amort=[r.get("depreciationAndAmortization") for r in inc],
            interest_expense=[r.get("interestExpense") for r in inc],
            ebitda=[r.get("ebitda") for r in inc],
            cash_and_equivalents=[(_match(bal, r) or {}).get("cashAndCashEquivalents") for r in inc],
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
        # WINDOW the raw list before netting: the endpoint returns "the most recent
        # N transactions" with no date scope, which for a low-velocity name spans
        # years — un-windowed, the sum mislabels itself as net_value_6m. Undated
        # rows are dropped (can't be confirmed in-window; ISO dates compare
        # lexicographically). The window matches EdgarSource's lookback (183d) so
        # the two sources' figures describe the same period in _merge_insider.
        cutoff = (date.today() - timedelta(days=183)).isoformat()
        insiders = [tx for tx in insiders
                    if (tx.get("transactionDate") or "") >= cutoff]
        if insiders:
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

class FinnhubSource(_KeyedHttpSource):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"
    _AUTH_PARAM = "token"
    _ENV_VAR = "FINNHUB_API_KEY"
    _PROVIDER = "finnhub"
    # No retry override: Finnhub's 60/min is comfortable, so the base default
    # (_max_retries = 0, single attempt) is intentional.

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
            "news": ("company-news", {
                "symbol": ticker,
                "from": (today - timedelta(days=_NEWS_LOOKBACK_DAYS)).isoformat(),
                "to": today.isoformat()}),
            "earnings": ("stock/earnings", {"symbol": ticker}),
            # Window reaches BACK ~120d (one quarter + print lag) so a PAST
            # announcement (epsActual) can land in the payload — _earnings' preferred
            # SUE decay anchor. Live-probed 2026-07-09: the FREE tier returns no
            # historical entries at all (even a full past year is empty), so on this
            # plan the anchor comes from the EDGAR 10-Q/10-K filed date (bridge)
            # instead; the reach-back costs nothing and activates on a paid key.
            "earnings_calendar": ("calendar/earnings", {
                "symbol": ticker,
                "from": (today - timedelta(days=120)).isoformat(),
                "to": (today + timedelta(days=90)).isoformat()}),
        }
        await _fetch_sections(res, self._get, calls)
        res.partial = _normalize_finnhub(ticker, res.raw)
        return res


def _news_flow(articles: list, ref: Optional[date] = None) -> NewsFlow:
    """Bucket Finnhub company-news articles into recent/prior/window counts by
    article `datetime` (unix seconds) vs a reference date. Pure."""
    today = ref or date.today()
    recent_cut = today - timedelta(days=_NEWS_RECENT_DAYS)
    prior_cut = today - timedelta(days=2 * _NEWS_RECENT_DAYS)
    recent = prior = window = 0
    latest: Optional[date] = None
    oldest: Optional[date] = None
    for a in articles:
        ts = a.get("datetime")
        if not ts:
            continue
        if ts > 1e12:        # tolerate a millisecond feed (Finnhub sends seconds)
            ts = ts / 1000
        try:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        window += 1
        if latest is None or d > latest:
            latest = d
        if oldest is None or d < oldest:
            oldest = d
        if d >= recent_cut:
            recent += 1
        elif d >= prior_cut:
            prior += 1
    # `capped`: the list hit the free-tier ~250 cap (always-noisy name). Separately,
    # `prior_unreliable`: the cap also ate the prior window (no history past 14d), so
    # `prior`'s 0 is a false 0 -> blank it. truncated == capped (honest: any capped list).
    capped = window >= _NEWS_TRUNCATE_AT
    prior_unreliable = capped and oldest is not None and oldest > prior_cut
    return NewsFlow(
        as_of=today.isoformat(), count_recent=recent,
        count_prior=None if prior_unreliable else prior,
        count_window=window, latest_dt=latest.isoformat() if latest else None,
        truncated=capped)


def _earnings(rows: list, calendar: Optional[dict], ref: Optional[date] = None) -> Earnings:
    """Build an Earnings section from Finnhub `stock/earnings` rows (newest-first)
    and a `calendar/earnings` payload. Pure. surprisePercent is already in percent."""
    today = ref or date.today()
    # Sort newest-first by period so correctness doesn't depend on Finnhub's ordering.
    ordered = sorted((rows or []), key=lambda r: r.get("period") or "", reverse=True)
    surprises = [r.get("surprisePercent") for r in ordered
                 if isinstance(r.get("surprisePercent"), (int, float))]
    beats = sum(1 for s in surprises if s > 0) if surprises else None
    # Next report = earliest calendar entry today-or-later with no actual yet (>= so a
    # same-day after-close print isn't dropped on the morning it matters most).
    next_date = None
    cal = (calendar or {}).get("earningsCalendar") or []
    future = sorted(d["date"] for d in cal
                    if d.get("date") and d["date"] >= today.isoformat()
                    and d.get("epsActual") is None)
    if future:
        next_date = future[0]
    # last_report_date: the best-available APPROXIMATION of the most-recent ANNOUNCEMENT
    # date (PREDICTIVE_SIGNALS §1 SUE leg). Finnhub `stock/earnings` rows carry only the
    # fiscal `period` (quarter-END), NOT the print date, so we prefer the `calendar/
    # earnings` entries that DO carry a true announcement `date` AND an `epsActual` (i.e.
    # a report that has already happened, date <= today). Pick the latest such date.
    # Fallback (calendar missing past entries — ALWAYS the case on the free tier,
    # live-probed 2026-07-09): the most recent `stock/earnings` period (quarter-end) — a
    # WEAKER proxy, since the actual print lands ~30-45 days later, so this OVER-states
    # staleness. `last_report_date_estimated` marks the fallback so the bridge can
    # refine the anchor with the EDGAR 10-Q/10-K filed date (~0-5d proxy) without ever
    # degrading a true announcement date. Documented in CLAUDE.md / HARNESS.md.
    last_report_date = None
    estimated = True
    past = sorted(d["date"] for d in cal
                  if d.get("date") and d["date"] <= today.isoformat()
                  and d.get("epsActual") is not None)
    if past:
        last_report_date = past[-1]
        estimated = False
    elif ordered:
        last_report_date = ordered[0].get("period") or None
    return Earnings(
        as_of=today.isoformat(), recent_surprise_pcts=surprises,
        quarters=len(surprises) or None, beats=beats,
        last_surprise_pct=surprises[0] if surprises else None, next_date=next_date,
        last_report_date=last_report_date, last_report_date_estimated=estimated)


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
        # `roiTTM` is Return on *Investment*, mapped here as a deliberate ROIC proxy.
        # It only surfaces on the FMP-gated fallback path (FMP leads the fundamentals
        # merge); whether to keep the proxy or drop it to None is an open TECH-DEBT
        # item gated on a quality/moat backtest.
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
    news = raw.get("news")
    if isinstance(news, list):          # present (even empty) -> a real 0-count fact
        # Re-bucketed against date.today() every call; correctness on a cache HIT
        # relies on the from/to dates being in the cache key (so it's day-partitioned).
        snap.news = _news_flow(news)
    er = raw.get("earnings")
    if isinstance(er, list):            # present (even empty) -> a real Earnings fact
        snap.earnings = _earnings(er, raw.get("earnings_calendar"))
    return snap


# --- SEC EDGAR: authoritative insider Form 4 -------------------------------

# SEC enforces ~10 req/s fair-access per IP, and each ticker pulls many filings.
# The collector's per-ticker semaphore doesn't bound SEC request *rate* (EDGAR's
# calls happen inside a worker thread, invisible to it), so all EdgarSource work
# is funnelled through this shared gate — well under the limit. Re-created if the
# event loop changes (e.g. a second collect() call) to stay loop-bound-safe.
_EDGAR_MAX_CONCURRENCY = 3
_edgar_gate: dict = {}

# Max recent Form 4 filings fetched per ticker for insider aggregation. A
# high-velocity insider universe could exceed this within the lookback window,
# truncating net_value_6m / buy_count / sell_count (acceptable for typical tickers).
_FORM4_FETCH_LIMIT = 40


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
    Returns None when there is nothing at all — NEVER an all-falsy Events
    (load-bearing for the merge's _has_data check; spec §4). Separately from the
    advisory flags, the latest exact-form 10-Q/10-K filed date is carried as
    `last_report_filed` (the bridge's SUE decay anchor) — exact forms only, since
    a 10-Q/A can land months after the print and would wrongly freshen the anchor."""
    cutoff = today - timedelta(days=lookback_days)
    kept: list[tuple[str, FilingEvent]] = []
    report_filed: Optional[str] = None
    for r in records:
        form = r.get("form", "")
        filed = r.get("filed")
        try:
            in_window = date.fromisoformat(filed) >= cutoff
        except (TypeError, ValueError):
            continue
        if form.strip().upper() in ("10-Q", "10-K"):
            if report_filed is None or filed > report_filed:
                report_filed = filed
            continue
        attr = classify_event_form(form)
        if attr is None or not in_window:
            continue
        kept.append((attr, FilingEvent(
            form=form, filed=filed,
            accession=r.get("accession"), url=r.get("url"))))
    if not kept and report_filed is None:
        return None
    kept.sort(key=lambda p: p[1].filed, reverse=True)   # newest-first
    ev = Events(recent=[fe for _, fe in kept], last_report_filed=report_filed)
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
        # 10-Q/10-K are fetched for the SUE decay anchor — they never enter the
        # advisory `recent` list. edgartools auto-includes /A amendments in the
        # fetch; build_events_section's exact-form compare keeps them off the anchor.
        self._event_forms = ev.get(
            "forms", ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G",
                      "10-Q", "10-K"])
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

        from ...providers._form4 import aggregate_form4

        res = SourceResult(source=self.name)
        cutoff = date.today() - timedelta(days=self.lookback_days)
        try:
            summary = aggregate_form4(
                Company(ticker).get_filings(form="4").latest(_FORM4_FETCH_LIMIT), cutoff, self._conviction)
        except Exception as e:
            res.errors.append(f"edgar: {redact_secrets(e)}")
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

    def _fetch_financials_object(self, ticker: str) -> Any:
        """Seam for mocking: returns an edgartools Financials (or raises)."""
        from edgar import Company
        return Company(ticker).get_financials()

    def _build_financials_snapshot(self, ticker: str, fin: Any) -> TickerSnapshot:
        """Map an edgartools Financials onto a Statements-only snapshot. Pure given
        `fin`. Values are absolute USD (no scaling)."""
        from ...providers._edgar_facts import extract_financials
        try:
            shares = fin.get_shares_outstanding_diluted()
        except Exception:
            shares = None
        ef = extract_financials(
            fin.income_statement().to_dataframe(),
            fin.cashflow_statement().to_dataframe(),
            fin.balance_sheet().to_dataframe(),
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
                diluted_shares=ef.diluted_shares,
                total_debt=ef.total_debt,
                cash_and_equivalents=ef.cash_and_equivalents,
                operating_income=ef.operating_income,
                dep_amort=ef.dep_amort,
                interest_expense=ef.interest_expense,
                ebitda=ef.ebitda,
                total_assets=ef.total_assets,
                asset_growth=ef.asset_growth,
                accruals=ef.accruals,
                dividends_paid=ef.dividends_paid,
                repurchases=ef.repurchases,
                debt_repayments=ef.debt_repayments,
                debt_issuance=ef.debt_issuance,
            )
        # gross_profit/total_equity aren't in EdgarFinancials; the merge layer fills them from FMP when available.
        return snap

    def _fetch_sic(self, ticker: str) -> Optional[str]:
        """Network seam (mockable): best-effort SIC off an edgartools Company.
        EdgarSource has no reusable Company handle in its assembly path, so this is
        one extra lightweight SEC request per ticker, bounded by the module
        concurrency semaphore. Returns a 4-digit string or None."""
        from edgar import Company

        from ...sectors import extract_sic
        return extract_sic(Company(ticker))

    def _raw_filings(self, ticker: str) -> Any:
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

    def _build_events_from_records(self, records: list[dict]) -> Optional[Events]:
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
            res.errors.append(f"edgar-sic: {redact_secrets(e)}")
        # Financials are isolated: a failure here must never drop the insider result.
        try:
            fin_snap = self._build_financials_snapshot(ticker, self._fetch_financials_object(ticker))
            if fin_snap.statements is not None:
                res.partial.statements = fin_snap.statements
        except Exception as e:
            res.errors.append(f"edgar-financials: {redact_secrets(e)}")
        # Events are isolated: a failure here must never drop insider/statements.
        try:
            ev = self._build_events_from_records(self._fetch_filings_index(ticker))
            if ev is not None:
                res.partial.events = ev
        except Exception as e:
            res.errors.append(f"edgar-events: {redact_secrets(e)}")
        return res


# --- Mock: offline demo (illustrative, not verified) ----------------------

class MockSource(Source):
    name = "mock"

    async def fetch(self, ticker: str) -> SourceResult:
        from ..mockdata import SAMPLE
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


# --- FINRA: keyless consolidated short interest ----------------------------

class FinraSource(Source):
    """Keyless FINRA ConsolidatedShortInterest. Bulk-loads the latest bi-monthly
    cycle ONCE per run (the YahooSource fetch-once-reuse precedent), indexes by
    normalized symbol, and serves per-ticker lookups as O(1) dict hits. Disk-cached
    by SETTLEMENT DATE so the cache survives the ~2 weeks until the next cycle."""

    name = "finra"
    DATA = _finra.FINRA_DATA_URL
    PARTS = _finra.FINRA_PARTS_URL
    PAGE = _finra.FINRA_PAGE   # FINRA record-max-limit
    MAX_PAGES = 200            # hard cap: ~1M rows dwarfs the real universe (~30k)

    def __init__(self, timeout: float = 30.0, cache_dir: str = ".cache/finra"):
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})
        self._cache_dir = Path(cache_dir)
        self._index: Optional[dict] = None
        self._settlement: Optional[str] = None
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # bulk load fires once, not per ticker

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

    def _read_cache(self, settlement: str) -> Optional[list]:
        return read_json_cache(self._cache_path(settlement))

    def _write_cache(self, settlement: str, rows: list) -> None:
        write_json_cache(self._cache_path(settlement), rows)

    async def _load(self) -> None:
        """Discover the latest cycle and build the symbol index once."""
        if self._index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_locked()

    async def _load_locked(self) -> None:
        if self._index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        try:
            settlement = _finra_latest_partition(await self._fetch_partitions())
            if not settlement:
                self._index = {}
                return
            rows = self._read_cache(settlement)
            if rows is None:
                rows, offset, truncated = [], 0, False
                for _ in range(self.MAX_PAGES):
                    page = await self._fetch_page(settlement, offset)
                    rows.extend(page)
                    if len(page) < self.PAGE:
                        break
                    offset += self.PAGE
                else:  # cap hit: a buggy/looping endpoint, never real data
                    truncated = True
                    print(f"finra: pagination cap ({self.MAX_PAGES} pages) hit for "
                          f"settlement {settlement}; result may be truncated",
                          file=sys.stderr)
                if not truncated:
                    # never cache a truncated set: the scout's short-interest
                    # fetcher shares this file and requires the COMPLETE rows
                    # for the whole ~2-week settlement cycle
                    self._write_cache(settlement, rows)
            self._index = _finra_index(rows)
            self._settlement = settlement
        except Exception as e:
            self._load_error = redact_secrets(e)
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


class WsbSource(Source):
    """Keyless WSB social mentions via ApeWisdom. Bulk-loads the top WSB tickers
    ONCE per run (the FinraSource fetch-once-reuse precedent), indexes by normalized
    symbol, and serves per-ticker lookups as O(1) dict hits. Disk-cached by fetch date.
    No API key, no config — cache_dir is shared with the scout WsbHypeSignal."""

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


# --- helpers --------------------------------------------------------------

def _year(d: Any) -> Optional[int]:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


# --- FINRA short interest (pure helpers) ----------------------------------
# Single-sourced in data/finra.py so the sync scout fetcher shares one row-shape
# definition (CLAUDE.md "edit … not in two places"). Re-exported under the historical
# _finra_* names so call sites + tests that import them from here keep working.
_finra_latest_partition = _finra.latest_partition
_finra_norm_symbol = _finra.norm_symbol
_finra_row_to_si = _finra.row_to_si
_finra_index = _finra.index_rows


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


def ret_between(xs: list[float], start_back: int, end_back: int) -> Optional[float]:
    """Return over a window of a price series: xs[-end_back] / xs[-start_back] - 1.

    `start_back` is the older endpoint (further back), `end_back` the newer one
    (`end_back=1` == most recent close). Guards the denominator's truthiness and
    sufficient history exactly like `_yh_ret_over`, so a zero/None-filtered price or a
    too-short series yields None rather than raising. `ret_between(xs, 127, 1)` is the
    trailing 6m return; `ret_between(xs, 274, 22)` is the 12-1 skip-month return."""
    if end_back < 1 or start_back <= end_back or len(xs) < start_back:
        return None
    denom = xs[-start_back]
    if not denom:
        return None
    return xs[-end_back] / denom - 1.0


# Multi-horizon momentum candidates (Stage 0 prize-bound + Stage 1 measurement).
# Pure functions over a daily adjusted-close series, oldest -> newest.
_MOM_SKIP = 22          # skip the most recent ~21 trading days (1m reversal guard)
_MOM_12_1_BACK = 274    # 274 - 22 == 252 td (12-month) formation window


def mom_6m(closes: list[float]) -> Optional[float]:
    """Absolute trailing 6-month return (== _yh_ret_over(closes, _YH_SIX_MONTHS))."""
    return ret_between(closes, _YH_SIX_MONTHS + 1, 1)


def mom_12_1(closes: list[float]) -> Optional[float]:
    """Canonical 12-1 momentum: 252-td formation return ending ~21 td (one month) back,
    skipping the most recent month to avoid short-term reversal."""
    return ret_between(closes, _MOM_12_1_BACK, _MOM_SKIP)


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


# --- PREDICTIVE_SIGNALS §2 price-refinement MEASUREMENT axes (backtest-only) ---------
# Pure single-series functions over a daily ADJUSTED-close list, oldest -> newest. They
# read only trailing windows, so they are point-in-time when the caller passes closes
# truncated to as_of. NO production leg reads them (momentum sub-score is byte-identical);
# they exist so the live-price backtest can measure rank IC + collinearity before wiring.
_PCT_52W_HIGH_WINDOW = 252        # ~trading days in 52 weeks
_PCT_52W_HIGH_MIN_HISTORY = 200   # require ~a full year before calling it a "52-week" high
_MAX_RET_WINDOW = 21              # ~trading days in 1 month (MAX-effect formation)
_VOL_SCALE_VOL_WINDOW = 126       # 6-month realized-vol scaler (Barroso-Santa-Clara) — NOT the
                                  # 252-day risk default; do not "simplify" to realized_vol
_VOL_FLOOR = 1e-4                 # annualized-vol floor: at/below this, vol_scaled abstains


def pct_to_52w_high(closes: list[float]) -> Optional[float]:
    """Nearness to the trailing 52-week high (George-Hwang 2004): closes[-1] / max(last 252),
    in (0, 1]; nearer the high scores higher. Abstains (None) below _PCT_52W_HIGH_MIN_HISTORY
    (~200) closes — so a freshly-listed name isn't ranked on a few months of data — and on a
    non-positive window max. NOTE: the 200 floor is below the 252-day window, so for a name
    with 200-251 closes the 'high' is taken over the full available history (marginally under
    a true 52 weeks); the floor reduces but does not fully eliminate that for very recent IPOs."""
    if len(closes) < _PCT_52W_HIGH_MIN_HISTORY:
        return None
    hi = max(closes[-_PCT_52W_HIGH_WINDOW:])
    if hi <= 0:
        return None
    return closes[-1] / hi


def max_daily_return(closes: list[float], window: int = _MAX_RET_WINDOW) -> Optional[float]:
    """Largest single-day simple return over the trailing `window` days (Bali-Cakici-Whitelaw
    2011, the "MAX effect" — a lottery-demand proxy and a NEGATIVE return predictor, so its
    scoring band is inverted). A non-positive prior close contributes a 0.0 return (a halt/
    placeholder, not a real move), matching _yh_annualized_vol's convention. Abstains (None)
    on fewer than `window` usable returns."""
    if len(closes) < window + 1:
        return None
    seg = closes[-(window + 1):]
    rets = [seg[i] / seg[i - 1] - 1.0 if seg[i - 1] > 0 else 0.0 for i in range(1, len(seg))]
    return max(rets)


def vol_scaled_momentum(closes: list[float]) -> Optional[float]:
    """Risk-managed momentum (Barroso-Santa-Clara 2015): 12-1 momentum / trailing 6-month
    annualized realized vol. Reuses mom_12_1 (needs 274 closes) and _yh_annualized_vol(126).
    Abstains (None) when mom_12_1 is unavailable or the vol is None / <= _VOL_FLOOR (a near-flat
    window would otherwise make mom/~0 a huge FINITE garbage value that silently pollutes the
    rank IC). NOTE: cross-sectionally this is just risk-adjusted RAW momentum — B-S-C's Sharpe
    gain is a time-series vol-targeting result, so a null rank IC here does NOT refute the
    paper; the collinearity vs residual_momentum settles whether it adds anything."""
    mom = mom_12_1(closes)
    if mom is None:
        return None
    vol = _yh_annualized_vol(closes, _VOL_SCALE_VOL_WINDOW)
    if vol is None or vol <= _VOL_FLOOR:
        return None
    return mom / vol


def _closes_from_chart(raw: Any) -> list[float]:
    try:
        result = raw["chart"]["result"][0]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    return [c for c in series if isinstance(c, (int, float))]


def _chart_ts_and_series(raw: Any) -> tuple[Optional[list], Optional[list]]:
    """Pull the Yahoo chart payload's (timestamp, adjclose) arrays as a pair, or
    (None, None) on any malformed/absent shape. Shared by `_dates_from_chart` and
    `_monthly_closes_from_chart` (both need timestamps paired with closes).
    NOT used by `_closes_from_chart`: that function tolerates a payload with
    closes but no timestamp array (older cached 5y payloads), which requires
    looking up `timestamp` and `adjclose` independently rather than atomically."""
    try:
        result = raw["chart"]["result"][0]
        return result["timestamp"], result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return None, None


def _dates_from_chart(raw: Any) -> list[date]:
    """Bar dates aligned 1:1 to `_closes_from_chart(raw)` (same numeric-close filter),
    oldest->newest, as `datetime.date`. Returns [] when timestamps are absent (older
    cached 5y payloads lacking a timestamp array) or misaligned (len(ts) != len(series)),
    so the caller can fall back to the date-less (residual-momentum-None) behavior."""
    ts, series = _chart_ts_and_series(raw)
    if not ts or not series or len(ts) != len(series):
        return []
    out: list[date] = []
    for t, c in zip(ts, series, strict=False):
        if not isinstance(c, (int, float)):
            continue  # mirror _closes_from_chart: drop non-numeric closes + their date
        out.append(datetime.fromtimestamp(t, tz=timezone.utc).date())
    return out


def _monthly_closes_from_chart(raw: Any) -> list[list]:
    """Pair the chart's timestamp + adjclose arrays and down-sample to ~one point
    per calendar month (last valid obs each month), oldest->newest as [iso, close].
    Returns [] if timestamps are absent (e.g. older cached 5y payloads lacking a timestamp array, or any malformed payload)."""
    ts, series = _chart_ts_and_series(raw)
    if not ts or not series:
        return []
    by_month: dict[str, list] = {}
    for t, c in zip(ts, series, strict=False):
        if not isinstance(c, (int, float)):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date()
        by_month[f"{d.year}-{d.month:02d}"] = [d.isoformat(), float(c)]
    return [by_month[k] for k in sorted(by_month)]


def _normalize_yahoo(ticker: str, closes: list[float], spy_closes: list[float],
                     dates: Optional[list] = None,
                     spy_dates: Optional[list] = None) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    if not closes:
        return snap
    stock_6m = _yh_ret_over(closes, _YH_SIX_MONTHS)
    spy_6m = _yh_ret_over(spy_closes, _YH_SIX_MONTHS) if spy_closes else None
    rel = stock_6m - spy_6m if (stock_6m is not None and spy_6m is not None) else None
    # Residual momentum needs the DATE-ALIGNED stock+SPY series (§2). The scalar live-merge
    # path (no dates) leaves it None; the dated backtest seam supplies dates so it computes.
    resid = None
    if dates is not None and spy_dates is not None:
        resid = _stats_residual_momentum(dates, closes, spy_dates, spy_closes)
    snap.price = Price(
        price=closes[-1],
        ma200=_yh_sma(closes, 200),
        ret_6m=stock_6m,
        rel_strength_6m=rel,
        realized_vol=_yh_annualized_vol(closes),
        max_drawdown=_yh_max_drawdown(closes),
        residual_momentum=resid,
        pct_to_52w_high=pct_to_52w_high(closes),
        max_daily_return=max_daily_return(closes),
        vol_scaled_momentum=vol_scaled_momentum(closes),
    )
    return snap


def snapshot_from_closes(ticker: str, closes: list[float],
                         spy_closes: list[float]) -> TickerSnapshot:
    """Public seam: build a point-in-time Price snapshot from a close series,
    delegating to the same math the live Yahoo source uses. Pass closes already
    truncated to the as-of date for a look-ahead-free reconstruction.

    NOTE: scalar (date-less) — residual_momentum stays None here. Use
    snapshot_from_closes_dated when you have the date-aligned stock + SPY series."""
    return _normalize_yahoo(ticker, closes, spy_closes)


def snapshot_from_closes_dated(ticker: str, dates: list, closes: list[float],
                               spy_dates: list, spy_closes: list[float]) -> TickerSnapshot:
    """Dated seam (§2): identical to snapshot_from_closes PLUS a date-aligned residual-
    momentum leg. The stock and SPY series are DATE-INNER-JOINED inside stats.residual_
    momentum before any return is computed — they need NOT be the same length or share
    listing dates. Pass both series truncated to as_of for a look-ahead-free read."""
    return _normalize_yahoo(ticker, closes, spy_closes, dates, spy_dates)


class GovContractsSource(Source):
    """Keyless USAspending federal procurement-contract obligations.

    Resolves ticker->name via SEC company_tickers.json (bulk-loaded once, month-
    cached), then per ticker queries `spending_by_transaction` for the trailing
    24m, confidence-filters recipients (see govcontract_match), and buckets
    window-scoped `Transaction Amount` into TTM vs prior-TTM. Aux section; never
    moves coverage. Never raises — degrades to None on any failure.

    NOTE: uses the action-level `spending_by_transaction` endpoint, NOT
    `spending_by_award` (whose `time_period` is an overlap filter returning
    un-window-scoped award totals — verified)."""

    name = "gov_contracts"
    COUNT_URL = "https://api.usaspending.gov/api/v2/search/spending_by_transaction_count/"
    DATA_URL = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
    _CONTRACT_CODES = ["A", "B", "C", "D"]
    _TTM_DAYS = 365            # TTM vs prior-TTM split boundary

    def __init__(self, timeout: float = 20.0, cache_dir: str = ".cache/usaspending",
                 config: Optional[dict] = None):
        import httpx  # lazy: only needed for live runs
        cfg = (config or {}).get("gov_contracts", {}) if config else {}
        self._client = httpx.AsyncClient(
            timeout=float(cfg.get("timeout", timeout)),
            headers={"User-Agent": "shortlist gov-contracts (contact in SEC_IDENTITY)"})
        self._cache_dir = Path(cfg.get("cache_dir", cache_dir))
        self._min_conf = float(cfg.get("match_min_confidence", 0.80))
        self._months = int(cfg.get("trailing_months", 24))
        self._max_pages = int(cfg.get("max_pages", 5))
        self._name_index: Optional[dict] = None
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # name index loads once, not per ticker

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _load_names(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_names_locked()

    async def _load_names_locked(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        self._name_index, self._load_error = await _load_ticker_name_index(
            self._client, str(self._cache_dir))

    def _filters(self, name: str, start: str, end: str) -> dict:
        return {"recipient_search_text": [name],
                "award_type_codes": self._CONTRACT_CODES,
                "time_period": [{"start_date": start, "end_date": end}]}

    def _cache_path(self, ticker: str, day: str) -> Path:
        return self._cache_dir / f"contracts-{ticker.upper()}-{day}.json"

    _CACHE_V = 1   # bump if the cached GovContracts shape changes

    def _read_cache(self, ticker: str, day: str) -> Optional[dict]:
        return _read_versioned_cache(self._cache_path(ticker, day), self._CACHE_V)

    def _write_cache(self, ticker: str, day: str, payload: dict) -> None:
        _write_versioned_cache(self._cache_path(ticker, day), self._CACHE_V, payload)

    async def fetch(self, ticker: str) -> SourceResult:
        from ..govcontract_match import match_confidence
        from ..models import GovContracts
        res = SourceResult(source=self.name)
        snap = TickerSnapshot(ticker=ticker)
        res.partial = snap
        await self._load_names()
        if self._load_error:
            res.errors.append(f"gov_contracts: {self._load_error}")
            return res
        name = (self._name_index or {}).get(ticker.upper())
        if not name:
            res.raw = {"resolved_name": None}
            return res
        today = date.today()
        end = today.isoformat()
        # Warm per-ticker cache (Yahoo/FINRA precedent): a same-day re-run of the
        # basket makes zero USAspending calls.
        cached = self._read_cache(ticker, end)
        if cached is not None:
            # Guarded rebuild: a corrupt/stale payload must never raise out of
            # fetch() — it degrades to a cache miss and the live path below runs.
            try:
                if cached.get("matched"):
                    snap.gov_contracts = GovContracts(**cached["gc"])
                res.raw = {"resolved_name": name, "matched": bool(cached.get("matched")),
                           "total_txns": cached.get("total_txns"), "cached": True}
                return res
            except Exception:
                snap.gov_contracts = None
        start = (today - timedelta(days=int(self._months * 30.44))).isoformat()
        cutoff = (today - timedelta(days=self._TTM_DAYS)).isoformat()
        try:
            cnt = await self._client.post(
                self.COUNT_URL, json={"filters": self._filters(name, start, end)})
            cnt.raise_for_status()
            total = ((cnt.json() or {}).get("results") or {}).get("contracts")
            ttm = prior = 0.0
            ttm_n = 0
            recipients: set[str] = set()
            primary_name, primary_amt, primary_conf = None, -1.0, 0.0
            latest_action = None
            truncated = False
            page = 1
            while page <= self._max_pages:
                body = {"filters": self._filters(name, start, end),
                        "fields": ["Award ID", "Recipient Name", "Action Date",
                                   "Transaction Amount", "Awarding Agency"],
                        "sort": "Transaction Amount", "order": "desc",
                        "page": page, "limit": 100}
                r = await self._client.post(self.DATA_URL, json=body)
                r.raise_for_status()
                payload = r.json() or {}
                rows = payload.get("results") or []
                for row in rows:
                    recip = row.get("Recipient Name") or ""
                    conf = match_confidence(name, recip, alias_for=(ticker,))
                    if conf < self._min_conf:
                        continue
                    amt = row.get("Transaction Amount")
                    adate = row.get("Action Date")
                    if amt is None or adate is None:
                        continue
                    recipients.add(recip)
                    if abs(amt) > primary_amt:   # primary = largest single action by |$|
                        primary_amt, primary_name, primary_conf = abs(amt), recip, conf
                    if latest_action is None or adate > latest_action:
                        latest_action = adate
                    if adate >= cutoff:        # ISO dates compare lexicographically
                        ttm += amt
                        ttm_n += 1
                    else:
                        prior += amt
                has_next = (payload.get("page_metadata") or {}).get("hasNext")
                if not has_next:
                    break
                if page == self._max_pages and has_next:
                    truncated = True
                page += 1
            if primary_name is None:           # nothing cleared the match guard
                self._write_cache(ticker, end, {"matched": False, "total_txns": total})
                res.raw = {"resolved_name": name, "matched": False, "total_txns": total}
                return res
            gc = GovContracts(
                as_of=end, latest_action=latest_action, ttm_obligated=ttm,
                prior_ttm_obligated=prior, award_count_ttm=ttm_n,
                matched_recipient=primary_name, match_confidence=primary_conf,
                recipient_count=len(recipients), truncated=truncated, total_txns=total)
            snap.gov_contracts = gc
            self._write_cache(ticker, end, {"matched": True, "total_txns": total,
                                            "gc": dataclasses.asdict(gc)})
            res.raw = {"resolved_name": name, "matched": True, "total_txns": total}
        except Exception as e:
            res.errors.append(f"gov_contracts: {redact_secrets(e)}")
        return res


class LobbyingSource(Source):
    """Keyless Senate LDA federal lobbying-disclosure spend.

    Resolves ticker->name via SEC company_tickers.json (bulk-loaded once, month-
    cached), then per ticker queries the LDA filings API (`/filings/?client_name=`)
    across the calendar years overlapping the trailing window, confidence-filters
    clients (see entity_match), and buckets spend (income-else-expenses) into TTM vs
    prior-TTM by `dt_posted`. Aux section; never moves coverage. Never raises.

    Targets lda.gov (lda.senate.gov is retired after 2026-06-30); base URL is
    config-driven."""

    name = "lobbying"
    _TTM_DAYS = 365

    def __init__(self, timeout: float = 20.0, cache_dir: str = ".cache/lda",
                 config: Optional[dict] = None):
        import httpx  # lazy: only needed for live runs
        cfg = (config or {}).get("lobbying", {}) if config else {}
        self._base = str(cfg.get("base_url", "https://lda.gov/api/v1")).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=float(cfg.get("timeout", timeout)),
            headers={"Accept": "application/json",
                     "User-Agent": "shortlist lobbying (contact in SEC_IDENTITY)"})
        self._cache_dir = Path(cfg.get("cache_dir", cache_dir))
        self._min_conf = float(cfg.get("match_min_confidence", 0.85))
        self._months = int(cfg.get("trailing_months", 24))
        self._max_pages = int(cfg.get("max_pages_per_year", 4))
        # Keyless LDA allows ~15 req/min; basket runs WILL hit 429. Retry it with
        # Retry-After-aware backoff (the FMP pattern) so coverage degrades to "slow",
        # not "missing". 402 gating and other 4xx are NOT retried.
        self._max_retries = int(cfg.get("max_retries", 2))
        self._name_index: Optional[dict] = None
        self._load_error: Optional[str] = None
        self._load_lock = asyncio.Lock()   # name index loads once, not per ticker

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict) -> Any:
        for attempt in range(self._max_retries + 1):
            r = await self._client.get(f"{self._base}/{path}", params=params)
            if await _retry_after_backoff(r, attempt, self._max_retries):
                continue
            r.raise_for_status()
            return r.json()

    async def _load_names(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return
        async with self._load_lock:
            await self._load_names_locked()

    async def _load_names_locked(self) -> None:
        if self._name_index is not None or self._load_error is not None:
            return   # another task won the race while we waited on the lock
        self._name_index, self._load_error = await _load_ticker_name_index(
            self._client, str(self._cache_dir))

    def _cache_path(self, ticker: str, day: str) -> Path:
        return self._cache_dir / f"lobby-{ticker.upper()}-{day}.json"

    def _read_cache(self, ticker: str, day: str) -> Optional[dict]:
        return _read_versioned_cache(self._cache_path(ticker, day), self._CACHE_V)

    def _write_cache(self, ticker: str, day: str, payload: dict) -> None:
        _write_versioned_cache(self._cache_path(ticker, day), self._CACHE_V, payload)

    _CACHE_V = 1   # bump if the cached Lobbying shape changes

    @staticmethod
    def _spend(row: dict) -> Optional[float]:
        """A filing reports EITHER income (outside firm fee) OR expenses (in-house).
        Summing across a client's filings can modestly double-count when the same
        activity is reported by a retained firm (income) AND in-house (expenses) —
        bounded and acceptable for a research-only presence/trend signal."""
        for k in ("income", "expenses"):
            v = row.get(k)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    async def fetch(self, ticker: str) -> SourceResult:
        from ..entity_match import match_confidence
        from ..models import Lobbying
        res = SourceResult(source=self.name)
        snap = TickerSnapshot(ticker=ticker)
        res.partial = snap
        await self._load_names()
        if self._load_error:
            res.errors.append(f"lobbying: {self._load_error}")
            return res
        name = (self._name_index or {}).get(ticker.upper())
        if not name:
            res.raw = {"resolved_name": None}
            return res
        today = date.today()
        end = today.isoformat()
        cached = self._read_cache(ticker, end)
        if cached is not None:
            if cached.get("matched"):
                snap.lobbying = Lobbying(**cached["lb"])
            res.raw = {"resolved_name": name, "matched": bool(cached.get("matched")),
                       "cached": True}
            return res
        cutoff = (today - timedelta(days=self._TTM_DAYS)).isoformat()
        window_start = today - timedelta(days=int(self._months * 30.44))
        years = list(range(window_start.year, today.year + 1))
        try:
            ttm = prior = 0.0
            ttm_n = 0
            total = 0
            registrants: set[str] = set()
            best_client, best_conf = None, 0.0
            latest = None
            truncated = False
            for yr in years:
                page = 1
                while page <= self._max_pages:
                    payload = await self._get_json(
                        "filings/",
                        {"client_name": name, "filing_year": yr, "page": page}) or {}
                    total += (payload.get("count") or 0) if page == 1 else 0
                    rows = payload.get("results") or []
                    for row in rows:
                        client = (row.get("client") or {}).get("name") or ""
                        conf = match_confidence(name, client)
                        if conf < self._min_conf:
                            continue
                        spend = self._spend(row)
                        posted = (row.get("dt_posted") or "")[:10]  # ISO date prefix
                        if spend is None or not posted:
                            continue
                        if conf > best_conf:
                            best_conf, best_client = conf, client
                        if latest is None or posted > latest:
                            latest = posted
                        # Window by dt_posted (submission date): it lags the activity
                        # quarter by up to a filing cycle, but it's the only monotone
                        # timestamp — acceptable for a trend context line.
                        if posted >= cutoff:
                            ttm += spend
                            ttm_n += 1
                            reg = (row.get("registrant") or {}).get("name")
                            if reg:
                                registrants.add(reg)   # TTM-scoped: matches the surfaced count
                        elif posted >= window_start.isoformat():
                            prior += spend
                    if not payload.get("next"):
                        break
                    if page == self._max_pages and payload.get("next"):
                        truncated = True
                    page += 1
            if best_client is None:
                self._write_cache(ticker, end, {"matched": False})
                res.raw = {"resolved_name": name, "matched": False, "total_filings": total}
                return res
            lb = Lobbying(
                as_of=end, latest_filing=latest, ttm_spend=ttm, prior_ttm_spend=prior,
                filing_count_ttm=ttm_n, matched_client=best_client,
                match_confidence=best_conf, registrant_count=len(registrants),
                truncated=truncated, total_filings=total)
            snap.lobbying = lb
            self._write_cache(ticker, end, {"matched": True, "lb": dataclasses.asdict(lb)})
            res.raw = {"resolved_name": name, "matched": True, "total_filings": total}
        except Exception as e:
            res.errors.append(f"lobbying: {redact_secrets(e)}")
        return res


_REGISTRY = {
    "yahoo": YahooSource,
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource,
    "finra": FinraSource, "mock": MockSource,
    "wsb": WsbSource, "gov_contracts": GovContractsSource,
    "lobbying": LobbyingSource,
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
            skipped.append(f"{n} ({redact_secrets(e)})")
    if skipped:
        print(f"  ! skipped sources: {', '.join(skipped)}")
    return out
