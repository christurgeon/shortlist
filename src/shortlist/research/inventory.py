"""Deterministic inventory context line for the research brief (research-only).

Reframes the inventory BALANCE and its trend as a caveated context line for Claude to
reconcile against the MD&A — NOT a scored or flagged signal. Lives in the prompt, never
in the grounding haystack (a computed number must not pass quote-verification as a
filing fact — the reverse_dcf discipline). Pure; no I/O.

WHY THIS REPORTS A LEVEL AND NOT A CASH-FLOW BRIDGE
---------------------------------------------------
"FCF excluding the inventory build" is not a neutral decomposition: stripping one
working-capital line while keeping the others can flip the sign of the trend. This
module does not compute one, and callers should not reconstruct one from it. It reports
DAYS INVENTORY OUTSTANDING instead, a ratio that does not depend on which lines are
stripped. Evidence: docs/design/PLAN_INVENTORY_DECOMPOSITION.md §0.2.

KNOWN LIMITATION — DIO needs `gross_profit`, which EDGAR does not supply
------------------------------------------------------------------------
COGS is `revenue - gross_profit`, and `gross_profit` is year-joined in from FMP by
`_merge_statements`. On an FMP-gated or rate-limited run the DIO leg abstains and only
the balance/revenue divergence renders. Supplying gross_profit from EDGAR would change
a SCORED input (gross_margin) and its merge precedence, so it is out of scope here.
"""
from __future__ import annotations

from typing import Optional

# A balance-sheet inventory change and a cash-flow inventory line do not reconcile:
# FX translation and non-cash write-downs sit between them. This module never pairs
# the two.


def _money(x: float) -> str:
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def _dio(entry: dict) -> Optional[float]:
    """Days inventory outstanding: inventory / (COGS/365), COGS = revenue - gross_profit.

    None whenever any input is missing or COGS is non-positive. A non-positive COGS is
    not a data error worth reporting — it is a filer whose gross profit meets or exceeds
    revenue (financials, royalty trusts), where DIO has no meaning."""
    inv = entry.get("inventory")
    rev = entry.get("revenue")
    gp = entry.get("gross_profit")
    if inv is None or rev is None or gp is None:
        return None
    cogs = rev - gp
    if cogs <= 0:
        return None
    return inv / (cogs / 365.0)


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or not old:
        return None
    return (new - old) / abs(old)


def context_line(m, cfg: Optional[dict]) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled, no series, or no
    inventory reported — a filer with no inventory line is the normal case for a bank
    or a services company, not a failure)."""
    if not cfg or not cfg.get("enabled", False):
        return None
    series = getattr(m, "financial_series", None) or []
    if len(series) < 1:
        return None
    cur = series[0]
    if cur.get("inventory") is None:
        return None
    prior = series[1] if len(series) > 1 else {}

    parts = [f"balance {_money(cur['inventory'])}"]
    if prior.get("inventory") is not None:
        growth = _pct_change(cur["inventory"], prior["inventory"])
        parts[0] = f"balance {_money(prior['inventory'])} -> {_money(cur['inventory'])}"
        if growth is not None:
            parts[0] += f" ({growth * 100:+.0f}%)"
        rev_growth = _pct_change(cur.get("revenue"), prior.get("revenue"))
        if rev_growth is not None:
            # The divergence is the point: inventory outgrowing revenue is the shape
            # worth asking the MD&A about, in either direction.
            parts.append(f"revenue {rev_growth * 100:+.0f}% over the same period")

    dio_now, dio_prior = _dio(cur), _dio(prior)
    if dio_now is not None:
        leg = f"days inventory outstanding {dio_now:.0f}"
        if dio_prior is not None:
            leg = f"days inventory outstanding {dio_prior:.0f} -> {dio_now:.0f}"
        # Prior years give the reader a base rate, so a one-year move is not read as a
        # trend on its own. Oldest-last, matching the newest-first series order.
        older = [f"{d:.0f}" for d in (_dio(e) for e in series[2:5]) if d is not None]
        if older:
            leg += f" (earlier years: {', '.join(older)})"
        parts.append(leg)

    body = "; ".join(parts)
    return (f"Inventory: {body}. Computed from the balance sheet and income statement, "
            f"NOT a filing quote. This is the inventory LEVEL and its trend — it is not "
            f"a free-cash-flow bridge and other working-capital lines are excluded. "
            f"Reconcile against the MD&A: a build can be restocking, buying ahead of "
            f"price, or product that is not selling.")
