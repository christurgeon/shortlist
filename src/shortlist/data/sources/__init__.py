from __future__ import annotations

import asyncio
import dataclasses
import inspect
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from .. import finra as _finra
from ..diskcache import read_json_cache, write_json_cache
from ..models import (
    SocialSentiment,
    SourceResult,
    TickerSnapshot,
)
from ._common import _load_ticker_name_index, _read_versioned_cache, _write_versioned_cache  # noqa: F401
from .base import Source, _fetch_sections, _KeyedHttpSource, _retry_after_backoff  # noqa: F401
from .edgar import EdgarSource, _edgar_semaphore, build_events_section, classify_event_form  # noqa: F401
from .finnhub import FinnhubSource, _earnings, _news_flow, _normalize_finnhub  # noqa: F401
from .fmp import FMPSource, _match, _normalize_fmp, _year  # noqa: F401
from .yahoo import YahooSource  # noqa: F401
from .yahoo_prices import (  # noqa: F401
    _MAX_RET_WINDOW,
    _MOM_12_1_BACK,
    _MOM_SKIP,
    _PCT_52W_HIGH_MIN_HISTORY,
    _PCT_52W_HIGH_WINDOW,
    _VOL_FLOOR,
    _VOL_SCALE_VOL_WINDOW,
    _YH_SIX_MONTHS,
    _YH_VOL_WINDOW,
    _chart_ts_and_series,
    _closes_from_chart,
    _dates_from_chart,
    _monthly_closes_from_chart,
    _normalize_yahoo,
    _yh_annualized_vol,
    _yh_max_drawdown,
    _yh_ret_over,
    _yh_sma,
    max_daily_return,
    mom_6m,
    mom_12_1,
    pct_to_52w_high,
    ret_between,
    snapshot_from_closes,
    snapshot_from_closes_dated,
    vol_scaled_momentum,
)

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


# --- FINRA short interest (pure helpers) ----------------------------------
# Single-sourced in data/finra.py so the sync scout fetcher shares one row-shape
# definition (CLAUDE.md "edit … not in two places"). Re-exported under the historical
# _finra_* names so call sites + tests that import them from here keep working.
_finra_latest_partition = _finra.latest_partition
_finra_norm_symbol = _finra.norm_symbol
_finra_row_to_si = _finra.row_to_si
_finra_index = _finra.index_rows


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
