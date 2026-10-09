"""Moat-durability definitions: ROIC, invested capital, the fiscal-year bucket, the
point-in-time snapshot, the compaction of SEC company facts and the two predictors that
passed. Pure, stdlib-only.

SHARED BY CONSTRUCTION: the Phase 0 measurement (`backtest/durability_study.py`) and the
`/deep` ROIC-persistence section (`durability_profile.py`) both call these functions, so a
rendered number can never be computed on a different basis from the measured one. That holds
on RAW company facts too: every reader drops the rows the study never saw (`_study_rows`), and
the live path compacts with the study's own `compact_facts` first.

THIS FILE IS IN `probe_durability.py: CODE`: an edit changes the code digest the study's raw
outputs name. Code that only the live section needs belongs in `durability_profile.py`.

ROIC HERE IS DELIBERATELY NOT `providers/_xbrl_facts._roic_series`. That helper needs BOTH a
long-term and a current debt tag (a debt-free filer gets no ROIC), adds the current portion on
top of `LongTermDebt` (which already includes it), and returns a list with no fiscal-year keys.
It is left untouched so the backtest stays byte-identical.

Spec: docs/superpowers/specs/2026-10-04-moat-durability-design.md §4.
Pre-registration: docs/audits/2026-10-04-moat-durability-prereg.md."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import pstdev
from typing import Iterable, Optional

from .providers._xbrl_facts import (
    ASSETS,
    COGS,
    EQUITY,
    GROSS_PROFIT,
    OP_INCOME,
    REVENUE,
    _gross_profit,
    annual_series,
)

# A flat rate, so the ROIC ranking equals the pre-tax ranking: no tax-tag noise and no 2018
# statutory-rate change can move a firm between quintiles.
TAX = 0.21
# Measured 2026-10-04 on SEC frames: with no floor the top quintile holds ROICs up to 6,190%;
# at 10% of assets the maximum is 190% and the 3-year stay rate is unchanged (45.7% -> 46.1%).
IC_FLOOR_FRAC = 0.10
AS_OF_LAG_DAYS = 120
_BUCKET_SHIFT_DAYS = 182

LT_NONCURRENT = ["LongTermDebtNoncurrent"]
LT_TOTAL = ["LongTermDebt"]
CUR_DEBT = ["LongTermDebtCurrent", "DebtCurrent"]

# The study is 10-K filers only. `_xbrl_facts.annual_series` also admits 20-F and 20-F/A, so
# the filter lives HERE, in front of every reader, and not only in the compaction.
STUDY_FORMS = frozenset({"10-K", "10-K/A"})
KEEP_TAGS = tuple(REVENUE + OP_INCOME + EQUITY + ASSETS + LT_NONCURRENT + LT_TOTAL + CUR_DEBT
                  + GROSS_PROFIT + COGS)
_FIELDS = ("start", "end", "val", "filed", "form")
_NEEDS_ONE_OF = tuple(REVENUE + OP_INCOME)

REV_FLOOR = 1e8
HISTORY = 4                      # buckets t-3..t
MIN_HISTORY = 3
# `track` and `stability` are defined from this start year. In 2011 the history buckets reach
# 2008, and XBRL was phased in by filer size from 2009 to 2011: a firm with three observed
# buckets would be an early adopter, ranked against a universe of early adopters.
HISTORY_FROM = 2012
_FAR_FUTURE = date(9999, 12, 31)


def fy_bucket(end_iso: str) -> int:
    """Calendar year of (fiscal year end - 182 days): a January 2012 year end is 2011."""
    return (date.fromisoformat(end_iso) - timedelta(days=_BUCKET_SHIFT_DAYS)).year


@dataclass(frozen=True)
class YearRow:
    """One fiscal year of one firm, as known at a snapshot's as-of date."""
    end: str
    revenue: Optional[float]
    op_income: Optional[float]
    equity: Optional[float]
    debt: float
    assets: Optional[float]
    gross_profit: Optional[float]

    @property
    def ic(self) -> Optional[float]:
        return None if self.equity is None else self.equity + self.debt

    @property
    def status(self) -> str:
        """'ok' (ROIC defined), 'low_ic' (invested capital <= 0 or under the floor), or
        'missing' (an input is not tagged, or not yet filed at the as-of date)."""
        if self.op_income is None or self.equity is None or self.assets is None:
            return "missing"
        if self.ic <= 0 or self.ic < IC_FLOOR_FRAC * self.assets:
            return "low_ic"
        return "ok"

    @property
    def nopat(self) -> Optional[float]:
        return None if self.op_income is None else self.op_income * (1.0 - TAX)

    @property
    def roic(self) -> Optional[float]:
        return self.nopat / self.ic if self.status == "ok" else None


