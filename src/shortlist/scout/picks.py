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
    cik: str | None         # subject CIK (from the emission) — re-resolved at scoreboard time
    session: str            # ISO date the pick was surfaced
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


def pick_from_card(card, candidate, session: date) -> Pick:
    """Build a Pick from a scored ScoreCard + the discovery Candidate that surfaced it.
    Catalyst/evidence/CIK prefer the activist-13D emission, else the first emission.

    Note: the selection session (not the filing date) anchors the pick — the after-close
    walk-back means the originating filing is ≤4 trading days older; that small gap is an
    accepted, documented approximation rather than a threaded filing-date field."""
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
        cik=getattr(em, "cik", None) if em else None,
        session=session.isoformat(),
        catalyst=em.signal if em else "",
        evidence=em.evidence if em else "",
        composite=getattr(card, "composite", None),
        confidence=getattr(card, "confidence", None),
        sic_bucket=getattr(card, "sic_bucket", None),
        as_of_price=getattr(m, "price", None) if m is not None else None,
        market_cap=getattr(m, "market_cap", None) if m is not None else None,
        gated=bool(getattr(card, "gates", [])),
    )


# --- scoreboard: split-safe, benchmark-relative performance since selection ---

def bucket_label(days: int) -> str:
    """Fixed horizon bucket for a held position (so a 2-day-old and a 120-day-old pick
    are comparable, not lumped into one undifferentiated 'since selection' number)."""
    for d, lbl in ((31, "1m"), (93, "3m"), (186, "6m"), (372, "12m")):
        if days <= d:
            return lbl
    return ">12m"


def _close_on_or_after(series, d):
    """First close on/after date `d` from an ascending (date, adjclose) series."""
    for dt, c in series:
        if dt >= d and c:
            return c
    return None


def pick_performance(pick: dict, stock_series, spy_series) -> dict:
    """Return-since-selection for one pick, split-safe and benchmark-relative.

    Both endpoints come from ONE fresh adjusted series (so a split between selection and
    now can't inject a spurious ±50% — never divide a fresh adjclose by the stored scalar).
    Excess = stock return − SPY return over the same window. All fields None-safe.
    stock_series / spy_series: ascending list[(date, adjclose)].
    """
    out = {"ticker": pick.get("ticker"), "days_held": None, "ret": None,
           "spy_ret": None, "excess": None, "horizon_bucket": None}
    try:
        sel = date.fromisoformat(pick["session"])
    except (KeyError, ValueError, TypeError):
        return out
    base = _close_on_or_after(stock_series, sel)        # split-consistent (one series)
    cur = stock_series[-1][1] if stock_series else None
    if not base or not cur:
        return out
    out["ret"] = cur / base - 1.0
    last_dt = stock_series[-1][0]
    out["days_held"] = (last_dt - sel).days
    out["horizon_bucket"] = bucket_label(out["days_held"])
    sbase = _close_on_or_after(spy_series, sel)
    scur = spy_series[-1][1] if spy_series else None
    if sbase and scur:
        out["spy_ret"] = scur / sbase - 1.0
        out["excess"] = out["ret"] - out["spy_ret"]
    return out
