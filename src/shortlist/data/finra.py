"""Shared FINRA ConsolidatedShortInterest primitives.

The dataset URLs, paging limit and pure row helpers live here so the async
``FinraSource`` (``data/sources/finra.py``) and any sync fetcher share one definition of
the FINRA row shape and disk-cache contract. No HTTP here; callers do their own fetch.

Row fields used downstream: ``symbolCode``, ``settlementDate``,
``currentShortPositionQuantity``, ``previousShortPositionQuantity``,
``averageDailyVolumeQuantity``, ``daysToCoverQuantity``, ``stockSplitFlag``,
``revisionFlag``.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .models import ShortInterest

# ConsolidatedShortInterest, not the frozen OTC-only EquityShortInterest.
FINRA_DATA_URL = "https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest"
FINRA_PARTS_URL = "https://api.finra.org/partitions/group/otcMarket/name/ConsolidatedShortInterest"
FINRA_PAGE = 5000   # FINRA record-max-limit (paginate past it)


def latest_partition(payload: Any) -> Optional[str]:
    """The newest settlement date from the /partitions/ payload (a partition key — it
    cannot be sorted in the data query, so discover it here)."""
    parts = (payload or {}).get("availablePartitions") or []
    dates = [p["partitions"][0] for p in parts if p.get("partitions")]
    return max(dates) if dates else None


def norm_symbol(sym: str) -> str:
    """Collapse separators so BRK.B / BRK-B / BRKB all match one key."""
    return (sym or "").upper().replace("-", "").replace(".", "")


def num(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    try:
        f = float(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None
    # Reject inf/nan ("inf"/"NaN" parse via float) so a non-finite value can never slip
    # past downstream numeric guards (keeps the aggregator's "never raises" invariant true).
    return f if (f is None or math.isfinite(f)) else None


def flag(row: dict, key: str) -> bool:
    return str(row.get(key, "")).strip().upper() in ("Y", "YES", "TRUE", "1")


def row_to_si(row: dict) -> ShortInterest:
    return ShortInterest(
        settlement_date=row.get("settlementDate"),
        short_shares=num(row, "currentShortPositionQuantity"),
        prev_short_shares=num(row, "previousShortPositionQuantity"),
        avg_daily_volume=num(row, "averageDailyVolumeQuantity"),
        days_to_cover=num(row, "daysToCoverQuantity"),
        split_flag=flag(row, "stockSplitFlag"),
        revised=flag(row, "revisionFlag"),
    )


def index_rows(rows: list) -> dict:
    """Index raw rows by normalized symbol (last-occurrence-wins on a symbol collision)."""
    return {norm_symbol(r["symbolCode"]): r for r in rows if r.get("symbolCode")}
