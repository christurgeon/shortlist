"""Phase-1 signal-validation evaluator: measure a discovery signal's forward-return quality
and emit a KILL / HOLD / INSUFFICIENT verdict + an information-ratio rank.

Decision statistic = a calendar-time-portfolio Fama-French-3-factor alpha with a block-
bootstrap CI (block >= holding horizon). Survivorship is ACCOUNTED (measurable-fraction gate),
not fixed — there is no free point-in-time ticker map. NEVER emits a PROMOTE (spec §6.4).
Proposal-only: never writes config.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..backtest.prices import PriceHistory, _add_months


@dataclass
class MeasuredEvent:
    signal: str
    ticker: str
    event_date: date
    ret: float | None          # fixed-horizon forward return; None if non-measurable
    measurable: bool
    strength: float | None
    gated: bool | None
    composite: float | None


@dataclass
class CohortMeasurement:
    signal: str
    n_selected: int
    n_measurable: int
    events: list[MeasuredEvent] = field(default_factory=list)

    def measurable_fraction(self) -> float:
        return (self.n_measurable / self.n_selected) if self.n_selected else 0.0

    def measurable_fraction_by_vintage(self) -> dict[int, tuple[int, int, float]]:
        """Measurable fraction stratified by vintage (calendar YEAR of event_date).

        Returns {year: (n_measurable, n_selected, fraction)}. An event with no event_date
        is excluded from every bucket (it can't be assigned a vintage) — it still counts
        in the pooled `measurable_fraction()` via n_selected/n_measurable on the cohort.
        """
        by_year: dict[int, list[MeasuredEvent]] = {}
        for ev in self.events:
            if ev.event_date is None:
                continue
            by_year.setdefault(ev.event_date.year, []).append(ev)
        out: dict[int, tuple[int, int, float]] = {}
        for year, evs in by_year.items():
            n_sel = len(evs)
            n_meas = sum(1 for e in evs if e.measurable)
            frac = (n_meas / n_sel) if n_sel else 0.0
            out[year] = (n_meas, n_sel, frac)
        return out


def _event_date(ev: dict) -> date | None:
    v = ev.get("event_date")
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def measure_cohort(events: list[dict], signal: str, horizon_months: int,
                   hist_by_ticker: dict[str, PriceHistory],
                   delisting_return: float | None,
                   as_of: date | None = None) -> CohortMeasurement:
    """Measure the fixed-horizon forward return of every event for `signal`.

    - Return measured at `event_date + horizon_months` via PriceHistory.forward_return
      (split-safe adjusted closes, None-safe).
    - When no data exists at the horizon target, a **calendar-time** rule (never a price
      heuristic) separates the two cases the spec (§6.1/H2) requires kept apart:
        * target > `as_of` (default today)  -> IMMATURE: not enough time has elapsed, the
          outcome is simply unknown -> non-measurable (never dropped, counted).
        * target <= `as_of` but the series ends before it -> DELISTING: a still-listed
          stock would have had data through the target, so an early terminus in the past
          means it stopped trading -> apply `delisting_return` (None -> non-measurable).
    - No usable series at all -> non-measurable.
    """
    ref = as_of or date.today()
    measured: list[MeasuredEvent] = []
    for ev in events:
        if ev.get("signal") != signal:
            continue
        d = _event_date(ev)
        tk = (ev.get("ticker") or "").upper()
        hist = hist_by_ticker.get(tk)
        ret: float | None = None
        if d is not None and hist is not None:
            ret = hist.forward_return(d, horizon_months)
            if ret is None and delisting_return is not None and _series_terminated(hist, d, horizon_months, ref):
                ret = delisting_return
        measured.append(MeasuredEvent(
            signal=signal, ticker=tk, event_date=d, ret=ret,
            measurable=ret is not None,
            strength=ev.get("strength"), gated=ev.get("gated"), composite=ev.get("composite"),
        ))
    n_meas = sum(1 for m in measured if m.measurable)
    return CohortMeasurement(signal=signal, n_selected=len(measured),
                             n_measurable=n_meas, events=measured)


def _month_iso(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_between(a: date, b: date) -> int:
    """Whole calendar months from a to b (b >= a)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def calendar_time_portfolio(measured: list[MeasuredEvent], k_months: int,
                            weighting: str = "equal") -> list[tuple[str, float, int]]:
    """Monthly calendar-time portfolio: each month, hold every name whose event fired in the
    trailing k_months; the month's return is the (equal- or value-) weighted mean of held
    names' monthly-equivalent returns. Collapsing contemporaneous events into one monthly
    return is what neutralises cross-sectional event clustering (spec §6.3).

    K-vs-cycle dedup: if the same ticker has more than one qualifying event inside a single
    month's trailing-K window, it is counted ONCE that month (the most-recent qualifying
    event) -- otherwise the independent-block accounting double-weights a repeat firer.
    """
    live = [m for m in measured if m.measurable and m.event_date is not None and m.ret is not None]
    if not live:
        return []
    lo = min(m.event_date for m in live)
    hi = max(m.event_date for m in live)
    n_months = _months_between(lo, hi) + k_months          # cover the last cohort's holding tail
    rows: list[tuple[str, float, int]] = []
    for i in range(n_months):
        y = lo.year + (lo.month - 1 + i) // 12
        mo = (lo.month - 1 + i) % 12 + 1
        month_start = date(y, mo, 1)
        qualifying = [m for m in live
                      if 0 <= _months_between(m.event_date, month_start) < k_months]
        if not qualifying:
            continue
        # dedup by ticker: keep one contribution per ticker (the most-recent qualifying event)
        by_ticker: dict[str, MeasuredEvent] = {}
        for m in qualifying:
            cur = by_ticker.get(m.ticker)
            if cur is None or m.event_date > cur.event_date:
                by_ticker[m.ticker] = m
        held = list(by_ticker.values())
        contribs = [(1.0 + m.ret) ** (1.0 / k_months) - 1.0 for m in held]
        if weighting == "value":
            ws = [float(m.composite or 0.0) for m in held]     # placeholder weight source
            tot = sum(ws)
            r = sum(c * w for c, w in zip(contribs, ws)) / tot if tot > 0 else sum(contribs) / len(contribs)
        else:
            r = sum(contribs) / len(contribs)
        rows.append((_month_iso(month_start), r, len(held)))
    return rows


def _series_terminated(hist: PriceHistory, entry: date, horizon_months: int,
                       as_of: date) -> bool:
    """True when the series has an entry price and the horizon target is in the PAST
    (target <= as_of) yet the series ends before it — a delisting, since a still-listed
    stock would have traded through the target. An immature event (target still in the
    future) returns False: the outcome is unknown, not a delisting."""
    if hist.close_asof(entry) is None:
        return False                      # no entry price -> not a delisting, just absent
    if not hist.dates:
        return False
    target = _add_months(entry, horizon_months)
    if target > as_of:
        return False                      # horizon not yet elapsed -> immature, not delisted
    return hist.dates[-1] < target        # past target with an early terminus -> delisting
