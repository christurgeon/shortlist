"""Ticker validation — a dependency-free leaf (no heavy imports), safe on the
always-on bot path. Two layers: a cheap input format pre-check (saves API quota
on typos) and a post-screen no-data predicate (separates unknown symbols from
real-but-thin/gated ones). Consumed by the scout bot today; importable by
daily.py / the harness CLI later.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# 1-6 chars, leads with a letter; allows BRK.B, BF-B. Permissive enough not to
# reject real US symbols; rejects HELLOWORLD, 123, "", $$.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")


def valid_format(t: str) -> bool:
    """True if t is a plausible US ticker. Expects t pre-uppercased (the bot's
    _tickers() upper-cases before calling); lowercase input returns False."""
    return bool(_TICKER_RE.match(t))


def partition_format(tickers: Iterable[str]) -> tuple[list[str], list[str]]:
    """(well_formed, malformed), preserving input order."""
    good: list[str] = []
    bad: list[str] = []
    for t in tickers:
        (good if valid_format(t) else bad).append(t)
    return good, bad


def no_data(card) -> bool:
    """True iff no source returned anything for this symbol: every sub-score is
    None AND market_cap is None. A real-but-FMP-gated symbol still gets a Finnhub
    market_cap, so this cleanly separates 'unknown ticker' from 'thin/gated'.

    opportunity is omitted deliberately: it is display-only max(momentum, value)
    and is never independently non-None when both legs are None.
    """
    subs = [card.quality, card.moat, card.growth, card.momentum,
            card.value, card.insider, getattr(card, "risk", None)]
    mcap = getattr(card.metrics, "market_cap", None) if card.metrics else None
    return not any(s is not None for s in subs) and mcap is None
