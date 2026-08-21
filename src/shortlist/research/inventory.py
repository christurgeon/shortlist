"""Deterministic inventory context line for the research brief (research-only).

Reframes the inventory BALANCE and its trend as a caveated context line for Claude to
reconcile against the MD&A — NOT a scored or flagged signal. Lives in the prompt, never
in the grounding haystack (a computed number must not pass quote-verification as a
filing fact — the reverse_dcf discipline). Pure; no I/O.

WHY THIS REPORTS A LEVEL AND NOT A CASH-FLOW BRIDGE
---------------------------------------------------
An earlier design computed "FCF excluding the inventory build" and flagged names where
the build fully explained a negative FCF. That was cut. Measured on HDSN FY2024->FY2025
(docs/PLAN_INVENTORY_DECOMPOSITION.md §0.2):

    FCF ex-inventory only      +26.26M -> +32.70M   (+24%, "improved")
    FCF ex-(inventory+AR+AP)   +26.66M -> +16.30M   (-39%, declined)

The improvement is an artifact of stripping the inventory outflow while keeping a
+20.2M payables inflow. A single-line adjustment is not a neutral decomposition, so
this module does not compute one and callers should not reconstruct one from it.

What it reports instead is DAYS INVENTORY OUTSTANDING, which is a ratio of two figures
that move together and therefore does not depend on which working-capital lines anyone
chooses to strip. On the same HDSN data DIO runs 326.9 / 317.6 / 205.0 / 268.9 days
across FY2022-25 — FY2024 is the outlier, not FY2025, which is the honest version of
the "restocking, not deterioration" read.

KNOWN LIMITATION — DIO needs `gross_profit`, which EDGAR does not supply
------------------------------------------------------------------------
COGS is derived as `revenue - gross_profit`, and `gross_profit` is NOT in
EdgarFinancials: `_merge_statements` year-joins it in from FMP. So on an FMP-gated or
rate-limited run (a 402 on a gated symbol, or a 429 against the 250/day free quota) the
DIO leg silently abstains and only the balance/revenue divergence renders. That is
graceful degradation, not a failure — but it means the most useful leg is missing
exactly when the free tier is under pressure.

Fixing it means extracting cost of revenue from EDGAR, which is NOT free: the natural
source is `us-gaap_GrossProfit` (LULU has it non-dimensional; HDSN and FISV report only
`CostOfGoodsAndServicesSold`, and on LULU every such row is a segment breakdown with
dimension=True). Supplying gross_profit from EDGAR would change a SCORED input
(gross_margin) and its merge precedence, so it is deliberately out of scope here and
logged to TODO.md instead.
"""
from __future__ import annotations

from typing import Optional

# A balance-sheet inventory change and a cash-flow inventory line do not reconcile for
# every filer: FX translation and non-cash write-downs both sit between them (LULU FY26
# moved +258.7M on the balance sheet against a +188.7M cash-flow line, a 70.0M FX gap;
# HDSN's gap is a recurring LCNRV write-down). This module never pairs the two, which is
# why no such caveat is needed here — but see the module docstring before adding one.


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
