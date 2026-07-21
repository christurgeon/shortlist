"""Signal sources for the backtest. Every source emits Observations whose values
are 0-100 sub-scores produced by the REAL scoring functions, so IC is comparable
across heterogeneous sources and a future XBRL source slots in unchanged.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

from .. import scoring
from ..data.bridge import snapshot_to_metrics
from ..data.models import TickerSnapshot
from ..data.sources import snapshot_from_closes_dated
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


def _score_axes(m, thresholds: dict, axes: tuple[str, ...]) -> dict[str, float]:
    """{axis: score} for every `axis` in `axes` whose `scoring.<axis>_score(m,
    thresholds)` is not None (dropped, never zeroed). Shared by
    MomentumSignalSource and XbrlSignalSource, which each score a tuple of axis
    names via the same `getattr(scoring, f"{axis}_score")` convention."""
    sig: dict[str, float] = {}
    for axis in axes:
        v = getattr(scoring, f"{axis}_score")(m, thresholds)
        if v is not None:
            sig[axis] = v
    return sig


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
        dates, closes = hist.through(as_of)          # strict: dates <= as_of, PAIRED
        if len(closes) < self.min_history:
            return None                               # dropped, never zeroed
        spy_dates, spy_closes = self.spy.through(as_of)
        # Dated seam: residual_momentum needs the date-aligned stock+SPY series, which it
        # INNER-JOINS internally (§2). The production `momentum` sub-score is byte-identical
        # to the scalar seam (the dated path only ADDS m.residual_momentum).
        snap = snapshot_from_closes_dated(ticker.upper(), dates, closes, spy_dates, spy_closes)
        m = snapshot_to_metrics(snap)
        score = scoring.momentum_score(m, self.thresholds)
        if score is None:
            return None
        sig: dict[str, float] = {"momentum": score}
        # Standalone residual-momentum axis (Blitz-Huij-Martens 2011, §2). UNLIKE SUE this
        # IS price-reconstructable, so it rides the LIVE-price path. Emitting it lets the
        # `residual_momentum~momentum` collinearity pair (and head-to-head rank IC) be
        # measured — the point is to show it dominates raw momentum. None-safe (a flat/thin
        # window or absent band drops it).
        rm = scoring.residual_momentum_score(m, self.thresholds)
        if rm is not None:
            sig["residual_momentum"] = rm
        # PREDICTIVE_SIGNALS §2 price-refinement MEASUREMENT axes (George-Hwang 52wk-high,
        # Bali MAX-effect, Barroso-Santa-Clara vol-scaled momentum) + the two LEG-reference
        # axes (price_vs_200dma / rel_strength_6m) so the leg-level collinearity can be
        # measured. Backtest-only — NO production leg reads them (the momentum sub-score above
        # is byte-identical). Emitted None-safe.
        sig.update(_score_axes(m, self.thresholds,
                                ("pct_to_52w_high", "max_daily_return", "vol_scaled_momentum",
                                 "price_vs_200dma", "rel_strength_6m")))
        return Observation(as_of, ticker.upper(), sig)


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
        m = snapshot_to_metrics(snap)
        card = scoring.score(m, self.config)
        # Composite only for cards production would rank with confidence: `scored`
        # alone is toothless here (always True for the unknown bucket, and thin
        # accumulated snapshots usually lack a SIC), so pair it with the validity
        # floor. Sub-axes stay per-axis None-safe below. Residual (documented in
        # the spec): a Finnhub-only name can read confidence ~0.32 and still pass
        # a 0.25 floor — a measurement-policy line to revisit WITH data, not here.
        sig: dict[str, float] = {}
        floor = scoring._validity(self.config or {})["min_scored_weight"]
        if card.scored and (card.confidence or 0.0) >= floor:
            sig["composite"] = card.composite
        for axis in ("quality", "moat", "growth", "value", "momentum", "insider"):
            v = getattr(card, axis, None)
            if v is not None:
                sig[axis] = v
        # Standalone SUE axis (PREDICTIVE_SIGNALS §1). SUE cannot be reconstructed on the
        # live-price MomentumSignalSource (price-only snapshots carry NO earnings) nor on
        # the XBRL path (surprises aren't in companyfacts), so it rides ONLY this guarded
        # snapshot-replay path — and only once accumulation captures the earnings fields.
        # Emitting it here lets the `sue~momentum` collinearity pair be measured (no-op
        # until accumulated snapshots carry the SUE inputs; the score helper is None-safe).
        sue = scoring.sue_score(m, (self.config or {}).get("thresholds") or {})
        if sue is not None:
            sig["sue"] = sue
        if not sig:
            return None
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
    Also emits a standalone `piotroski` axis (Core-6 fundamental-quality, unfitted prior),
    a standalone `share_count` axis (diluted-share-count dilution, unfitted prior), and a
    standalone `net_debt_to_ebitda` axis (net-debt/EBITDA leverage, unfitted prior) so
    their rank IC is measurable before any is trusted in production scoring.

    Also emits the absolute-valuation axes `ebit_ev_yield` (EBIT/EV earnings yield,
    unfitted prior), the per-leg value-attribution axes `value_fcf_yield` /
    `value_pe_vs_history`, and `value_plus_evebit` (the value average WITH the
    EV/EBIT leg) so the leg's additive-or-dilutive effect on the combined `value`
    IC is measurable before any production use (spec §11).

    Also emits the investment & earnings-quality axes `asset_growth` (Cooper-Gulen-
    Schill 2008) and `accruals` (Sloan 1996), both unfitted priors and both NEGATIVE
    predictors the score helpers invert, so their rank IC + collinearity (`accruals~
    piotroski`, `asset_growth~growth`) are measurable before the opt-in quality legs
    are trusted (PREDICTIVE_SIGNALS §3). And `shareholder_yield` (Boudoukh et al. 2007 /
    Faber, PREDICTIVE_SIGNALS §5) — a POSITIVE predictor scored straight, with the
    `shareholder_yield~fcf_yield` and `shareholder_yield~share_count` collinearity pairs
    (the buyback leg is the dollar-twin of dilution) measured before the opt-in value leg.
    """
    name = "xbrl"
    _AXES = ("quality", "moat", "growth", "value", "piotroski", "share_count",
             "net_debt_to_ebitda", "ebit_ev_yield", "value_fcf_yield",
             "value_pe_vs_history", "value_plus_evebit", "asset_growth", "accruals",
             "shareholder_yield")

    def __init__(self, facts: Optional[dict[str, dict]] = None,
                 histories: Optional[dict[str, PriceHistory]] = None,
                 thresholds: Optional[dict] = None, *,
                 fact_loader=None, lru_size: int = 4):
        """Two construction modes:
        - EAGER (`facts` dict): all companyfacts held in memory (small universes / tests).
        - LAZY (`fact_loader`, a `TICKER -> Optional[dict]` callable): facts are loaded one
          at a time from the disk cache and held in a small LRU, so peak RAM is bounded —
          the full-universe path on memory-constrained boxes. Pair with the engine's
          ticker-major iteration so each ticker loads once. Identical Observations either way."""
        self.facts = {k.upper(): v for k, v in facts.items()} if facts is not None else None
        self.histories = {k.upper(): v for k, v in (histories or {}).items()}
        self.thresholds = thresholds or {}
        self._loader = fact_loader
        self._lru: "OrderedDict[str, Optional[dict]]" = OrderedDict()
        self._lru_size = max(1, lru_size)

    def _get_facts(self, tk: str) -> Optional[dict]:
        """companyfacts for an UPPER ticker — from the eager dict, or lazily from the
        loader with an LRU (negatives cached too, so a missing ticker isn't reloaded)."""
        if self.facts is not None:
            return self.facts.get(tk)
        if tk in self._lru:
            self._lru.move_to_end(tk)
            return self._lru[tk]
        cf = self._loader(tk) if self._loader else None
        self._lru[tk] = cf                     # inserted last (most-recent) by construction
        while len(self._lru) > self._lru_size:
            self._lru.popitem(last=False)
        return cf

    def observe(self, ticker: str, as_of: date) -> Optional[Observation]:
        cf = self._get_facts(ticker.upper())
        if cf is None:
            return None
        hist = self.histories.get(ticker.upper())
        price = hist.nominal_close_asof(as_of) if hist else None
        price_at = (lambda d: hist.nominal_price_on(d)) if hist else (lambda d: None)
        panel = extract_panel(cf, as_of)
        if not panel.revenue:                 # nothing knowable yet -> drop, never zero
            return None
        m = panel_to_metrics(panel, ticker=ticker.upper(), sic=None,  # SIC not in companyfacts; sector masking is a score()-level concern
                             price=price, price_at=price_at)
        sig = _score_axes(m, self.thresholds, self._AXES)
        if not sig:
            return None
        return Observation(as_of, ticker.upper(), sig)
