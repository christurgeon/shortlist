from __future__ import annotations

from statistics import mean, median, pstdev
from typing import Optional


def median_pe(pes: list[Optional[float]], min_points: int = 2) -> Optional[float]:
    """Median of historical annual PE ratios; the `pe_median_5y` anchor that
    `pe_vs_history()` measures the current PE against.

    Drops falsy values (None and 0 — a 0 PE is a missing/degenerate reading);
    keeps negatives. Returns None with fewer than `min_points` usable points.
    This is the single source of truth for the formula used by BOTH the screener
    FMP provider and the harness FMP source — do not reinline it."""
    vals = [p for p in pes if p]
    if len(vals) < min_points:
        return None
    return median(vals)


def avg_roic(roics: list[Optional[float]], min_points: int = 2) -> Optional[float]:
    """Mean of historical annual ROIC; the `roic_5y_avg` moat input that smooths
    a one-off TTM spike into a persistent return-on-capital read.

    Drops None values; keeps zeros and negatives (a real low/negative ROIC year).
    Returns None with fewer than `min_points` usable points."""
    vals = [r for r in roics if r is not None]
    if len(vals) < min_points:
        return None
    return mean(vals)


def cagr(series: list[Optional[float]], most_recent_first: bool = True,
         min_points: int = 3) -> Optional[float]:
    """Compound annual growth rate over a financial series.

    Drops None values (a missing year), then requires >= `min_points` usable
    points. Returns None when either endpoint is <= 0, because CAGR is undefined
    across a sign change (a swing through zero makes the ratio meaningless) — the
    caller's weight-redistribution handles the gap. `most_recent_first=True`
    matches `Statements`' newest-first ordering. Single source of truth for the
    growth-rate legs in BOTH the screener provider and the harness bridge."""
    vals = [v for v in (series or []) if v is not None]
    if len(vals) < min_points:
        return None
    if most_recent_first:
        vals = list(reversed(vals))
    start, end = vals[0], vals[-1]
    if start <= 0 or end <= 0:
        return None
    n = len(vals) - 1
    return (end / start) ** (1 / n) - 1.0


def growth_persistence(series: list[Optional[float]], most_recent_first: bool = True,
                       min_points: int = 3) -> Optional[float]:
    """0..1 consistency proxy: the fraction of consecutive year-over-year periods
    that grew. Sign-safe (works through negative/zero years where `cagr` can't),
    so it rewards a steady compounder over one that booked the same CAGR via a
    single spike year. Drops None values; needs >= `min_points` usable points.
    Note: dropping a None makes the years on either side of the gap adjacent, so a
    missing year is treated as a single YoY step rather than a break — acceptable
    given statement gaps are rare."""
    vals = [v for v in (series or []) if v is not None]
    if len(vals) < min_points:
        return None
    if most_recent_first:
        vals = list(reversed(vals))
    pairs = list(zip(vals, vals[1:], strict=False))  # (older, newer)
    ups = sum(1 for older, newer in pairs if newer > older)
    return ups / len(pairs)


def gross_margin_stability(margins: list[float]) -> Optional[float]:
    """0..1 moat proxy: higher = steadier gross margins.

    `max(0, 1 - stdev/mean)` over >=3 yearly gross margins (population stdev, to
    match the screener FMP provider). Returns None with <3 points or zero mean.
    This is the single source of truth for the formula used by BOTH the screener
    provider and the harness bridge — do not reinline it."""
    if len(margins) < 3:
        return None
    avg = mean(margins)
    if not avg:
        return None
    return max(0.0, 1.0 - (pstdev(margins) / avg))


def piotroski_f(*, net_income, ocf, total_debt, gross_profit, revenue,
                most_recent_first: bool = True) -> tuple[Optional[int], Optional[int]]:
    """Core-6 Piotroski-inspired fundamental-quality score (asset-free, equity-free).

    Each arg is a financial series; index alignment is positional (newest-first by
    default), matching the rest of stats.py — a rare missing year makes neighbors
    adjacent (same tradeoff cagr/growth_persistence make).

    Six tests; F1-F3 are single-year LEVELS (latest year only), F4-F6 are 1-year
    DELTAS (latest t vs prior t-1, requiring revenue>0 each year — the only division
    guard):
      F1 net_income>0, F2 ocf>0, F3 ocf>net_income (accruals quality),
      F4 net margin rising (net_income/revenue), F5 debt-intensity falling
      (total_debt/revenue), F6 gross margin rising (gross_profit/revenue).

    Deliberately asset-free AND equity-free: total assets is not extracted on either
    stack, and equity denominators darken/distort on buyback-heavy firms (see spec
    §3). Revenue is the natural denominator; a non-positive (zero or negative) revenue year abstains the three delta legs.

    Returns RAW (won, evaluated) — no min-legs floor here, so this leaf needs no
    config (the floor is applied by consumers in scoring/backtest). A 1-year input
    yields (<=3, <=3) from the level legs; when NO leg is evaluable (all series empty)
    it returns (None, None) — the model's 'no data' sentinel. Pure; no I/O."""
    def _series(s):
        s = list(s or [])
        return s if most_recent_first else list(reversed(s))

    ni = _series(net_income)
    cf = _series(ocf)
    dbt = _series(total_debt)
    gp = _series(gross_profit)
    rev = _series(revenue)

    def _t(s):   # (latest, prior) or (None, None) if no prior year
        return (s[0], s[1]) if len(s) >= 2 else (None, None)

    won = 0
    legs = 0

    # F1 profitability: net income positive (level; abstain if latest value missing)
    if ni and ni[0] is not None:
        legs += 1
        if ni[0] > 0:
            won += 1
    # F2 cash generation: OCF positive (level; abstain if latest value missing)
    if cf and cf[0] is not None:
        legs += 1
        if cf[0] > 0:
            won += 1
    # F3 accruals quality: OCF > net income (level)
    if ni and cf and ni[0] is not None and cf[0] is not None:
        legs += 1
        if cf[0] > ni[0]:
            won += 1
    # F4 net-margin trend: net_income/revenue rising (delta; revenue>0 guard)
    ni_t, ni_p = _t(ni)
    rev_t, rev_p = _t(rev)
    if None not in (ni_t, ni_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (ni_t / rev_t) > (ni_p / rev_p):
            won += 1
    # F5 debt-intensity trend: total_debt/revenue falling (delta; revenue>0 guard)
    d_t, d_p = _t(dbt)
    if None not in (d_t, d_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (d_t / rev_t) < (d_p / rev_p):
            won += 1
    # F6 gross-margin trend: gross_profit/revenue rising (delta; revenue>0 guard)
    gp_t, gp_p = _t(gp)
    if None not in (gp_t, gp_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (gp_t / rev_t) > (gp_p / rev_p):
            won += 1

    return (won, legs) if legs else (None, None)
