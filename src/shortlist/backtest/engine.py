"""Backtest engine: build a non-overlapping observation grid, join forward
returns (excess over SPY by default), compute time-series + cross-sectional rank
IC and quantile spreads. No look-ahead: signals use data <= T, returns use > T.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .metrics import ICStats, QuantileResult, aggregate_ic, quantile_spread, spearman_ic
from .prices import PriceHistory, _add_months
from .signals import SignalSource

_TRUST_MIN_PERIODS = 24
_TRUST_MIN_BREADTH = 30


def observation_grid(start: date, end: date, step_months: int) -> list[date]:
    grid, cur = [], start
    while cur <= end:
        grid.append(cur)
        cur = _add_months(cur, step_months)
    return grid


def _collect_rows(src: SignalSource, universe: list[str],
                  histories: dict[str, PriceHistory], spy: PriceHistory,
                  grid: list[date], horizon: int,
                  return_mode: str) -> dict[str, list[tuple[date, str, float, float]]]:
    """Join every emitted signal to forward returns over the grid, keyed by signal
    name. A source may emit several signals per observation (e.g. the XBRL source
    emits quality/moat/growth/value); each becomes its own report."""
    rows: dict[str, list[tuple[date, str, float, float]]] = defaultdict(list)
    for t in grid:
        for tk in universe:
            obs = src.observe(tk, t)
            if obs is None or not obs.signals:
                continue
            fr = fwd_return(histories[tk], spy, t, horizon, return_mode)
            if fr is None:
                continue
            for sig_name, sv in obs.signals.items():
                rows[sig_name].append((t, tk, sv, fr))
    return rows


@dataclass
class SignalReport:
    signal: str
    horizon: int
    ts_ic: Optional[ICStats]        # time-series: per-name IC aggregated over names
    xs_ic: Optional[ICStats]        # cross-sectional: per-date IC aggregated over dates
    spread: Optional[QuantileResult]
    n_obs: int
    breadth: float                  # mean names per grid date
    notes: list[str] = field(default_factory=list)


@dataclass
class BacktestReport:
    universe: list[str]
    price_asof: Optional[date]
    horizons: list[int]
    return_mode: str
    reports: list[SignalReport]
    caveats: list[str] = field(default_factory=list)


def _pairs_ic(pairs: list[tuple[float, float]]) -> Optional[float]:
    """Spearman IC over (signal, forward-return) pairs."""
    return spearman_ic([p[0] for p in pairs], [p[1] for p in pairs])


def fwd_return(hist: PriceHistory, spy: PriceHistory, t: date, horizon: int,
               mode: str) -> Optional[float]:
    r = hist.forward_return(t, horizon)
    if r is None:
        return None
    if mode == "excess":
        rs = spy.forward_return(t, horizon)
        if rs is None:
            return None
        return r - rs
    return r


def run_backtest(sources: list[SignalSource], histories: dict[str, PriceHistory],
                 spy: PriceHistory, *, start: date, end: date, horizons: list[int],
                 step_months: Optional[int] = None, n_buckets: int = 5,
                 return_mode: str = "excess",
                 xs_min_breadth: int = _TRUST_MIN_BREADTH,
                 price_asof: Optional[date] = None) -> BacktestReport:
    """Build a non-overlapping observation grid per horizon (step = the horizon,
    so windows never overlap and the t-stat stays valid) unless step_months pins a
    fixed grid. Signals use data <= T; forward returns use only data > T."""
    reports: list[SignalReport] = []
    universe = sorted(histories.keys())
    for src in sources:
        for h in horizons:
            grid = observation_grid(start, end, step_months or h)
            rows_by_signal = _collect_rows(src, universe, histories, spy, grid, h, return_mode)

            for signal in sorted(rows_by_signal):
                rows = rows_by_signal[signal]

                by_date: dict[date, list[tuple[float, float]]] = defaultdict(list)
                by_name: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for t, tk, sv, fr in rows:
                    by_date[t].append((sv, fr))
                    by_name[tk].append((sv, fr))

                xs_ics, breadths = [], []
                for pairs in by_date.values():
                    breadths.append(len(pairs))
                    if len(pairs) >= xs_min_breadth:
                        ic = _pairs_ic(pairs)
                        if ic is not None:
                            xs_ics.append(ic)
                ts_ics = []
                for pairs in by_name.values():
                    ic = _pairs_ic(pairs)
                    if ic is not None:
                        ts_ics.append(ic)

                spread = quantile_spread([(sv, fr) for _, _, sv, fr in rows], n_buckets)
                avg_breadth = (sum(breadths) / len(breadths)) if breadths else 0.0
                notes = []
                if not xs_ics:
                    notes.append(f"cross-sectional IC suppressed: no grid date reached "
                                 f"{xs_min_breadth} names (max breadth "
                                 f"{max(breadths, default=0)})")
                if len(by_date) < _TRUST_MIN_PERIODS or avg_breadth < _TRUST_MIN_BREADTH:
                    notes.append(f"EXPLORATORY: below trust floor (>= ~{_TRUST_MIN_BREADTH} "
                                 f"names/date and >= ~{_TRUST_MIN_PERIODS} periods); "
                                 f"treat IC as directional, not significant")
                reports.append(SignalReport(
                    signal=signal, horizon=h,
                    ts_ic=aggregate_ic(ts_ics), xs_ic=aggregate_ic(xs_ics),
                    spread=spread, n_obs=len(rows), breadth=avg_breadth, notes=notes))

    caveats = [
        "Universe is currently-listed names (survivorship bias): realized spreads "
        "are an UPPER BOUND. Read this as relative signal validation, not tradeable PnL.",
        "Gross signal IC — not net of transaction costs.",
        "Forward returns are excess (over SPY)." if return_mode == "excess"
        else "Forward returns are raw (absolute).",
        "Adjusted close is total-return adjusted (splits + dividends).",
        "Time-series IC t-stat assumes per-name independence (names share factor "
        "exposure over common windows) — it is anti-conservative; weight the mean "
        "IC and hit-rate over the TS t-stat.",
        "Quantile spread pools all (date, name) rows into one sort, not a "
        "time-averaged cross-sectional spread.",
    ]
    return BacktestReport(universe=universe, price_asof=price_asof, horizons=horizons,
                          return_mode=return_mode, reports=reports, caveats=caveats)
