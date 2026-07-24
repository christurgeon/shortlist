from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from .. import finra as _finra
from ..diskcache import read_json_cache, write_json_cache
from ..models import SourceResult, TickerSnapshot
from .base import Source

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


# --- FINRA short interest (pure helpers) ----------------------------------
# Single-sourced in data/finra.py so the sync scout fetcher shares one row-shape
# definition (CLAUDE.md "edit … not in two places"). Re-exported under the historical
# _finra_* names so call sites + tests that import them from here keep working.
_finra_latest_partition = _finra.latest_partition
_finra_norm_symbol = _finra.norm_symbol
_finra_row_to_si = _finra.row_to_si
_finra_index = _finra.index_rows
