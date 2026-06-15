"""Deterministic federal-lobbying context line for the research brief (research-only).

Reframes Senate LDA lobbying spend as a caveated context line for Claude to
reconcile against the business — NOT a scored or flagged signal. Lives in the
prompt, never in the grounding haystack (a computed number must not pass quote-
verification as a filing fact — the reverse_dcf discipline). Pure; no I/O.
"""
from __future__ import annotations

from typing import Optional


def _money(x: float) -> str:
    return f"${x / 1e6:,.1f}M" if abs(x) >= 1e6 else f"${x / 1e3:,.0f}K"


def context_line(m, cfg: Optional[dict]) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled / low
    confidence / no material spend)."""
    if not cfg or not cfg.get("enabled", False):
        return None
    ttm = getattr(m, "lobbying_ttm_usd", None)
    conf = getattr(m, "lobbying_match_confidence", None)
    if ttm is None or not ttm:                       # None or 0 -> abstain
        return None
    if conf is None or conf < float(cfg.get("min_confidence", 0.85)):
        return None
    n_reg = getattr(m, "lobbying_registrant_count", None) or 1
    parts = [f"~{_money(ttm)} on federal lobbying (trailing 12m, {n_reg} "
             f"registrant{'s' if n_reg != 1 else ''}, client matched at {conf:.2f})"]
    yoy = getattr(m, "lobbying_yoy_growth", None)
    if yoy is not None:
        parts.append(f"{yoy * 100:+.0f}% vs prior 12m")
    body = "; ".join(parts)
    caveat = ""
    if getattr(m, "lobbying_truncated", None):
        total = getattr(m, "lobbying_total_filings", None)
        of = f" of ~{total:,}" if total else ""
        caveat = f" [PARTIAL: capped paging{of} filings — figure is a lower bound]"
    return (f"Federal lobbying: {body}.{caveat} Rising lobbying spend signals "
            f"regulatory/policy engagement (a moat or a risk depending on the "
            f"thesis) — reconcile against the business, not a standalone signal.")