def _debt(end: str, noncur: dict, total: dict, cur: dict) -> float:
    """Total debt at `end`. `LongTermDebt` already includes the current portion, so the current
    tag is added only to the NON-current tag. No debt tag at all means debt-free, not missing."""
    if end in noncur:
        return noncur[end] + cur.get(end, 0.0)
    if end in total:
        return total[end]
    return cur.get(end, 0.0)


def facts_on_forms(raw: dict, forms: frozenset[str]) -> Optional[dict]:
    """The KEEP_TAGS facts of `raw` in USD on `forms`, with only the fields a reader uses, or
    None when no revenue or operating-income fact is left."""
    gaap = (raw.get("facts") or {}).get("us-gaap") or {}
    out: dict[str, dict] = {}
    for tag in KEEP_TAGS:
        rows = ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
        kept = [{k: f[k] for k in _FIELDS if k in f} for f in rows if f.get("form") in forms]
        if kept:
            out[tag] = {"units": {"USD": kept}}
    if not any(t in out for t in _NEEDS_ONE_OF):
        return None
    return {"facts": {"us-gaap": out}}


def compact_facts(raw: dict) -> Optional[dict]:
    """The us-gaap USD facts the study needs, 10-K forms only, or None when the filer has no
    annual revenue or operating-income fact at all. ONE FUNCTION FOR BOTH PATHS: the bulk
    archive of the study and the per-CIK response of the live `/deep` section."""
    return facts_on_forms(raw, STUDY_FORMS)


def _study_rows(facts: dict) -> dict:
    """`facts` with only the rows the study reads: a 10-K form, and not filed before its own
    period ended.

    NO VALUE CAN BE KNOWN BEFORE ITS PERIOD ENDS, so such a row is a context typed with the
    wrong year. Left in, a year-long period ending in 2105 gives a dead filer a "last year end"
    in 2105, and its exit reads as a gap; one ending next year puts last year's values in a
    bucket that the firm never reported. Rows with no `filed` or no `end` are left for
    `annual_series` to drop.

    THE FORM FILTER is a no-op on a compacted record and decisive on raw company facts, where
    a later-filed 20-F/A row would replace the 10-K value
    (tests/test_durability.py::test_a_foreign_annual_form_is_never_read_from_raw_facts)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    kept = {}
    for tag, node in gaap.items():
        units = {u: [r for r in rows if r.get("form") in STUDY_FORMS
                     and (not (r.get("filed") and r.get("end")) or r["filed"] >= r["end"])]
                 for u, rows in (node.get("units") or {}).items()}
        kept[tag] = {**node, "units": units}
    return {**facts, "facts": {**facts.get("facts", {}), "us-gaap": kept}}


def count_filed_before_period_end(facts: dict) -> int:
    """The number of rows, on any form, that were filed before their own period ended.
    `_study_rows` drops them. Reported with the gates: real facts are expected to satisfy
    end <= filed, and a large count would say that expectation is wrong."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    return sum(1 for node in gaap.values() for rows in (node.get("units") or {}).values()
               for r in rows if r.get("filed") and r.get("end") and r["filed"] < r["end"])


def _fiscal_ends(facts: dict) -> dict[int, str]:
    ends = set(annual_series(facts, OP_INCOME, _FAR_FUTURE)) | set(
        annual_series(facts, REVENUE, _FAR_FUTURE))
    out: dict[int, str] = {}
    for e in sorted(ends):
        out[fy_bucket(e)] = e
    return out


def fiscal_ends(facts: dict) -> dict[int, str]:
    """{bucket -> fiscal year end} over EVERY filing, whenever filed. Used only to locate a
    firm's year ends (and so its as-of dates) — never to read a value. Two ends in one bucket
    (a fiscal-year change): the later end wins."""
    return _fiscal_ends(_study_rows(facts))


