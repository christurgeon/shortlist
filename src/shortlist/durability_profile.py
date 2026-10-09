"""Where one company stands against the population the moat-durability study measured: the
profile behind the `/deep` "ROIC persistence" section. Pure apart from reading the committed
reference table.

EVERY NUMBER IS COMPUTED WITH `shortlist/durability.py`, the study's own basis, on company
facts that went through the study's own `compact_facts`. This module is deliberately NOT in
`probe_durability.py: CODE`: nothing here feeds a study number, so it can change without
moving the digest the raw outputs name.

WHAT THE SECTION MAY SAY is fixed by docs/audits/2026-10-04-moat-durability-verdict.md and its
addenda: a display line, never a scoring leg, gate, flag or discovery list. So this module
gives a company's THIRD among top-fifth firms and never a probability for the company: the
measured effect is a straight-line fit within sector and cohort year, and a per-name number
would extrapolate it.

THREE GUARDS HERE ARE NOT IN THE STUDY, because the study never met their cases:
- an unknown SIC abstains (in the study 12,523 of 12,539 filers had one; live, a failed SIC
  fetch would let a bank through the sector mask);
- `investment` is dropped unless the two year ends are about a year apart (YEAR_GAP_DAYS);
- the reference table is for ONE fiscal year. A company one year past it is read against it
  and told so; one further out, or one whose latest year on file is BEFORE it, abstains."""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .durability import (
    HISTORY,
    REV_FLOOR,
    YearRow,
    as_of_for,
    history_predictors,
    investment,
    panel_rows,
)
from .sectors import _as_int, resolve_bucket

TABLE_SCHEMA = 1
_TABLE_PATH = Path(__file__).with_name("durability_table.json")
_YEAR_KEYED = ("universe", "floors")
_TABLE_KEYS = ("table_year", "universe", "floors", "cohort", "cohorts", "effects", "passed",
               "predictors_tested", "tests_run", "discovery_survivors")

# Days between two consecutive fiscal year ends. A fiscal-year change puts two year ends in
# one bucket and the later one wins, so "the year before" can be two years back. No member of
# the reference cohort is outside this range (tests/test_durability_equivalence.py), so the
# guard changes nothing the name is ranked against.
YEAR_GAP_DAYS = (350, 380)
# The latest year end plus 120 days is when the study reads a year. A year after THAT with no
# newer year on file, the company has stopped filing or the facts are stale.
STALE_AFTER_DAYS = 365

# Why the section is or is not shown. A CLOSED SET: `research/durability.py` holds one
# sentence for each, and a test fails when the two lists differ.
SHOWN = "shown"
UNAVAILABLE = "unavailable"
SIC_UNKNOWN = "sic_unknown"
SECTOR_NOT_COVERED = "sector_not_covered"
NO_ANNUAL_FACTS = "no_annual_facts"
STALE = "stale"
LATEST_YEAR_UNUSABLE = "latest_year_unusable"
LOW_CAPITAL = "low_capital"
REVENUE_BELOW_FLOOR = "revenue_below_floor"
REFERENCE_OUT_OF_DATE = "reference_out_of_date"
BEFORE_REFERENCE = "before_reference"
NOT_TOP_FIFTH = "not_top_fifth"
NO_PREDICTOR = "no_predictor"
FACTS_LAG_FILING = "facts_lag_filing"
NOT_SHOWN = (UNAVAILABLE, SIC_UNKNOWN, SECTOR_NOT_COVERED, NO_ANNUAL_FACTS, STALE,
             LATEST_YEAR_UNUSABLE, LOW_CAPITAL, REVENUE_BELOW_FLOOR, REFERENCE_OUT_OF_DATE,
             BEFORE_REFERENCE, NOT_TOP_FIFTH, NO_PREDICTOR, FACTS_LAG_FILING)


# ---------------------------------------------------------------- the reference table

def table_source() -> str:
    """The text of the committed table, or "" when it cannot be read. The brief cache key
    hashes it, so it must not raise."""
    try:
        return _TABLE_PATH.read_text()
    except (OSError, ValueError):
        return ""


def _ascending(values) -> bool:
    return (isinstance(values, list) and len(values) > 0
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
            and all(a <= b for a, b in zip(values, values[1:], strict=False)))


