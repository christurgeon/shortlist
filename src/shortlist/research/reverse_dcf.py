"""Reverse-DCF implied-growth line for the research brief (research-only).

Single-stage Gordon inversion: g = R - F0/P, where F0 is a normalized base FCF
(median of the last K positive years) and P is market cap. A reframing of FCF
yield into growth-space so the brief can diff price-implied growth against
realized revenue/FCF CAGR. NOT a scored signal; never feeds the composite or rank.
Spec: docs/superpowers/specs/2026-06-13-reverse-dcf-implied-growth-design.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Optional, Sequence


@dataclass
class ImpliedGrowth:
    rate: float                    # g = R - F0/P (perpetual implied FCF growth)
    base_fcf: float                # F0: median of the positive-year window
    n_positive_years: int          # how many positive years went into the median
    disc: float                    # the discount-rate prior used
    floor: float                   # display floor; rate < floor => distressed
    run_rate_understated: bool     # latest FCF > ratio * base
    latest_fcf: Optional[float]    # newest finite FCF cell (for the caveat)
    distressed: bool               # rate < floor: clamp display + relabel


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def implied_growth(fcf_series: Sequence, market_cap,
                   cfg: Optional[dict]) -> Optional[ImpliedGrowth]:
    """Single-stage Gordon implied perpetual FCF growth, or None on any degenerate
    input. `fcf_series` is newest-first free_cash_flow. Pure; no I/O."""
    if not cfg or not cfg.get("enabled", False):
        return None
    if not _finite(market_cap) or market_cap <= 0:
        return None

    disc = float(cfg.get("discount_rate", 0.10))
    base_years = int(cfg.get("base_years", 3))
    floor = float(cfg.get("display_floor", -0.50))
    ratio = float(cfg.get("run_rate_flag_ratio", 1.5))

    positives = [f for f in (fcf_series or []) if _finite(f) and f > 0]
    if not positives:
        return None
    window = positives[:base_years]   # newest-first, so this is the most recent K
    f0 = median(window)
    if not _finite(f0) or f0 <= 0:
        return None

    g = disc - f0 / market_cap
    if not _finite(g):
        return None

    latest = next((f for f in (fcf_series or []) if _finite(f)), None)
    run_rate = latest is not None and latest > ratio * f0

    return ImpliedGrowth(
        rate=g, base_fcf=f0, n_positive_years=len(window), disc=disc, floor=floor,
        run_rate_understated=run_rate, latest_fcf=latest, distressed=g < floor,
    )


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _money_m(x: float) -> str:
    return f"${x / 1e6:,.0f}M"


def format_line(ig: ImpliedGrowth) -> str:
    """One self-disclosing brief line. Distressed names get a clamped label (no
    absurd raw rate); fast growers get a run-rate caveat. Always points at the CAGR."""
    yr = "yr" if ig.n_positive_years == 1 else "yrs"
    base = (f"base = median of {ig.n_positive_years} positive FCF {yr} "
            f"{_money_m(ig.base_fcf)}")
    if ig.distressed:
        return (f"Price-implied FCF growth: price sits below a {_pct(ig.floor)}/yr "
                f"FCF DCF (distressed/special-situation; disc {_pct(ig.disc)}, "
                f"{base}). Compare to revenue/FCF CAGR above.")
    tail = ""
    if ig.run_rate_understated and ig.latest_fcf is not None:
        tail = (f" (base may understate run-rate; latest FCF "
                f"{_money_m(ig.latest_fcf)})")
    return (f"Price-implied FCF growth: market embeds ~{_pct(ig.rate)}/yr perpetual "
            f"FCF growth (disc {_pct(ig.disc)}, {base}){tail}. "
            f"Compare to revenue/FCF CAGR above.")
