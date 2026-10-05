"""The moat-durability study: cohorts, outcomes, predictors, the controlled regression and the
pass rule. Pure, stdlib-only. Every definition here is fixed by the pre-registration
(docs/audits/2026-10-04-moat-durability-prereg.md) — change the note first, never this file
alone.

THE WRONG METRIC, kept so the next reader meets it: the raw difference in hold rate between the
best and worst third of a predictor (`raw_tercile_spread`). It is large for anything that tracks
ROIC level or sector, and it measures "high-ROIC software stays high-ROIC software". The
deciding number is `fit`'s coefficient, which holds level, sector and size fixed."""
from __future__ import annotations

import random
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Iterable, Optional

from ..durability import YearRow, fiscal_ends, snapshot
from ._ols import ols

REV_FLOOR = 1e8
HORIZON = 3
HISTORY = 4                      # buckets t-3..t
MIN_HISTORY = 3
MIN_PEERS = 5
MIN_IC_GROWTH = 1.05             # P6 is defined only when invested capital grew > 5%
MIN_SECTOR_ROWS = 20
DISCOVERY = range(2011, 2018)    # start years; outcomes 2014-2020
HOLDOUT = range(2018, 2022)      # start years; outcomes 2021-2024
BOOT_REPS = 2000
BOOT_SEED = 20261004
PREDICTORS = ("track", "stability", "investment", "share_stability", "gross_margin",
              "incremental_roic")
# `investment` has no registered sign for `compounded`, so it is not tested there.
TESTS = tuple((p, o) for o in ("held", "compounded") for p in PREDICTORS
              if not (p == "investment" and o == "compounded"))


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


def cross_section(firms: Iterable[Firm], snap_year: int, bucket: int) -> dict[str, float]:
    """{cik -> ROIC} of the universe for `bucket`, as seen in snapshot `snap_year`."""
    out = {}
    for f in firms:
        if f.masked:
            continue
        row = f.snaps.get(snap_year, {}).get(bucket)
        if row is not None and row.status == "ok" and (row.revenue or 0.0) >= REV_FLOOR:
            out[f.cik] = row.roic
    return out


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
    if len(seen) >= MIN_HISTORY:
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
        share_stability = -abs(now.revenue / tot_now - base.revenue / tot_base)

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
        rows.append(Row(cik=f.cik, year=year, sic2=_sic(f, 2) or "none", roic=now.roic,
                        revenue=now.revenue, preds=_predictors(f, year, hist, floors, peers),
                        state=state, held=held, rank_t3=rank, rev_ratio=ratio))
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
    out = []
    for r in rows:
        y = _outcome(r, outcome, exits)
        if y is not None and (pred is None or pred in r.p):
            out.append((r, y))
    return out


def sector_rates(samp: list[tuple[Row, float]]) -> dict[tuple[str, int], float]:
    """C1: {(cik, year) -> the mean outcome of the row's SIC-2 sector}, leaving out the row's
    own start year and its own firm. Sectors under 20 rows pool into 'other'. A sector with
    nothing left after the exclusion falls back to the sample mean."""
    size: dict = defaultdict(int)
    for r, _ in samp:
        size[r.sic2] += 1
    sec = {s: (s if n >= MIN_SECTOR_ROWS else "other") for s, n in size.items()}
    n: dict = defaultdict(int)
    h: dict = defaultdict(float)
    for r, y in samp:
        s = sec[r.sic2]
        for k in ((s,), (s, "y", r.year), (s, "c", r.cik), (s, r.year, r.cik)):
            n[k] += 1
            h[k] += y
    overall = sum(y for _, y in samp) / len(samp)
    out = {}
    for r, _ in samp:
        s = sec[r.sic2]
        ks = ((s,), (s, "y", r.year), (s, "c", r.cik), (s, r.year, r.cik))
        cnt = n[ks[0]] - n[ks[1]] - n[ks[2]] + n[ks[3]]
        tot = h[ks[0]] - h[ks[1]] - h[ks[2]] + h[ks[3]]
        out[(r.cik, r.year)] = tot / cnt if cnt > 0 else overall
    return out


def _demean_by_year(samp: list[tuple[Row, float]], cols: list[list[float]]) -> list[list[float]]:
    out = []
    for col in cols:
        tot: dict = defaultdict(float)
        cnt: dict = defaultdict(int)
        for (r, _), v in zip(samp, col, strict=True):
            tot[r.year] += v
            cnt[r.year] += 1
        out.append([v - tot[r.year] / cnt[r.year] for (r, _), v in zip(samp, col, strict=True)])
    return out


