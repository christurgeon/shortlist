"""The moat-durability study: cohorts, outcomes, predictors, the controlled regression and the
pass rule. Pure, stdlib-only. Every definition here is fixed by the pre-registration
(docs/audits/2026-10-04-moat-durability-prereg.md) — change the note first, never this file
alone.

THE WRONG METRIC, kept so the next reader meets it: the raw difference in hold rate between the
best and worst third of a predictor (`raw_tercile_spread`). It is large for anything that tracks
ROIC level or sector, and it measures "high-ROIC software stays high-ROIC software". The
deciding number is `fit`'s coefficient, which holds level, sector and size fixed."""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import median, pstdev
from typing import Iterable, Optional

from ..durability import YearRow, fiscal_ends, panel_rows, snapshot
from ._ols import ols

REV_FLOOR = 1e8
HORIZON = 3
HISTORY = 4                      # buckets t-3..t
MIN_HISTORY = 3
# `track` and `stability` are defined from this start year. In 2011 the history buckets reach
# 2008, and XBRL was phased in by filer size from 2009 to 2011: a firm with three observed
# buckets would be an early adopter, ranked against a universe of early adopters.
HISTORY_FROM = 2012
MIN_PEERS = 5
MIN_IC_GROWTH = 1.05             # P6 is defined only when invested capital grew > 5%
DISCOVERY = range(2011, 2018)    # start years; outcomes 2014-2020
HOLDOUT = range(2018, 2022)      # start years; outcomes 2021-2024
BOOT_REPS = 2000
BOOT_SEED = 20261004
PREDICTORS = ("track", "stability", "investment", "share_stability", "gross_margin",
              "incremental_roic")
# `investment` has no registered sign for `compounded`, so it is not tested there.
TESTS = tuple((p, o) for o in ("held", "compounded") for p in PREDICTORS
              if not (p == "investment" and o == "compounded"))
NO_SIC = "none"                  # a firm with no SIC code: in the universe, out of the regressions


@dataclass
class Firm:
    cik: str
    sic: Optional[str]
    masked: bool                                   # financial / REIT / insurer
    snaps: dict[int, dict[int, YearRow]]           # snapshot year -> bucket -> row
    last_bucket: int                               # latest bucket with any fiscal year end


def build_firm(cik: str, sic: Optional[str], masked: bool, facts: dict,
               years: Iterable[int]) -> Optional[Firm]:
    ends = fiscal_ends(facts)
    if not ends:
        return None
    snaps = {}
    for y in years:
        s = snapshot(facts, y)
        if s is not None:
            snaps[y] = s
    return Firm(cik=cik, sic=sic, masked=masked, snaps=snaps, last_bucket=max(ends))


# ---------------------------------------------------------------- cross-section helpers

