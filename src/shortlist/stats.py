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
