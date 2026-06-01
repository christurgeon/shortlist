"""Aggregate signal emissions into candidates and prefilter the set."""
from __future__ import annotations

from typing import Callable

from .models import Candidate, Emission


def aggregate(emissions: list[Emission], weights: dict[str, float]) -> list[Candidate]:
    """Group emissions by ticker into Candidates; weight by per-signal config weight.

    Weight lookup uses the emission's exact ``signal`` string as the key into
    ``weights``; if the signal is absent from the map, a default weight of 1.0 is
    used.  The caller is responsible for keying ``weights`` with the same signal
    names that emitters produce.
    """
    by_ticker: dict[str, Candidate] = {}
    for e in emissions:
        c = by_ticker.setdefault(e.ticker, Candidate(ticker=e.ticker))
        c.add(e, weights.get(e.signal, 1.0))
    return list(by_ticker.values())


def prefilter(candidates: list[Candidate],
              in_cooldown: Callable[[str], bool],
              is_held: Callable[[str], bool]) -> list[Candidate]:
    """Drop booster-only candidates (no discovery signal), cooldown, and held names."""
    kept = []
    for c in candidates:
        if not c.has_discovery:      # a booster alone cannot originate a candidate
            continue
        if in_cooldown(c.ticker) or is_held(c.ticker):
            continue
        kept.append(c)
    return kept
