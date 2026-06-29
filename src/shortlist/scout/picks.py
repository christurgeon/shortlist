"""Selection ledger: record each surfaced pick (with an as-of price) so we can track how
picks perform over time, and a split-safe, benchmark-relative scoreboard.

This is the empirical mechanism for measuring an event/text discovery signal's
forward-return quality — the honest path the rank-IC backtest can't reach (13D events
aren't in SEC companyfacts; see docs/AUTONOMOUS_SCOUT.md §9). Mirrors the
research.screening_call precedent of persisting an as_of_price for a retrospective hit-rate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date


@dataclass
class Pick:
    ticker: str
    cik: str | None
    session: str            # ISO date the pick was surfaced
    filing_date: str | None  # ISO date of the originating filing (when known)
    catalyst: str           # signal that surfaced it, e.g. "edgar:activist_13d"
    evidence: str           # human-readable, e.g. "Activist 13D: Elliott → XYZ"
    composite: float | None
    confidence: float | None
    sic_bucket: str | None
    as_of_price: float | None   # close at selection (display only; returns use a fresh series)
    market_cap: float | None
    gated: bool             # recorded for raw-signal measurement; excluded from the /deep block

    def to_dict(self) -> dict:
        return asdict(self)


def pick_from_card(card, candidate, session: date, filing_date: str | None = None) -> Pick:
    """Build a Pick from a scored ScoreCard + the discovery Candidate that surfaced it.
    Catalyst/evidence prefer the activist-13D emission, else the first emission."""
    em = None
    emissions = getattr(candidate, "emissions", None) or []
    for e in emissions:
        if e.signal == "edgar:activist_13d":
            em = e
            break
    if em is None and emissions:
        em = emissions[0]
    m = getattr(card, "metrics", None)
    return Pick(
        ticker=card.ticker,
        cik=None,
        session=session.isoformat(),
        filing_date=filing_date,
        catalyst=em.signal if em else "",
        evidence=em.evidence if em else "",
        composite=getattr(card, "composite", None),
        confidence=getattr(card, "confidence", None),
        sic_bucket=getattr(card, "sic_bucket", None),
        as_of_price=getattr(m, "price", None) if m is not None else None,
        market_cap=getattr(m, "market_cap", None) if m is not None else None,
        gated=bool(getattr(card, "gates", [])),
    )
