from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from ..models import SourceResult
from ._common import _NameIndexedSource
from .base import _retry_after_backoff


class LobbyingSource(_NameIndexedSource):
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
    _CACHE_PREFIX = "lobby"
    _CACHE_V = 1   # bump if the cached Lobbying shape changes

    def __init__(self, timeout: float = 20.0, cache_dir: str = ".cache/lda",
                 config: Optional[dict] = None):
        import httpx  # lazy: only needed for live runs
        cfg = (config or {}).get("lobbying", {}) if config else {}
        self._base = str(cfg.get("base_url", "https://lda.gov/api/v1")).rstrip("/")
        super().__init__(
            httpx.AsyncClient(
                timeout=float(cfg.get("timeout", timeout)),
                headers={"Accept": "application/json",
                         "User-Agent": "shortlist lobbying (contact in SEC_IDENTITY)"}),
            Path(cfg.get("cache_dir", cache_dir)))
        self._min_conf = float(cfg.get("match_min_confidence", 0.85))
        self._months = int(cfg.get("trailing_months", 24))
        self._max_pages = int(cfg.get("max_pages_per_year", 4))
        # Keyless LDA allows ~15 req/min; basket runs WILL hit 429. Retry it with
        # Retry-After-aware backoff (the FMP pattern) so coverage degrades to "slow",
        # not "missing". 402 gating and other 4xx are NOT retried.
        self._max_retries = int(cfg.get("max_retries", 2))

    async def _get_json(self, path: str, params: dict) -> Any:
        for attempt in range(self._max_retries + 1):
            r = await self._client.get(f"{self._base}/{path}", params=params)
            if await _retry_after_backoff(r, attempt, self._max_retries):
                continue
            r.raise_for_status()
            return r.json()

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
        res, snap, name = await self._begin(ticker)
        if name is None:
            return res
        today = date.today()
        end = today.isoformat()
        cached, snap.lobbying = self._cached_model(ticker, end, Lobbying, "lb")
        if cached is not None:
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
            res.errors.append(f"{self.name}: {redact_secrets(e)}")
        return res
