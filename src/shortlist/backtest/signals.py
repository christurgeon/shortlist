"""Signal sources for the backtest. Every source emits Observations whose values
are 0-100 sub-scores produced by the REAL scoring functions, so IC is comparable
across heterogeneous sources and a future XBRL source slots in unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

from .. import scoring
from ..data.bridge import snapshot_to_metrics
from ..data.models import TickerSnapshot
from ..data.sources import snapshot_from_closes
from ..data.store import load
from ..providers._xbrl_facts import extract_panel, panel_to_metrics
from .prices import PriceHistory


@dataclass(frozen=True)
class Observation:
    """A point-in-time signal reading.

    as_of: the first date this value was PUBLICLY KNOWABLE. For price-reconstructed
    signals that is the price date; for a future XBRL source it must be the filing
    acceptance date, NOT the fiscal-period end (or look-ahead leaks).
    """
    as_of: date
    ticker: str
    signals: dict[str, float]


class SignalSource(Protocol):
    name: str

    def observe(self, ticker: str, as_of: date) -> Optional[Observation]: ...


class MomentumSignalSource:
    """Reconstructs the production momentum sub-score at a historical date from
    price history alone — by reusing snapshot_from_closes -> snapshot_to_metrics
    -> scoring.momentum_score on closes truncated at `as_of`. No reimplementation.
    """
    name = "momentum"

    def __init__(self, histories: dict[str, PriceHistory], spy: PriceHistory,
                 thresholds: dict, min_history: int = 200):
        self.histories = {k.upper(): v for k, v in histories.items()}
        self.spy = spy
        self.thresholds = thresholds
        self.min_history = min_history

    def observe(self, ticker: str, as_of: date) -> Optional[Observation]:
        hist = self.histories.get(ticker.upper())
        if hist is None:
            return None
        closes = hist.closes_through(as_of)          # strict: dates <= as_of
        if len(closes) < self.min_history:
            return None                               # dropped, never zeroed
        spy_closes = self.spy.closes_through(as_of)
        snap = snapshot_from_closes(ticker.upper(), closes, spy_closes)
        score = scoring.momentum_score(snapshot_to_metrics(snap), self.thresholds)
        if score is None:
            return None
        return Observation(as_of, ticker.upper(), {"momentum": score})


class SnapshotSignalSource:
    """Re-scores stored point-in-time TickerSnapshots via the real scoring engine,
    emitting composite + every sub-score. Valid ONLY for organically-accumulated
    daily captures (never backfilled/restated data). Produces nothing until the
    store has history; the CLI guards activation (see fit/cli).
    """
    name = "composite"

    def __init__(self, store_root: str, config: dict):
        self.store_root = store_root
        self.config = config

    def observe(self, ticker: str, as_of: date) -> Optional[Observation]:
        try:
            raw = load(ticker, self.store_root, day=as_of.isoformat())
        except (FileNotFoundError, OSError):
            return None
        snap = TickerSnapshot.from_dict(raw)
        card = scoring.score(snapshot_to_metrics(snap), self.config)
        sig: dict[str, float] = {"composite": card.composite}
        for axis in ("quality", "moat", "growth", "value", "momentum", "insider"):
            v = getattr(card, axis, None)
            if v is not None:
                sig[axis] = v
        return Observation(as_of, ticker.upper(), sig)


class XbrlSignalSource:
    """Reconstructs the production FUNDAMENTAL sub-scores (quality/moat/growth/
    value) at a historical date from SEC companyfacts truncated point-in-time
    (filed <= as_of), reusing the real scoring functions — no reimplementation.
    Validates the fundamental axes the price-only momentum source can't reach.

    Sub-score level by design (like MomentumSignalSource): sector masking and
    weight-redistribution are scoring.score() concerns and not applied here, so
    IC stays comparable across sources. value emits 2 of 4 legs (fcf_yield,
    pe_vs_history); peg + upside_to_target need analyst data absent from XBRL.
    Also emits a standalone `piotroski` axis (Core-6 fundamental-quality, unfitted prior)
    and a standalone `share_count` axis (diluted-share-count dilution, unfitted prior) so
    their rank IC is measurable before either is trusted in production scoring.
    """
    name = "xbrl"
    _AXES = ("quality", "moat", "growth", "value", "piotroski", "share_count",
             "net_debt_to_ebitda")

    def __init__(self, facts: dict[str, dict], histories: dict[str, PriceHistory],
                 thresholds: dict):
        self.facts = {k.upper(): v for k, v in facts.items()}
        self.histories = {k.upper(): v for k, v in histories.items()}
        self.thresholds = thresholds

    def observe(self, ticker: str, as_of: date) -> Optional[Observation]:
        cf = self.facts.get(ticker.upper())
        if cf is None:
            return None
        hist = self.histories.get(ticker.upper())
        price = hist.close_asof(as_of) if hist else None
        price_at = (lambda d: hist.price_on(d)) if hist else (lambda d: None)
        panel = extract_panel(cf, as_of)
        if not panel.revenue:                 # nothing knowable yet -> drop, never zero
            return None
        m = panel_to_metrics(panel, ticker=ticker.upper(), sic=None,  # SIC not in companyfacts; sector masking is a score()-level concern
                             price=price, price_at=price_at)
        sig: dict[str, float] = {}
        for axis in self._AXES:
            v = getattr(scoring, f"{axis}_score")(m, self.thresholds)
            if v is not None:
                sig[axis] = v
        if not sig:
            return None
        return Observation(as_of, ticker.upper(), sig)