def parse_table(text: str) -> Optional[dict]:
    """The table with its year keys as ints, or None when the text is not a usable table of
    this schema. BUILT BY `docs/audits/scripts/build_durability_table.py`, never edited by
    hand: `tests/test_durability_equivalence.py` rebuilds it from the committed study data."""
    try:
        table = json.loads(text)
        if table["schema"] != TABLE_SCHEMA or any(k not in table for k in _TABLE_KEYS):
            return None
        for key in _YEAR_KEYED:
            table[key] = {int(y): v for y, v in table[key].items()}
        years = set(table["universe"])
        cohort = table["cohort"]
        usable = (years == set(table["floors"]) and table["table_year"] == max(years)
                  and all(_ascending(v) for v in table["universe"].values())
                  and all(isinstance(v, float) for v in table["floors"].values())
                  and _ascending(cohort["investment"]) and _ascending(cohort["stability"]))
        return table if usable else None
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None


def load_table() -> Optional[dict]:
    """Parsed on every call (a few milliseconds, once per brief). NOT CACHED: a cache would
    hand every caller the same mutable dict, and would keep a None from a table that was
    unreadable once."""
    return parse_table(table_source())


# ---------------------------------------------------------------- the profile

@dataclass(frozen=True)
class DurabilityProfile:
    period_end: str                     # the latest reported fiscal year end
    bucket: int                         # the study's fiscal-year bucket of that end
    reference_year: int                 # the table's year: the universe and floor it is read against
    roic: float
    floor: float                        # the top-fifth floor of the reference year
    universe_n: int
    has_debt: bool                      # False: no debt in the tagged data, capital is book equity
    capital_growth: Optional[float]     # IC(t) / IC(t-1) - 1; NOT oriented
    investment_third: Optional[int]     # 0 fastest-growing, 1 middle, 2 slowest-growing
    stability_spread: Optional[float]   # spread (pstdev) of the ROIC percentile rank, 0..1
    stability_third: Optional[int]      # 0 least steady, 1 middle, 2 steadiest
    years_seen: int


def cuts(ascending: list[float]) -> tuple[float, float]:
    """(the lowest member value outside the bottom third, the lowest member value in the top
    third). A MEMBER's third is the study's own: its mid-rank among the cohort, `rank >= 2/3`
    the top and `rank < 1/3` the bottom, tested in integers so that a tie on a boundary cannot
    move with a rounding error. With no member in a third its cut is +inf."""
    n, middle, top = len(ascending), float("inf"), float("inf")
    for value in ascending:
        twice_rank = bisect_left(ascending, value) + bisect_right(ascending, value)
        if middle == float("inf") and 3 * twice_rank >= 2 * n:
            middle = value
        if 3 * twice_rank >= 4 * n:
            top = value
            break
    return middle, top


def third(value: float, ascending: list[float]) -> int:
    """0, 1 or 2: the third `value` falls in, 2 = highest. ONE DEFINITION with `cut_points`: a
    value is in the top third when it is at or above the lowest top-third member, so the label
    can never disagree with the cut values the section prints beside it."""
    middle, top = cuts(ascending)
    if value >= top:
        return 2
    return 0 if value < middle else 1


def cut_points(table: dict) -> dict[str, tuple[float, float]]:
    """{predictor -> (a, b)}, NOT oriented: a capital growth (or a rank spread) of `a` or less
    is the slowest-growing (steadiest) third, and one over `b` the fastest-growing (least
    steady) third."""
    out = {}
    for name in ("investment", "stability"):
        middle, top = cuts(table["cohort"][name])
        out[name] = (-top, -middle)
    return out


def sector_reason(sic, config: dict) -> str:
    code = _as_int(sic)
    if code is None:
        return SIC_UNKNOWN
    return SECTOR_NOT_COVERED if resolve_bucket(code, config) != "unknown" else ""


