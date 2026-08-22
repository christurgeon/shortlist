"""Deterministic sell-side rating-revision context line for the research brief.

Renders the CHANGE in analyst rating counts over the recommendation window, never the
levels. Two constraints force that:

- The levels on StockMetrics are merged field-by-field across sources (`fmp` ahead of
  `finnhub`) while the deltas come from a single Finnhub payload, so rendering them
  together would pair one vendor's panel with another's.
- Brav & Lehavy: the *level* of sell-side optimism is negatively related to realised
  returns, the *revision* is the predictive half. The level already reaches the scorer
  through `upside_to_target`; this line exists to supply the half that does not.

Prompt context only — never the grounding haystack, never scored, never flagged.
Pure; no I/O.
"""
from __future__ import annotations

from typing import Optional


def context_line(m, cfg: Optional[dict]) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled / no window)."""
    if not cfg or not cfg.get("enabled", False):
        return None
    months = getattr(m, "rating_months", None)
    if not months:                       # None or 0 -> no derivable window
        return None
    buy = getattr(m, "rating_buy_delta", None) or 0
    hold = getattr(m, "rating_hold_delta", None) or 0
    sell = getattr(m, "rating_sell_delta", None) or 0
    span = f"{months} month" + ("" if months == 1 else "s")
    if buy == hold == sell == 0:
        # An explicit "unchanged" beats omitting the line: with no line at all the
        # model cannot tell a flat consensus from an unfetched one.
        change = "unchanged"
    else:
        change = (f"buy {buy:+d}, hold {hold:+d}, sell {sell:+d} "
                  f"(net {buy - sell:+d} analysts)")
    return (f"Analyst revision (context only — change in sell-side rating counts over "
            f"the last {span}, not filing text): {change}. Weigh the DIRECTION OF "
            "REVISION, not the level of optimism; the price-target level already "
            "feeds the value sub-score.")
