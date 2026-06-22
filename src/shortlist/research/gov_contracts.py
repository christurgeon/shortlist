"""Deterministic gov-contracts context line for the research brief (research-only).

Reframes the USAspending gov-contract obligations as a caveated context line for
Claude to reconcile against the business — NOT a scored or flagged signal. Lives
in the prompt, never in the grounding haystack (a computed number must not pass
quote-verification as a filing fact — the reverse_dcf discipline). Pure; no I/O.
"""
from __future__ import annotations

from typing import Optional


def _money_b(x: float) -> str:
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def context_line(m, cfg: Optional[dict]) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled / low
    confidence / no material obligations)."""
    if not cfg or not cfg.get("enabled", False):
        return None
    ttm = getattr(m, "gov_contract_ttm_usd", None)
    conf = getattr(m, "gov_contract_match_confidence", None)
    if not ttm:                                      # None or 0 -> abstain
        return None
    if conf is None or conf < float(cfg.get("min_confidence", 0.8)):
        return None
    n_recip = getattr(m, "gov_contract_recipient_count", None) or 1
    who = (f"primary recipient matched at {conf:.2f}" if n_recip == 1
           else f"summed across {n_recip} recipients (primary matched at {conf:.2f})")
    parts = [f"~{_money_b(ttm)} federal contract obligations (trailing 12m, {who})"]
    tr = getattr(m, "gov_contract_to_revenue", None)
    if tr is not None:
        parts.append(f"~{tr * 100:.0f}% of revenue")
    yoy = getattr(m, "gov_contract_yoy_growth", None)
    if yoy is not None:
        parts.append(f"{yoy * 100:+.0f}% vs prior 12m")
    body = "; ".join(parts)
    caveat = ""
    if getattr(m, "gov_contract_truncated", None):
        total = getattr(m, "gov_contract_total_txns", None)
        of = f" of ~{total:,}" if total else ""
        caveat = (f" [PARTIAL: top actions{of} by size only — figure is approximate "
                  f"and omits smaller/negative (de-obligation) actions]")
    return (f"Government contracts: {body}.{caveat} Attribution is fuzzy and excludes "
            f"subsidiaries booked under other names — reconcile against the "
            f"business (a new award is a tailwind; a recompete loss a risk).")
