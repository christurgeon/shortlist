"""Shared return type for the EDGAR clients."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Emission:
    """One signal firing for one ticker on one session."""
    ticker: str
    signal: str            # e.g. "edgar:activist_13d", "edgar:13f_new_position"
    strength: float        # 0..1, source-normalized
    evidence: str          # human-readable
    is_discovery: bool     # True = can originate an unknown ticker; False = confluence-only
    cik: str | None = None  # optional EDGAR CIK (carried by filing-based clients so a caller
                            # can re-resolve a renamed ticker; None elsewhere)
    meta: dict = field(default_factory=dict)  # optional per-emission facts (e.g. the 8-K
                            # accession + matched items); {} for clients that carry none
