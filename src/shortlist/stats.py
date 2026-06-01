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
    pairs = list(zip(vals, vals[1:]))  # (older, newer)
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
