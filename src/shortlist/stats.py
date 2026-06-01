from __future__ import annotations

from statistics import mean, pstdev
from typing import Optional


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