def _latest(compacted: dict, today: date) -> tuple[Optional[tuple[int, dict[int, YearRow]]], str, dict]:
    """((bucket, snapshot), "", detail) for the latest reported year, read as the study reads
    it: at the year end plus 120 days, or today when that is sooner. A fact filed after that
    date cannot change the profile."""
    rows = panel_rows(compacted, today)
    if not rows:
        return None, NO_ANNUAL_FACTS, {}
    t = max(rows)
    end = rows[t].end
    detail = {"period_end": end}
    as_of = as_of_for(end)
    if as_of + timedelta(days=STALE_AFTER_DAYS) < today:
        return None, STALE, detail
    snap = panel_rows(compacted, min(today, as_of))
    now = snap.get(t)
    # A different year end in the bucket at the as-of date is a fiscal-year change filed late.
    if now is None or now.end != end or now.status == "missing" or now.revenue is None:
        return None, LATEST_YEAR_UNUSABLE, detail
    if now.status == "low_ic":
        return None, LOW_CAPITAL, detail
    if now.revenue < REV_FLOOR:
        return None, REVENUE_BELOW_FLOOR, detail
    return (t, snap), "", detail


def _adjacent_investment(snap: dict[int, YearRow], t: int) -> Optional[float]:
    inv = investment(snap, t)
    if inv is None:
        return None
    gap = (date.fromisoformat(snap[t].end) - date.fromisoformat(snap[t - 1].end)).days
    return inv if YEAR_GAP_DAYS[0] <= gap <= YEAR_GAP_DAYS[1] else None


def profile(compacted: dict, today: date, table: Optional[dict], *, sic, config: dict,
            max_gap: int = 1) -> tuple[Optional[DurabilityProfile], str, dict]:
    """(profile, SHOWN, {}) or (None, a NOT_SHOWN code, the detail its sentence needs).

    `compacted` is the output of `durability.compact_facts`. A name whose fiscal year is past
    the table's year is ranked against that year, up to `max_gap` years.

    A NAME WHOSE LATEST YEAR ON FILE IS BEFORE THE TABLE'S YEAR ABSTAINS (a company in the
    weeks before its new 10-K). The table does hold lists for the three years before its own,
    but they are those years AS SEEN IN THE TABLE-YEAR SNAPSHOT: they omit every firm that
    left in between, so their floor is not that year's floor (fiscal 2024: 15.92% in the
    table against 15.75% in its own snapshot), and the cohort the thirds come from is the
    table year's. Shown, the section would name a population that is not the study's.

    IT CAN RAISE on malformed facts (a `val` that is not a number, a list where a dict is
    expected): `annual_series` does. `research/durability.py` is where that is caught."""
    if table is None:
        return None, UNAVAILABLE, {}
    reason = sector_reason(sic, config)
    if reason:
        return None, reason, {}
    latest, reason, detail = _latest(compacted, today)
    if latest is None:
        return None, reason, detail
    t, snap = latest
    table_year = table["table_year"]
    if t < table_year:
        return None, BEFORE_REFERENCE, {**detail, "table_year": table_year}
    if t - table_year > max_gap:
        return None, REFERENCE_OUT_OF_DATE, {**detail, "table_year": table_year}

    def ref(y: int) -> int:
        return min(y, table_year)

    now, floor = snap[t], table["floors"][table_year]
    if now.roic < floor:
        return None, NOT_TOP_FIFTH, {**detail, "roic": now.roic, "floor": floor,
                                     "table_year": table_year}
    years = [y for y in range(t - HISTORY + 1, t + 1) if ref(y) in table["universe"]]
    hist = {y: table["universe"][ref(y)] for y in years}
    floors = {y: table["floors"][ref(y)] for y in years}
    _track, stability, _in_top, seen = history_predictors(snap, t, hist, floors)
    inv = _adjacent_investment(snap, t)
    if inv is None and stability is None:
        return None, NO_PREDICTOR, detail
    cohort = table["cohort"]
    return DurabilityProfile(
        period_end=now.end, bucket=t, reference_year=table_year, roic=now.roic, floor=floor,
        universe_n=len(table["universe"][table_year]), has_debt=now.debt > 0,
        capital_growth=None if inv is None else -inv,
        investment_third=None if inv is None else third(inv, cohort["investment"]),
        stability_spread=None if stability is None else -stability,
        stability_third=None if stability is None else third(stability, cohort["stability"]),
        years_seen=seen), SHOWN, {}
