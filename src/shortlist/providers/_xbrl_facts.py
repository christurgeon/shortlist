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
        node = gaap[concept] if concept in gaap else dei.get(concept)
        if not node:
            continue
        chosen: dict[str, tuple[date, float]] = {}  # end -> (filed, val) within THIS concept
        for unit in units:
            for f in node.get("units", {}).get(unit, []):
                form = f.get("form")
                if form not in _ANNUAL_FORMS:
                    continue
                filed, end, val = f.get("filed"), f.get("end"), f.get("val")
                if None in (filed, end, val):
                    continue
                try:
                    filed_d = _d(filed)
                    _d(end)                      # validate the key parses (skip if not)
                    if not instant:
                        start = f.get("start")
                        if start is None:
                            continue
                        span = (_d(end) - _d(start)).days
                        if not (_MIN_PERIOD_DAYS <= span <= _MAX_PERIOD_DAYS):
                            continue
                except ValueError:
                    continue   # malformed ISO date in this SEC fact -> skip the row
                if filed_d > as_of:
                    continue
                prev = chosen.get(end)
                # strict `>`: a same-date tie keeps the first row seen (SEC array order)
                if prev is None or filed_d > prev[0]:
                    chosen[end] = (filed_d, float(val))
        for end, (_filed, val) in chosen.items():
            out.setdefault(end, val)   # first concept in priority order owns this end; later aliases skipped
    return out


# ---------------------------------------------------------------------------
# US-GAAP concept families, PRIORITY order (annual_series fills each end from the
# first that reports it — these are NOT merged). Verified tag choices/aliases.
# ---------------------------------------------------------------------------
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
NET_INCOME = ["NetIncomeLoss"]
OCF = ["NetCashProvidedByUsedInOperatingActivities",
       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment",
         "PaymentsToAcquireProductiveAssets"]
DILUTED_EPS = ["EarningsPerShareDiluted"]
GROSS_PROFIT = ["GrossProfit"]
COGS = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]
EQUITY = ["StockholdersEquity"]
LT_DEBT = ["LongTermDebtNoncurrent", "LongTermDebt"]
CUR_DEBT = ["LongTermDebtCurrent", "DebtCurrent"]
OP_INCOME = ["OperatingIncomeLoss"]
INTEREST = ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
INCOME_TAX = ["IncomeTaxExpenseBenefit"]
SHARES_OUT = ["EntityCommonStockSharesOutstanding"]                   # dei, instant, unit="shares"
WTD_DIL_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]  # us-gaap, unit="shares"; scaffolded for later EPS/dilution work


# ---------------------------------------------------------------------------
# End-aligned dict helpers — all cross-metric math goes through these so it
# aligns by fiscal end (shared keys only), never by list position.
# ---------------------------------------------------------------------------

def align_fcf(ocf: dict, capex: dict) -> dict:
    """FCF = OCF - capex per fiscal end reporting both (capex is a positive outflow)."""
    return {e: ocf[e] - capex[e] for e in ocf if e in capex}


def sum_aligned(a: dict, b: dict) -> dict:
    """a + b per fiscal end reporting both."""
    return {e: a[e] + b[e] for e in a if e in b}


def ratio_latest(num: dict, den: dict) -> Optional[float]:
    """num/den at the LATEST fiscal end both report. None if no common end or den==0."""
    common = set(num) & set(den)
    if not common:
        return None
    e = max(common)   # ISO-8601 keys sort chronologically -> max() is the latest end
    return (num[e] / den[e]) if den[e] != 0.0 else None


def desc(series: dict) -> list[float]:
    """Series values newest-first (descending fiscal end) — for cagr/persistence."""
    return [series[e] for e in sorted(series, reverse=True)]


def latest(series: dict) -> Optional[float]:
    """Value at the most recent fiscal end, or None if empty."""
    return series[max(series)] if series else None
