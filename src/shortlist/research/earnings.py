"""Deterministic earnings-execution context line for the research brief (research-only).

Reframes Finnhub earnings-surprise history + next-report proximity as a caveated
context line for Claude to weigh — NOT a scored or flagged signal. Lives in the
prompt, never in the grounding haystack (a computed number must not pass quote-
verification as a filing fact — the reverse_dcf discipline). Pure; no I/O.
"""
from __future__ import annotations

from typing import Optional


def context_line(m, cfg: Optional[dict]) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled / no quarters)."""
    if not cfg or not cfg.get("enabled", False):
        return None
    q = getattr(m, "earnings_quarters", None)
    if not q:                                # None or 0 -> abstain
        return None
    parts = []
    beats = getattr(m, "earnings_beats", None)
    if beats is not None:
        parts.append(f"beat estimates in {beats}/{q} recent quarters")
    avg = getattr(m, "earnings_avg_surprise_pct", None)
    if avg is not None:
        last = getattr(m, "earnings_last_surprise_pct", None)
        tail = f", latest {last:+.1f}%" if last is not None else ""
        parts.append(f"avg surprise {avg:+.1f}%{tail}")
    days = getattr(m, "earnings_days_to_next", None)
    if days is not None:
        parts.append(f"next report in ~{days}d")
    if not parts:
        return None
    return ("Earnings execution: " + "; ".join(parts)
            + ". A consistent beat record + magnitude is a quality/drift signal; "
            "an imminent report is a near-term catalyst (timing risk).")
