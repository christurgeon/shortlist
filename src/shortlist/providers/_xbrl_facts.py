# src/shortlist/providers/_xbrl_facts.py
"""Pure transform: a parsed SEC companyfacts dict -> a point-in-time annual
fundamentals panel -> the scalars shortlist.models.StockMetrics carries.

Every series is a {fiscal_end_iso: value} dict so cross-metric math aligns by
fiscal end, never by list position. Values pass through verbatim — XBRL USD facts
are absolute dollars, like FMP/Finnhub*1e6.

POINT-IN-TIME RULE: a fact is usable at `as_of` only if filed <= as_of; within a
concept, for each distinct fiscal-period `end` the value from the LATEST such
filing wins (restatement-aware, never look-ahead).

ALIASES ARE PRIORITY, NOT A MERGE: `concepts` is a fallback list in priority
order; each fiscal end is filled by the highest-priority concept that reports it
(Revenues / SalesRevenueNet / RevenueFromContract... are different line items)."""
from __future__ import annotations

from datetime import date
from typing import Optional

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}
_MIN_PERIOD_DAYS = 350   # admits 52/53-week fiscal years; excludes quarters/half-years
_MAX_PERIOD_DAYS = 380   # (transition/stub-period annuals are rare and dropped)


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def annual_series(facts: dict, concepts: list[str], as_of: date, *,
                  instant: bool = False,
                  units: tuple[str, ...] = ("USD",)) -> dict[str, float]:
    """{fiscal_end_iso: value} via priority-with-fallback across `concepts`.
    `instant=True` for balance-sheet concepts (no `start` to length-check)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    out: dict[str, float] = {}
    for concept in concepts:                       # priority order
        node = gaap.get(concept) or dei.get(concept)
        if not node:
            continue
        chosen: dict[str, tuple[date, float]] = {}  # end -> (filed, val) within THIS concept
        for unit in units:
            for f in node.get("units", {}).get(unit, []):
                form, filed, end, val = (f.get("form"), f.get("filed"),
                                         f.get("end"), f.get("val"))
                if form not in _ANNUAL_FORMS or None in (filed, end, val):
                    continue
                if _d(filed) > as_of:
                    continue
                if not instant:
                    start = f.get("start")
                    if start is None:
                        continue
                    span = (_d(end) - _d(start)).days
                    if not (_MIN_PERIOD_DAYS <= span <= _MAX_PERIOD_DAYS):
                        continue
                prev = chosen.get(end)
                if prev is None or _d(filed) > prev[0]:   # latest filing within concept
                    chosen[end] = (_d(filed), float(val))
        for end, (_filed, val) in chosen.items():
            out.setdefault(end, val)   # higher-priority concept (seen first) keeps the end
    return out
