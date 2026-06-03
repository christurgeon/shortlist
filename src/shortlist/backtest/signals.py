"""Signal sources for the backtest. Every source emits Observations whose values
are 0-100 sub-scores produced by the REAL scoring functions, so IC is comparable
across heterogeneous sources and a future XBRL source slots in unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

from ..data.sources import snapshot_from_closes
from ..data.bridge import snapshot_to_metrics
from ..data.store import load
from ..data.models import TickerSnapshot
from .. import scoring
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
