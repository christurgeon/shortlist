"""Adapter: turn point-in-time Observations into the (period_id, {axis: subscore},
fwd_return) rows that backtest.fit.fit_weights consumes.

Reuses the engine's NON-OVERLAPPING observation grid and forward-return helper — the
non-overlap discipline (grid step = horizon) is what keeps the fitter's walk-forward
OOS IC honest (overlapping windows leak across train/test folds). Only rows where EVERY
requested axis co-emits are kept, so every composite the fitter scores is an
apples-to-apples blend of the same axes (a partial-axis row would collapse to a
single-axis composite and corrupt the cross-sectional IC). Pure and deterministic.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .engine import fwd_return, observation_grid
from .fit import FitRow
from .prices import PriceHistory
from .signals import SignalSource


def build_fit_rows(src: SignalSource, universe: list[str],
                   histories: dict[str, PriceHistory], spy: PriceHistory, *,
                   start: date, end: date, horizon: int, axes: list[str],
                   return_mode: str = "excess",
                   step_months: Optional[int] = None) -> list[FitRow]:
    grid = observation_grid(start, end, step_months or horizon)
    rows: list[FitRow] = []
    # Ticker-major (NOT date-major), matching engine.py:_collect_rows: a lazy XBRL
    # source's small per-ticker LRU (XbrlSignalSource) loads each ticker's companyfacts
    # once across all grid dates this way. Date-major would thrash that cache -- every
    # ticker beyond the LRU size gets re-read from disk on every grid date. Aggregation
    # below is order-independent (fit.py groups by period_id), so this is a pure
    # perf/cache-locality change.
    for tk in universe:
        hist = histories.get(tk)
        if hist is None:
            continue
        for t in grid:
            obs = src.observe(tk, t)
            if obs is None or not obs.signals:
                continue
            if any(a not in obs.signals for a in axes):
                continue                              # co-emission filter
            fr = fwd_return(hist, spy, t, horizon, return_mode)
            if fr is None:
                continue                              # no forward return -> drop, never impute
            rows.append((t, {a: obs.signals[a] for a in axes}, fr))
    return rows
