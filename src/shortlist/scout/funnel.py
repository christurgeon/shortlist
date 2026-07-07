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


def apply_veto(candidates: list[Candidate],
               veto_map: dict[str, dict]) -> tuple[list[Candidate], list[Candidate]]:
    """Drop candidates carrying a fresh negative-item 8-K (spec 2026-07-07 §4). Runs
    BETWEEN prefilter and select so a vetoed name never consumes a deep-screen slot —
    the next-ranked candidate backfills it in select().

    `veto_map` is UPPER ticker -> {"last_date","items","adsh"} (built by
    daily._negative_veto_sweep from ScoutState.eightk_negative). An empty map is the
    identity — the byte-identical pre-feature funnel. Returns (kept, vetoed); the CALLER
    names each vetoed ticker in manifest.notes (this stage stays pure — no state, no I/O).
    """
    if not veto_map:
        return list(candidates), []
    kept: list[Candidate] = []
    vetoed: list[Candidate] = []
    for c in candidates:
        (vetoed if c.ticker.upper() in veto_map else kept).append(c)
    return kept, vetoed
