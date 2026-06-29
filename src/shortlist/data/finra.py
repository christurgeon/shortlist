"""Shared FINRA ConsolidatedShortInterest primitives.

The dataset URLs, the paging limit, and the pure row helpers live here so the async
harness ``FinraSource`` (``data/sources.py``) and the sync scout fetcher
(``scout/short_interest.py``) agree on ONE definition of the FINRA row shape and the
disk-cache contract — the ``_form4.py`` / ``_edgar_facts.py`` shared-leaf precedent
(``CLAUDE.md`` "edit … not in two places"). No HTTP here; both callers do their own
(async vs sync) fetch and reuse these helpers.

Row fields used downstream: ``symbolCode``, ``settlementDate``,
``currentShortPositionQuantity``, ``previousShortPositionQuantity``,
``averageDailyVolumeQuantity``, ``daysToCoverQuantity``, ``stockSplitFlag``,
``revisionFlag``.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .models import ShortInterest

# The ConsolidatedShortInterest dataset (NOT the frozen OTC-only EquityShortInterest).
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