def quintile_floor(values: Iterable[float]) -> Optional[float]:
    """The lowest value still in the top fifth, or None under 5 values. Ties at the floor are
    all in."""
    xs = sorted(values, reverse=True)
    return xs[len(xs) // 5 - 1] if len(xs) >= 5 else None


def pct_rank(value: float, sorted_values: list[float]) -> float:
    """Share of `sorted_values` (ascending) at or below `value`."""
    return bisect_right(sorted_values, value) / len(sorted_values)


def avg_ranks(values: dict) -> dict:
    """{key -> rank in (0,1)}; ties share the average rank. One value ranks 0.5."""
    order = sorted(values, key=values.get)
    n, out, i = len(order), {}, 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = ((i + j) / 2 + 0.5) / n
        for k in order[i:j + 1]:
            out[k] = r
        i = j + 1
    return out


def _in_universe(row: Optional[YearRow]) -> bool:
    return row is not None and row.status == "ok" and (row.revenue or 0.0) >= REV_FLOOR


def cross_section(firms: Iterable[Firm], snap_year: int, bucket: int, *,
                  masked: bool = True) -> dict[str, float]:
    """{cik -> ROIC} of the universe for `bucket`, as seen in snapshot `snap_year`.
    `masked=False` keeps the financial sectors; only the gates report that size."""
    out = {}
    for f in firms:
        if masked and f.masked:
            continue
        row = f.snaps.get(snap_year, {}).get(bucket)
        if _in_universe(row):
            out[f.cik] = row.roic
    return out


def comparison_panel(rec: dict) -> dict[int, YearRow]:
    """{bucket -> row} for one compacted filer record on its LATEST values, whenever filed,
    from its 10-K rows and its foreign annual forms read as one. NOT THE STUDY'S DATA: the
    study reads the 10-K rows alone, as of a date. This is what SEC frames holds for the filer,
    and only the reproduction gate reads it."""
    merged: dict = defaultdict(list)
    for part in (rec, rec.get("foreign_forms") or {}):
        for tag, node in ((part.get("facts") or {}).get("us-gaap") or {}).items():
            merged[tag] += node["units"]["USD"]
    return panel_rows({"facts": {"us-gaap": {t: {"units": {"USD": rows}}
                                             for t, rows in merged.items()}}}, date.max)


def comparison_count(panels: Iterable[dict[int, YearRow]], bucket: int) -> int:
    """The number of filers with ROIC defined and revenue over the floor in `bucket`, with NO
    sector mask and NO as-of date: the population the frames targets count. `cross_section` is
    lower than those targets by construction (the mask, the 120-day rule, the 10-K filter),
    which a band around them would misread as a data fault."""
    return sum(_in_universe(panel.get(bucket)) for panel in panels)


# ---------------------------------------------------------------- cohort rows

@dataclass
class Row:
    cik: str
    year: int
    sic2: str
    roic: float
    revenue: float
    preds: dict[str, Optional[float]]     # ORIENTED: higher = the registered-favourable end
    state: str                            # observed | low_ic | gap | exit
    held: Optional[bool]
    rank_t3: Optional[float]
    rev_ratio: Optional[float]
    compounded: Optional[bool] = None
    c0: float = 0.0
    c2: float = 0.0
    p: dict[str, float] = field(default_factory=dict)
    sic3: str = NO_SIC                    # only the SIC-3 cut of pass rule 5 reads it


def _sic(f: Firm, digits: int) -> Optional[str]:
    s = (f.sic or "").strip()
    return s.zfill(4)[:digits] if s.isdigit() else None


def _predictors(f: Firm, year: int, hist: dict[int, list[float]],
                floors: dict[int, Optional[float]], peers: dict) -> dict[str, Optional[float]]:
    snap = f.snaps[year]
    now = snap[year]
    seen = [(y, snap[y]) for y in range(year - HISTORY + 1, year + 1)
            if y in snap and snap[y].status == "ok" and floors.get(y) is not None]
    track = stability = None
    if year >= HISTORY_FROM and len(seen) >= MIN_HISTORY:
        track = sum(r.roic >= floors[y] for y, r in seen) / len(seen)
        stability = -pstdev(pct_rank(r.roic, hist[y]) for y, r in seen)

    prev = snap.get(year - 1)
    investment = (-(now.ic / prev.ic - 1.0)
                  if prev is not None and prev.status == "ok" else None)

    share_stability = None
    base = snap.get(year - HORIZON)
    key = _sic(f, 3)
    if (key in peers and peers[key][2] >= MIN_PEERS and base is not None
            and (base.revenue or 0) > 0 and (now.revenue or 0) > 0):
        tot_now, tot_base, _n = peers[key]
        # The RELATIVE change in share (a log ratio), not the absolute one. An absolute change
        # grows with the share itself: on synthetic firms whose growth does not depend on size
        # its rank correlation with the firm's SIC-3 share is -0.82 to -0.88, against +0.05 to
        # +0.09 for this form (docs/audits/scripts/probe_durability_share_form.py). The
        # absolute form would have measured size inside the industry.
        share_stability = -abs(math.log((now.revenue / tot_now) / (base.revenue / tot_base)))

    gross_margin = (now.gross_profit / now.revenue
                    if now.gross_profit is not None and (now.revenue or 0) > 0 else None)

    incremental = None
    if base is not None and base.status == "ok" and now.ic > MIN_IC_GROWTH * base.ic:
        incremental = (now.nopat - base.nopat) / (now.ic - base.ic)

    return {"track": track, "stability": stability, "investment": investment,
            "share_stability": share_stability, "gross_margin": gross_margin,
            "incremental_roic": incremental}


def _peer_totals(firms: Iterable[Firm], year: int) -> dict:
    """{SIC-3 -> (revenue now, revenue 3 years back, n)} over EVERY filer with revenue in both
    years of snapshot `year` — not only the ROIC universe."""
    tot: dict = defaultdict(lambda: [0.0, 0.0, 0])
    for f in firms:
        snap = f.snaps.get(year, {})
        now, base, key = snap.get(year), snap.get(year - HORIZON), _sic(f, 3)
        if (key and now is not None and base is not None
                and (now.revenue or 0) > 0 and (base.revenue or 0) > 0):
            t = tot[key]
            t[0] += now.revenue
            t[1] += base.revenue
            t[2] += 1
    return {k: tuple(v) for k, v in tot.items()}


def build_cohort(firms: list[Firm], year: int) -> list[Row]:
    """The top ROIC quintile at `year`, each firm with its predictors and its state three years
    later. Empty when either cross-section has under 5 firms."""
    hist = {y: sorted(cross_section(firms, year, y).values())
            for y in range(year - HISTORY + 1, year + 1)}
    floors = {y: quintile_floor(v) for y, v in hist.items()}
    later = sorted(cross_section(firms, year + HORIZON, year + HORIZON).values())
    floor_later = quintile_floor(later)
    if floors[year] is None or floor_later is None:
        return []
    now_xs = cross_section(firms, year, year)
    peers = _peer_totals(firms, year)
    rows = []
    for f in firms:
        if now_xs.get(f.cik, float("-inf")) < floors[year]:
            continue
        now = f.snaps[year][year]
        row3 = f.snaps.get(year + HORIZON, {}).get(year + HORIZON)
        held = rank = None
        if row3 is not None and row3.status == "ok":
            state, held, rank = "observed", row3.roic >= floor_later, pct_rank(row3.roic, later)
        elif row3 is not None and row3.status == "low_ic":
            state, held = "low_ic", row3.op_income > 0
        elif f.last_bucket >= year + HORIZON:
            state = "gap"
        else:
            state = "exit"
        ratio = (row3.revenue / now.revenue
                 if row3 is not None and (row3.revenue or 0) > 0 else None)
        rows.append(Row(cik=f.cik, year=year, sic2=_sic(f, 2) or NO_SIC, roic=now.roic,
                        revenue=now.revenue, preds=_predictors(f, year, hist, floors, peers),
                        state=state, held=held, rank_t3=rank, rev_ratio=ratio,
                        sic3=_sic(f, 3) or NO_SIC))
    _finish(rows)
    return rows


def _finish(rows: list[Row]) -> None:
    """Fill the cohort-relative fields: `compounded`, and the within-cohort-year ranks."""
    ratios = [r.rev_ratio for r in rows if r.held is not None and r.rev_ratio is not None]
    cut = median(ratios) if ratios else None
    c0 = avg_ranks({i: r.roic for i, r in enumerate(rows)})
    c2 = avg_ranks({i: r.revenue for i, r in enumerate(rows)})
    for i, r in enumerate(rows):
        r.c0, r.c2 = c0[i], c2[i]
        # A row with no revenue ratio is out of outcome B WHETHER IT HELD OR NOT. A firm that
        # did not hold did not compound, so its B is known without the ratio; but keeping those
        # rows while dropping the held ones would select the sample on outcome A.
        if r.held is not None and r.rev_ratio is not None and cut is not None:
            r.compounded = bool(r.held and r.rev_ratio >= cut)
    for name in PREDICTORS:
        ranks = avg_ranks({i: r.preds[name] for i, r in enumerate(rows)
                           if r.preds[name] is not None})
        for i, rk in ranks.items():
            rows[i].p[name] = rk


# ---------------------------------------------------------------- the regression

def _outcome(r: Row, outcome: str, exits: Optional[bool]) -> Optional[float]:
    """The outcome value, or None when the row is outside this run's sample. `exits` codes the
    'exit' state for the bounds runs (True = all held, False = none); None leaves exits out.
    'gap' rows are never in a sample."""
    if outcome == "rank":
        return r.rank_t3
    if r.state == "exit":
        return None if exits is None else float(exits)
    v = r.held if outcome == "held" else r.compounded
    return None if v is None else float(v)


def sample(rows: Iterable[Row], pred: Optional[str], outcome: str,
           exits: Optional[bool] = None) -> list[tuple[Row, float]]:
    """The rows of one run with their outcome value. With a predictor this is a regression
    sample, and a firm with no SIC code is left out: it has no sector to be held to.
    `pred=None` (the instrument gate, the base rates) keeps it."""
    out = []
    for r in rows:
        y = _outcome(r, outcome, exits)
        if y is None or (pred is not None and (pred not in r.p or r.sic2 == NO_SIC)):
            continue
        out.append((r, y))
    return out


def _demean(samp: list[tuple[Row, float]], cols: list[list[float]], key) -> list[list[float]]:
    out = []
    for col in cols:
        tot: dict = defaultdict(float)
        cnt: dict = defaultdict(int)
        for (r, _), v in zip(samp, col, strict=True):
            tot[key(r)] += v
            cnt[key(r)] += 1
        out.append([v - tot[key(r)] / cnt[key(r)] for (r, _), v in zip(samp, col, strict=True)])
    return out


def fit(samp: list[tuple[Row, float]], pred: str, digits: int = 2) -> float:
    """β: the change in the outcome from the worst to the best rank of `pred`, among firms of
    the same sector and start year, holding ROIC level (rank and rank²) and size.

    THE SECTOR CONTROL IS A FIXED EFFECT PER (SIC-2, START YEAR) CELL: every column is demeaned
    within its cell. A row alone in its cell therefore adds nothing, and small sectors are NOT
    pooled — pooling, or a sector effect that is not also by start year, or an estimated sector
    mean used as a covariate (the note's original C1), each let a predictor that is only a
    sector label earn β (docs/audits/scripts/probe_durability_sector_control.py). `digits=3`
    is the SIC-3 cut that pass rule 5 reads: SIC-2 cells do not hold a sub-industry fixed.

    Raises ValueError on a singular design (for example a predictor that IS the level rank,
    or a sample in which every row is alone in its cell)."""
    if digits not in (2, 3):
        raise ValueError(f"fit: digits must be 2 or 3, got {digits!r}")
    cols = [[y for _, y in samp],
            [r.c0 for r, _ in samp], [r.c0 ** 2 for r, _ in samp],
            [r.c2 for r, _ in samp],
            [r.p[pred] for r, _ in samp]]
    y, *xs = _demean(samp, cols, lambda r: (r.sic2 if digits == 2 else r.sic3, r.year))
    return ols(y, [list(t) for t in zip(*xs, strict=True)])[-1]


def level_slope(samp: list[tuple[Row, float]]) -> float:
    """The instrument gate: the slope of the outcome on the ROIC rank alone, within year."""
    y, x = _demean(samp, [[v for _, v in samp], [r.c0 for r, _ in samp]], lambda r: r.year)
    return ols(y, [[v] for v in x])[-1]


def bootstrap(samp: list[tuple[Row, float]], pred: str, *, reps: int = BOOT_REPS,
              seed: int = BOOT_SEED) -> dict:
    """Resample FIRMS (each carries all its cohort-years). Ranks are fixed from the full
    sample — they are properties of the population cross-section, not of the resample. The
    cell means are NOT fixed: `fit` recomputes them inside every resample."""
    by_cik: dict = defaultdict(list)
    for item in samp:
        by_cik[item[0].cik].append(item)
    ciks = sorted(by_cik)
    rng = random.Random(seed)
    betas, singular = [], 0
    for _ in range(reps):
        draw = [item for c in rng.choices(ciks, k=len(ciks)) for item in by_cik[c]]
        try:
            betas.append(fit(draw, pred))
        except ValueError:
            singular += 1
    if not betas:
        raise ValueError(f"bootstrap: none of {reps} replications could be fitted")
    betas.sort()
    return {"se": pstdev(betas), "lo": betas[int(0.025 * len(betas))],
            "hi": betas[int(0.975 * len(betas)) - 1], "reps": len(betas),
            "singular": singular, "firms": len(ciks)}


def raw_tercile_spread(samp: list[tuple[Row, float]], pred: str) -> float:
    """THE WRONG METRIC (see the module docstring). Reported next to β so the gap is visible."""
    top = [y for r, y in samp if r.p[pred] >= 2 / 3]
    bot = [y for r, y in samp if r.p[pred] < 1 / 3]
    return sum(top) / len(top) - sum(bot) / len(bot)


# ---------------------------------------------------------------- the pass rule

DISC_MIN, DISC_SE = 0.10, 2.0
HOLD_MIN, HOLD_SE = 0.06, 1.64
BOUND_MIN = 0.03
# Rule 5 (amendment 2): at least this share of β must survive SIC-3 x start-year cells, in both
# windows. SIC-2 cells let a predictor that is only a SUB-industry label through: on synthetic
# rows it passed both windows in 89% of worlds, and in 0% to 2% with this rule. A fixed floor
# on the SIC-3 β was measured and NOT adopted (it left 11.5% to 17.5%); β is per unit of
# cohort-wide rank, so only a share of β is the same hurdle for every predictor.
SUB_SHARE = 0.5


def _sub_industry(beta: float, beta_sic3: Optional[float]) -> bool:
    """A cut that could not be computed fails: an arm nobody can check is not passed."""
    return beta_sic3 is not None and beta > 0 and beta_sic3 >= SUB_SHARE * beta


def discovery_rules(beta: float, se: float, bound_held: float, bound_not: float,
                    rank_beta: float, beta_sic3: Optional[float]) -> dict[str, bool]:
    return {"magnitude": beta >= max(DISC_MIN, DISC_SE * se),
            "bounds": min(bound_held, bound_not) >= BOUND_MIN,
            "continuous_sign": rank_beta > 0,
            "sub_industry": _sub_industry(beta, beta_sic3)}


def holdout_rule(beta: float, se: float, beta_sic3: Optional[float]) -> bool:
    return beta >= max(HOLD_MIN, HOLD_SE * se) and _sub_industry(beta, beta_sic3)


def passes(discovery: dict[str, bool], holdout: bool) -> bool:
    return all(discovery.values()) and holdout


def measure(rows: list[Row], pred: str, outcome: str, *, reps: int = BOOT_REPS,
            seed: int = BOOT_SEED, with_bounds: bool) -> dict:
    """Everything the note reports for one predictor, one outcome, one window. Never raises:
    a test that cannot be computed returns "error" beside whatever was computed before the
    failure, and counts as not passed.

    THE BOOTSTRAP MUST BE COMPLETE. A resample that cannot be fitted is not dropped silently:
    the SE over the survivors is too small (one survivor gives 0, under which any positive β
    clears the 2 x SE arm), so one such replication makes the test an error.

    The counts say what the sector control could not use. A missing SIC code kept `n_no_sic`
    rows out of the primary sample and `n_no_sic_exit` exit rows out of the two bounds runs —
    a dead filer is the likeliest to have no code, and the bounds exist to bracket attrition.
    `n_alone_in_cell` of the `n` sample rows are the only row of their (SIC-2, start year) cell
    and carry no weight in β; `n_alone_in_sic3_cell` is the same for the SIC-3 cut.

    `beta_sic3_cells` feeds rule 5. It has its own `try`: when the SIC-3 cut cannot be computed
    the value is None and rule 5 fails, but β and its SE are still reported."""
    samp = sample(rows, pred, outcome)
    cells = Counter((r.sic2, r.year) for r, _ in samp)
    cells3 = Counter((r.sic3, r.year) for r, _ in samp)
    no_sic = [r for r in rows if r.sic2 == NO_SIC and pred in r.p]
    counts = {"n": len(samp),
              "n_no_sic": sum(_outcome(r, outcome, None) is not None for r in no_sic),
              "n_no_sic_exit": sum(r.state == "exit" for r in no_sic),
              "n_alone_in_cell": sum(cells[(r.sic2, r.year)] == 1 for r, _ in samp),
              "n_alone_in_sic3_cell": sum(cells3[(r.sic3, r.year)] == 1 for r, _ in samp)}
    out = dict(counts)
    try:
        out["beta"] = fit(samp, pred)
        try:
            out["raw_tercile_spread"] = raw_tercile_spread(samp, pred)
        except ZeroDivisionError:
            # A third of the predictor is empty (`track` takes at most seven values, and over
            # two thirds of the rows can tie). The wrong metric must not sink the right one.
            out["raw_tercile_spread"] = None
        out.update(bootstrap(samp, pred, reps=reps, seed=seed))
        if out["singular"]:
            raise ValueError(f"bootstrap: {out['singular']} of {reps} replications could not be "
                             "fitted")
        try:
            out["beta_sic3_cells"] = fit(samp, pred, digits=3)
        except ValueError:
            out["beta_sic3_cells"] = None
        if with_bounds:
            out["bound_held"] = fit(sample(rows, pred, outcome, exits=True), pred)
            out["bound_not"] = fit(sample(rows, pred, outcome, exits=False), pred)
            out["rank_beta"] = fit(sample(rows, pred, "rank"), pred)
    except (ValueError, ZeroDivisionError) as e:
        # A singular design or an empty sample fails THIS test; it must not sink a run that
        # has already spent minutes on the others.
        return {**out, "error": f"{type(e).__name__}: {e}"}
    return out


# ---------------------------------------------------------------- gates and descriptives

# Universe size per year on SEC frames with the invested-capital floor, measured 2026-10-04.
# frames returns RESTATED values, so this is a plausibility band, not a target.
FRAMES_UNIVERSE = {2011: 2114, 2012: 2102, 2013: 2052, 2014: 2066, 2015: 1995, 2016: 2161,
                   2017: 2254, 2018: 2214, 2019: 2190, 2020: 2207, 2021: 2359, 2022: 2309,
                   2023: 2245, 2024: 2179}
REPRO_TOL = 0.15
GAP_SPIKE = 0.05
STATES = ("observed", "low_ic", "gap", "exit")


def reproduction_failures(sizes: dict[int, int], expected: dict[int, int] = FRAMES_UNIVERSE,
                          tol: float = REPRO_TOL) -> dict[int, tuple[int, int]]:
    """{year -> (got, expected)} for every year outside +/- `tol`. A missing year counts as 0."""
    return {y: (sizes.get(y, 0), e) for y, e in expected.items()
            if abs(sizes.get(y, 0) - e) > tol * e}


def gap_spikes(rates: dict[int, float]) -> list[int]:
    """Outcome years whose 'gap' rate is more than 5 points above BOTH neighbours — the shape
    a tag-adoption change leaves (the 2018 revenue-tag switch is the known candidate)."""
    return [y for y in sorted(rates)
            if y - 1 in rates and y + 1 in rates
            and rates[y] - rates[y - 1] > GAP_SPIKE and rates[y] - rates[y + 1] > GAP_SPIKE]


def share_alone_in_cell(rows: list[Row], digits: int) -> Optional[float]:
    """Share of the rows with a SIC code that are the only row of their (SIC-`digits`, start
    year) cell, or None with no such row. No outcome and no predictor enters it, so it can be
    read with the gates: it says how much of a cohort the sector control can use at all."""
    coded = [r for r in rows if r.sic2 != NO_SIC]
    if not coded:
        return None
    key = (lambda r: (r.sic2, r.year)) if digits == 2 else (lambda r: (r.sic3, r.year))
    cells = Counter(key(r) for r in coded)
    return sum(cells[key(r)] == 1 for r in coded) / len(coded)


def zero_debt_share(firms: Iterable[Firm], year: int,
                    ciks: Optional[set[str]] = None) -> Optional[float]:
    """Share of the universe at `year` (or of `ciks` within it) with debt of zero, or None when
    that group is empty. NO DEBT TAG READS AS ZERO DEBT, and so does debt reported under a tag
    outside the panel. Either makes invested capital too small and ROIC too high, which favours
    entry to the cohort; this share bounds how many firm-years that can touch."""
    xs = cross_section(firms, year, year)
    debts = [f.snaps[year][year].debt for f in firms
             if f.cik in xs and (ciks is None or f.cik in ciks)]
    return sum(d == 0 for d in debts) / len(debts) if debts else None


def no_revenue_count(firms: Iterable[Firm], year: int, min_assets: float = 0.0) -> int:
    """Firms outside the masked sectors with ROIC defined at `year` and NO revenue value: the
    universe needs revenue for its floor, so a filer that reports revenue under a tag outside
    the panel is left out of it. The reproduction gate cannot see this (its targets read the
    same tags). With no `min_assets` the count also holds small filers that have no revenue to
    report; with one it is the firms that would most likely have met the floor."""
    return sum(1 for f in firms if not f.masked
               and (row := f.snaps.get(year, {}).get(year)) is not None
               and row.status == "ok" and row.revenue is None and row.assets >= min_assets)


def state_shares(rows: list[Row]) -> dict[str, float]:
    return {s: sum(r.state == s for r in rows) / len(rows) for s in STATES} if rows else {}


def state_rate_by_tercile(rows: list[Row], pred: str, state: str) -> list[Optional[float]]:
    """Share of rows in `state` in the worst, middle and best third of a predictor. A state
    whose share differs across the thirds can move β: 'exit' is what the bounds runs bracket;
    'gap' is outside them; 'low_ic' is coded by the sign of operating income."""
    out: list[Optional[float]] = []
    for lo, hi in ((0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.01)):
        grp = [r for r in rows if pred in r.p and lo <= r.p[pred] < hi]
        out.append(sum(r.state == state for r in grp) / len(grp) if grp else None)
    return out


def exit_rate_by_tercile(rows: list[Row], pred: str) -> list[Optional[float]]:
    return state_rate_by_tercile(rows, pred, "exit")
