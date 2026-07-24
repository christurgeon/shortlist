from __future__ import annotations

import asyncio
import dataclasses
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ...env import redact_secrets
from ..models import SourceResult, TickerSnapshot
from ._common import _load_ticker_name_index, _read_versioned_cache, _write_versioned_cache
from .base import Source


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