def fit(samp: list[tuple[Row, float]], pred: str,
        c1: Optional[dict[tuple[str, int], float]] = None) -> float:
    """β: the change in the outcome from the worst to the best rank of `pred`, holding ROIC
    level (rank and rank²), sector and size, with start-year fixed effects. Raises ValueError
    on a singular design (for example a predictor that IS the level rank)."""
    c1 = sector_rates(samp) if c1 is None else c1
    cols = [[y for _, y in samp],
            [r.c0 for r, _ in samp], [r.c0 ** 2 for r, _ in samp],
            [c1[(r.cik, r.year)] for r, _ in samp], [r.c2 for r, _ in samp],
            [r.p[pred] for r, _ in samp]]
    y, *xs = _demean_by_year(samp, cols)
    return ols(y, [list(t) for t in zip(*xs, strict=True)])[-1]


def level_slope(samp: list[tuple[Row, float]]) -> float:
    """The instrument gate: the slope of the outcome on the ROIC rank alone, within year."""
    y, x = _demean_by_year(samp, [[v for _, v in samp], [r.c0 for r, _ in samp]])
    return ols(y, [[v] for v in x])[-1]


def bootstrap(samp: list[tuple[Row, float]], pred: str, *, reps: int = BOOT_REPS,
              seed: int = BOOT_SEED) -> dict:
    """Resample FIRMS (each carries all its cohort-years). Ranks and C1 are fixed from the full
    sample — they are properties of the population cross-section, not of the resample."""
    c1 = sector_rates(samp)
    by_cik: dict = defaultdict(list)
    for item in samp:
        by_cik[item[0].cik].append(item)
    ciks = sorted(by_cik)
    rng = random.Random(seed)
    betas, singular = [], 0
    for _ in range(reps):
        draw = [item for c in rng.choices(ciks, k=len(ciks)) for item in by_cik[c]]
        try:
            betas.append(fit(draw, pred, c1))
        except ValueError:
            singular += 1
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


def discovery_rules(beta: float, se: float, bound_held: float, bound_not: float,
                    rank_beta: float) -> dict[str, bool]:
    return {"magnitude": beta >= max(DISC_MIN, DISC_SE * se),
            "bounds": min(bound_held, bound_not) >= BOUND_MIN,
            "continuous_sign": rank_beta > 0}


def holdout_rule(beta: float, se: float) -> bool:
    return beta >= max(HOLD_MIN, HOLD_SE * se)


def passes(discovery: dict[str, bool], holdout: bool) -> bool:
    return all(discovery.values()) and holdout


def measure(rows: list[Row], pred: str, outcome: str, *, reps: int = BOOT_REPS,
            seed: int = BOOT_SEED, with_bounds: bool) -> dict:
    """Everything the note reports for one predictor, one outcome, one window. Never raises:
    a test that cannot be computed returns {"n", "error"} and counts as not passed."""
    samp = sample(rows, pred, outcome)
    try:
        out = {"n": len(samp), "beta": fit(samp, pred),
               "raw_tercile_spread": raw_tercile_spread(samp, pred),
               **bootstrap(samp, pred, reps=reps, seed=seed)}
        if with_bounds:
            out["bound_held"] = fit(sample(rows, pred, outcome, exits=True), pred)
            out["bound_not"] = fit(sample(rows, pred, outcome, exits=False), pred)
            out["rank_beta"] = fit(sample(rows, pred, "rank"), pred)
    except (ValueError, ZeroDivisionError) as e:
        # A singular design or an empty sample fails THIS test; it must not sink a run that
        # has already spent minutes on the others.
        return {"n": len(samp), "error": f"{type(e).__name__}: {e}"}
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


def state_shares(rows: list[Row]) -> dict[str, float]:
    return {s: sum(r.state == s for r in rows) / len(rows) for s in STATES} if rows else {}


def exit_rate_by_tercile(rows: list[Row], pred: str) -> list[Optional[float]]:
    """Share of 'exit' rows in the worst, middle and best third of a predictor. Attrition that
    differs across the thirds is what the bounds run exists to catch."""
    out: list[Optional[float]] = []
    for lo, hi in ((0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.01)):
        grp = [r for r in rows if pred in r.p and lo <= r.p[pred] < hi]
        out.append(sum(r.state == "exit" for r in grp) / len(grp) if grp else None)
    return out