def as_of_for(end_iso: str) -> date:
    return date.fromisoformat(end_iso) + timedelta(days=AS_OF_LAG_DAYS)


def panel_rows(facts: dict, as_of: date) -> dict[int, YearRow]:
    """{bucket -> YearRow} from the facts filed on or before `as_of` (the existing
    point-in-time rule in `annual_series`). Every year a reader at `as_of` would have,
    comparatives included, and nothing filed later."""
    return _panel_rows(_study_rows(facts), as_of)


def _panel_rows(facts: dict, as_of: date) -> dict[int, YearRow]:
    rev = annual_series(facts, REVENUE, as_of)
    oi = annual_series(facts, OP_INCOME, as_of)
    eq = annual_series(facts, EQUITY, as_of, instant=True)
    assets = annual_series(facts, ASSETS, as_of, instant=True)
    noncur = annual_series(facts, LT_NONCURRENT, as_of, instant=True)
    total = annual_series(facts, LT_TOTAL, as_of, instant=True)
    cur = annual_series(facts, CUR_DEBT, as_of, instant=True)
    gp = _gross_profit(facts, as_of)
    out: dict[int, YearRow] = {}
    for e in sorted(set(rev) | set(oi)):
        out[fy_bucket(e)] = YearRow(
            end=e, revenue=rev.get(e), op_income=oi.get(e), equity=eq.get(e),
            debt=_debt(e, noncur, total, cur), assets=assets.get(e), gross_profit=gp.get(e))
    return out


def snapshot(facts: dict, bucket: int) -> Optional[dict[int, YearRow]]:
    """The firm's panel as of (its `bucket` fiscal year end + 120 days), or None when the firm
    has no fiscal year end in that bucket."""
    facts = _study_rows(facts)
    end = _fiscal_ends(facts).get(bucket)
    if end is None:
        return None
    return _panel_rows(facts, as_of_for(end))


# ---------------------------------------------------------------- the universe and the predictors

def quintile_floor(values: Iterable[float]) -> Optional[float]:
    """The lowest value still in the top fifth, or None under 5 values. Ties at the floor are
    all in."""
    xs = sorted(values, reverse=True)
    return xs[len(xs) // 5 - 1] if len(xs) >= 5 else None


def pct_rank(value: float, sorted_values: list[float]) -> float:
    """Share of `sorted_values` (ascending) at or below `value`."""
    return bisect_right(sorted_values, value) / len(sorted_values)


def in_universe(row: Optional[YearRow]) -> bool:
    return row is not None and row.status == "ok" and (row.revenue or 0.0) >= REV_FLOOR


def history_predictors(snap: dict[int, YearRow], year: int, hist: dict[int, list[float]],
                       floors: dict[int, Optional[float]],
                       ) -> tuple[Optional[float], Optional[float], int, int]:
    """(track, stability, years in the top fifth, years seen) over buckets year-3..year.
    `hist[y]` is the ascending ROIC universe of bucket y and `floors[y]` its top-fifth floor. A
    year is seen when its ROIC is defined and its universe has a floor. `track` (the share of
    seen years in the top fifth) and `stability` (minus the spread of the percentile rank, so
    that a STEADY rank is high) are None before HISTORY_FROM or under MIN_HISTORY seen years."""
    seen = [(y, snap[y]) for y in range(year - HISTORY + 1, year + 1)
            if y in snap and snap[y].status == "ok" and floors.get(y) is not None]
    in_top = sum(r.roic >= floors[y] for y, r in seen)
    if year < HISTORY_FROM or len(seen) < MIN_HISTORY:
        return None, None, in_top, len(seen)
    return (in_top / len(seen), -pstdev(pct_rank(r.roic, hist[y]) for y, r in seen),
            in_top, len(seen))


def investment(snap: dict[int, YearRow], year: int) -> Optional[float]:
    """Minus the growth of invested capital in the last year, so that LOW growth is high.
    None unless the prior year's ROIC is defined in this snapshot: a condition the
    pre-registration does not state (verdict Addendum 1)."""
    prev = snap.get(year - 1)
    if prev is None or prev.status != "ok":
        return None
    return -(snap[year].ic / prev.ic - 1.0)

