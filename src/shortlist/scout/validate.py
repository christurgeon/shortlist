"""Phase-1 signal-validation evaluator: measure a discovery signal's forward-return quality
and emit a KILL / HOLD / INSUFFICIENT verdict + an information-ratio rank.

Decision statistic = a calendar-time-portfolio Fama-French-3-factor alpha with a block-
bootstrap CI (block >= holding horizon). Survivorship is ACCOUNTED (measurable-fraction gate),
not fixed — there is no free point-in-time ticker map. NEVER emits a PROMOTE (spec §6.4).
Proposal-only: never writes config.yaml.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date
from statistics import NormalDist

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
    immature: bool = False     # LAST field, defaulted (positional back-compat, design I3):
                                # target not yet elapsed AND a real price series/entry price
                                # exists AND the series is not already known-terminated (B1).
                                # A recent event with NO series / no entry price is NEVER
                                # immature -- it is non-measurable-and-counted (survivorship).
    # Appended AFTER `immature` to preserve that field's positional slot. Actual per-
    # holding-month returns (month i spans event_date+i .. event_date+i+1), so the
    # calendar-time portfolio can use what each name really DID each month rather than an
    # assumed smooth path. None/empty -> `calendar_time_portfolio` falls back to the old
    # constant `(1+ret)**(1/K)-1` rate (old persisted cohorts, hand-built test events).
    # See docs/audits/2026-07-26-funnel-composition-audit.md §4.
    monthly_rets: list[float] | None = None


@dataclass
class CohortMeasurement:
    signal: str
    n_selected: int
    n_measurable: int
    events: list[MeasuredEvent] = field(default_factory=list)
    # New fields appended at the END (positional back-compat, design I3) --
    # `measurable_fraction()` keeps dividing these STORED ints as given (hand-built
    # CohortMeasurements in tests are trusted, not re-derived from `events`).
    n_immature: int = 0        # events excluded from n_selected/measurable_fraction (H2)
    n_events: int = 0          # RAW count incl. immature -- full transparency

    def measurable_fraction(self) -> float:
        return (self.n_measurable / self.n_selected) if self.n_selected else 0.0

    def measurable_fraction_by_vintage(self) -> dict[int, tuple[int, int, float]]:
        """Measurable fraction stratified by vintage (calendar YEAR of event_date),
        MATURE-ONLY (H2 fix): immature events are excluded from both the bucket's n_sel
        and n_meas -- a vintage that is ENTIRELY immature contributes no bucket at all
        (the N2 knife-edge: a bucket is skipped iff it has zero MATURE events, not zero
        measurable ones -- an all-matured-but-all-lost vintage still emits a 0.0 bucket,
        which correctly trips the vintage floor).

        Returns {year: (n_measurable, n_selected, fraction)}. An event with no event_date
        is excluded from every bucket (it can't be assigned a vintage) — it still counts
        in the pooled `measurable_fraction()` via n_selected/n_measurable on the cohort.
        """
        by_year: dict[int, list[MeasuredEvent]] = {}
        for ev in self.events:
            if ev.event_date is None:
                continue
            if ev.immature:
                continue
            by_year.setdefault(ev.event_date.year, []).append(ev)
        out: dict[int, tuple[int, int, float]] = {}
        for year, evs in by_year.items():
            n_sel = len(evs)                      # always > 0 (only populated years appear)
            n_meas = sum(1 for e in evs if e.measurable)
            frac = n_meas / n_sel
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


def _monthly_path(hist: PriceHistory | None, d: date | None,
                  horizon_months: int) -> list[float] | None:
    """Per-holding-month returns for one event: month i spans `d+i` .. `d+i+1` months.

    The calendar-time portfolio rebalances monthly, so it needs what a name actually did
    each month — inferring a smooth `(1+ret)**(1/K)-1` path spreads a one-month collapse
    across the whole window and applies the rebalancing drag to it K times
    (docs/audits/2026-07-26-funnel-composition-audit.md §4).

    Returns None if any leg is unavailable, so the caller falls back to the old constant
    rate rather than a partly-imputed path — a half-real path would be worse than either.
    """
    if hist is None or d is None or horizon_months <= 0:
        return None
    out: list[float] = []
    for i in range(horizon_months):
        a = hist.price_on(_add_months(d, i), tol_days=5) if i else hist.close_asof(d)
        b = hist.price_on(_add_months(d, i + 1), tol_days=5)
        if a is None or b is None or a <= 0:
            return None
        out.append(b / a - 1.0)
    return out


def measure_cohort(events: list[dict], signal: str, horizon_months: int,
                   hist_by_ticker: dict[str, PriceHistory],
                   delisting_return: float | None,
                   as_of: date | None = None,
                   use_event_delisting: bool = True) -> CohortMeasurement:
    """Measure the fixed-horizon forward return of every event for `signal`.

    - Return measured at `event_date + horizon_months` via PriceHistory.forward_return
      (split-safe adjusted closes, None-safe).
    - When no data exists at the horizon target, a **calendar-time** rule (never a price
      heuristic) separates the two cases the spec (§6.1/H2) requires kept apart:
        * target > `as_of` (default today)  -> IMMATURE: not enough time has elapsed, the
          outcome is simply unknown -> flagged `immature` and EXCLUDED from `n_selected`/
          `measurable_fraction()` (H2 fix; design 2026-07-06) -- never dropped from
          `events`, just out of the denominator. **B1 leak-proof guard:** an event is only
          ever `immature` when a real price series AND a usable entry price exist -- a
          recent event with NO series / no entry price is NON-MEASURABLE AND COUNTED (the
          survivorship case below), never relabeled immature just because it's recent.
        * target <= `as_of` but the series ends before it -> DELISTING: a still-listed
          stock would have had data through the target, so an early terminus in the past
          means it stopped trading. `use_event_delisting` (default True) FIRST prefers the
          per-event CLASSIFIED terminal return the 13D backfill computes
          (`ev["meta"]["delisting_event_return"]`, spec §6.6) over the blanket
          `delisting_return`; the blanket value is the fallback when no classified value is
          present. Passing `use_event_delisting=False` ignores the classified value
          entirely (the sensitivity band in daily.py:_delisting_band_flip relies on this so
          the band's fixed variants actually vary the classified events too — "the
          classifier shrinks the band's bite but does not remove the guard"). Both -> None
          means non-measurable (and, since target <= as_of here, NOT immature -- counted).
    - No usable series at all -> non-measurable, counted, never immature.
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
        immature = False
        if d is not None and hist is not None:
            ret = hist.forward_return(d, horizon_months)
            if ret is None and _series_terminated(hist, d, horizon_months, ref):
                per_ev = None
                if use_event_delisting:
                    md = ev.get("meta") or {}
                    per_ev = md.get("delisting_event_return")
                if per_ev is not None:
                    ret = per_ev
                elif delisting_return is not None:
                    ret = delisting_return
            if ret is None:
                # B1: immature only when a usable entry price exists AND the horizon
                # hasn't elapsed AND the series isn't already known-terminated (the last
                # clause is the belt-and-suspenders form of the same calendar guard
                # _series_terminated already applies -- it returns False whenever the
                # target is still in the future, so this never mislabels a genuinely
                # terminated-but-not-yet-mature series as immature).
                immature = (
                    hist.close_asof(d) is not None
                    and _add_months(d, horizon_months) > ref
                    and not _series_terminated(hist, d, horizon_months, ref)
                )
        measured.append(MeasuredEvent(
            signal=signal, ticker=tk, event_date=d, ret=ret,
            measurable=ret is not None,
            strength=ev.get("strength"), gated=ev.get("gated"), composite=ev.get("composite"),
            immature=immature,
            monthly_rets=_monthly_path(hist, d, horizon_months) if ret is not None else None,
        ))
    n_immature = sum(1 for m in measured if m.immature)
    n_meas = sum(1 for m in measured if m.measurable)
    return CohortMeasurement(signal=signal, n_selected=len(measured) - n_immature,
                             n_measurable=n_meas, events=measured,
                             n_immature=n_immature, n_events=len(measured))


def _month_iso(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_between(a: date, b: date) -> int:
    """Whole calendar months from a to b (b >= a)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def calendar_time_portfolio(measured: list[MeasuredEvent], k_months: int,
                            weighting: str = "equal",
                            month_span: tuple[date, date] | None = None,
                            ) -> list[tuple[str, float, int]]:
    """Monthly calendar-time portfolio: each month, hold every name whose event fired in the
    trailing k_months; the month's return is the (equal- or value-) weighted mean of held
    names' monthly-equivalent returns. Collapsing contemporaneous events into one monthly
    return is what neutralises cross-sectional event clustering (spec §6.3).

    K-vs-cycle dedup: if the same ticker has more than one qualifying event inside a single
    month's trailing-K window, it is counted ONCE that month (the most-recent qualifying
    event) -- otherwise the independent-block accounting double-weights a repeat firer.

    `month_span` pins the calendar grid to an explicit (first, last) event date instead of
    deriving it from `measured`. Bootstrap replicates MUST pass the original cohort's span:
    a resample loses the earliest event ~37% of the time ((1-1/n)^n -> 1/e), and the derived
    window can only ever CONTRACT, so re-deriving it per replicate would make the FF3 fit run
    on a randomly shortened sample and understate the interval. Default None reproduces the
    pre-existing derive-from-events behaviour exactly.
    """
    live = [m for m in measured if m.measurable and m.event_date is not None and m.ret is not None]
    if not live:
        return []
    if month_span is not None:
        lo, hi = month_span
    else:
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
        # A calendar-time portfolio is equal-weighted and REBALANCED MONTHLY, so each
        # month's return must be the mean of what the held names ACTUALLY did that month.
        # The old code gave every name a constant `(1+ret)**(1/K)-1` rate for the whole
        # window, which spreads a one-month collapse evenly across K months and then applies
        # the rebalancing drag to it K times over. Fall back to that constant only when an
        # event carries no path (audit 2026-07-26 §4).
        contribs = []
        for m in held:
            i = _months_between(m.event_date, month_start)
            path = m.monthly_rets
            if path and 0 <= i < len(path) and path[i] is not None:
                contribs.append(path[i])
            else:
                contribs.append((1.0 + m.ret) ** (1.0 / k_months) - 1.0)
        if weighting == "value":
            ws = [float(m.composite or 0.0) for m in held]     # placeholder weight source
            tot = sum(ws)
            r = sum(c * w for c, w in zip(contribs, ws, strict=False)) / tot if tot > 0 else sum(contribs) / len(contribs)
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


def ols(y: list[float], X: list[list[float]]) -> list[float]:
    """Ordinary least squares via normal equations (X'X b = X'y) solved by Gaussian
    elimination. An intercept column of 1s is prepended internally. Stdlib only.
    Returns [intercept, *coeffs]. Raises ValueError on a singular system.

    NO regularization: the pivot check (abs(pivot) < 1e-12 -> raise) is load-bearing for
    the abstention contract. A materially-collinear / near-singular design must RAISE so
    the caller (ff3_alpha / information_ratio) catches it and returns None, rather than a
    ridge silently returning a garbage alpha for an ill-conditioned regression."""
    n = len(y)
    if n == 0 or n != len(X):
        raise ValueError("ols: empty or mismatched input")
    k = len(X[0]) + 1
    A = [[1.0] + list(row) for row in X]                 # design matrix with intercept
    # Normal equations
    XtX = [[sum(A[r][i] * A[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    Xty = [sum(A[r][i] * y[r] for r in range(n)) for i in range(k)]
    # Gaussian elimination with partial pivoting
    M = [XtX[i] + [Xty[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("ols: singular normal-equations matrix")
        M[col], M[piv] = M[piv], M[col]
        pivval = M[col][col]
        M[col] = [v / pivval for v in M[col]]
        for r in range(k):
            if r != col and abs(M[r][col]) > 0:
                factor = M[r][col]
                M[r] = [a - factor * b for a, b in zip(M[r], M[col], strict=False)]
    return [M[i][k] for i in range(k)]


def _aligned(ctp_rows, ff3):
    """Join CTP months to FF3 months -> (excess_returns, factor_rows) aligned."""
    y, X = [], []
    for mo, r, _n in ctp_rows:
        f = ff3.get(mo)
        if f is None:
            continue
        mkt, smb, hml, rf = f
        y.append(r - rf)
        X.append([mkt, smb, hml])
    return y, X


def ff3_alpha(ctp_rows, ff3, min_obs: int = 6):
    y, X = _aligned(ctp_rows, ff3)
    if len(y) < min_obs:
        return (None, [])
    try:
        b = ols(y, X)
    except ValueError:
        return (None, [])
    return (b[0], b[1:])


def effective_blocks(n_months: int, k_months: int) -> int:
    """Independent blocks ~= T/K (spec §6.3/F2). NOT raw months — K-month holdings make
    adjacent monthly CTP returns autocorrelated out to lag K."""
    if k_months <= 0:
        return 0
    return n_months // k_months


def _residuals(y, X, b):
    out = []
    for i in range(len(y)):
        pred = b[0] + sum(b[1 + j] * X[i][j] for j in range(len(X[i])))
        out.append(y[i] - pred)
    return out


def information_ratio(ctp_rows, ff3, min_obs: int = 6):
    """Annualised FF3 alpha / annualised tracking error (residual std). One horizon-agnostic,
    risk-normalised number so signals at different K are comparable (spec §6.3/F4)."""
    y, X = _aligned(ctp_rows, ff3)
    if len(y) < min_obs:
        return None
    try:
        b = ols(y, X)
    except ValueError:
        return None
    resid = _residuals(y, X, b)
    n = len(resid)
    k = len(X[0]) + 1                                     # intercept + factors (regressors)
    if n <= k:
        return None                                      # no residual d.o.f.
    mean = sum(resid) / n
    var = sum((e - mean) ** 2 for e in resid) / (n - k)  # regression d.o.f. (n-k, not n-1)
    te = math.sqrt(var)
    if te <= 0:
        return None
    return (b[0] * 12.0) / (te * math.sqrt(12.0))


def double_sort(measured: list[MeasuredEvent], k_months: int, ff3: dict, *,
                min_bucket_events: int, min_independent_blocks: int,
                weighting: str = "equal", n_boot: int = 2000,
                seed: int = 12345,
                min_measurable_frac: float | None = None) -> dict | None:
    """High-vs-low composite double-sort (design B1 + v2 resolution 2): does the scorer's
    composite sort winners INSIDE a cohort? Gate-agnostic (tests the composite's ORDERING
    power, not `gated`) — split events with a non-None composite at the median (ties -> the
    high side), build a calendar-time portfolio per side, and measure the FF3 alpha of the
    high-minus-low spread.

    Returns None when:
      - no events have both a non-None composite and a measurable return,
      - either side has fewer than `min_bucket_events` events after the median split, or
      - the spread's INDEPENDENT-BLOCK count (`effective_blocks`, not raw months — K-month
        holdings autocorrelate adjacent monthly returns) is below `min_independent_blocks`
        (the load-bearing block gate: a thin, autocorrelated spread must never be misread as
        evidence).

    Never raises on well-typed input; pure and deterministic (the bootstrap CI is seeded).
    """
    eligible = [m for m in measured
                if m.composite is not None and m.measurable and m.ret is not None]
    if not eligible:
        return None

    high, low, median = median_split(eligible)                # ties -> high side
    if len(high) < min_bucket_events or len(low) < min_bucket_events:
        return None

    ctp_high = calendar_time_portfolio(high, k_months, weighting=weighting)
    ctp_low = calendar_time_portfolio(low, k_months, weighting=weighting)
    hi_by_month = {mo: (r, held) for mo, r, held in ctp_high}
    lo_by_month = {mo: (r, held) for mo, r, held in ctp_low}
    common_months = sorted(set(hi_by_month) & set(lo_by_month))
    spread_rows = [
        (mo, hi_by_month[mo][0] - lo_by_month[mo][0], min(hi_by_month[mo][1], lo_by_month[mo][1]))
        for mo in common_months
    ]

    eff = effective_blocks(len(spread_rows), k_months)
    if eff < min_independent_blocks:
        return None

    alpha, _betas = ff3_alpha(spread_rows, ff3)
    # The CI resamples ISSUERS, not months. `stationary_block_bootstrap_alpha` answers "how
    # smooth is this monthly series?" while the parent verdict's `alpha_ci` answers "which
    # events did this cohort catch?" -- one verdict object was shipping both, one key apart.
    # There is deliberately NO fallback to the month bootstrap: it fails on THIN cohorts,
    # which is exactly where the month bootstrap's interval is most artificially tight, so
    # falling back would substitute the known-too-tight estimator precisely where the data is
    # weakest. Abstain instead, as `ols`/`_monthly_path`/`measure_cohort` all do.
    ci, z0, n_fit, n_discarded = event_bootstrap_spread_alpha(
        eligible, ff3, k_months, min_bucket_events=min_bucket_events,
        n_boot=n_boot, seed=seed, weighting=weighting)
    high_ir = information_ratio(ctp_high, ff3)
    low_ir = information_ratio(ctp_low, ff3)

    # Per-bucket measurable fractions, computed over ALL composite-defined events -- NOT over
    # `eligible`, which already filtered on `m.measurable` and would therefore report a
    # tautological 1.0/1.0. `immature` events are excluded, matching
    # `CohortMeasurement.measurable_fraction()`'s mature-only (H2) denominator: without that
    # filter these are OLD-STYLE POOLED fractions and are not comparable to the floor they are
    # about to be tested against, to the pooled fraction the digest prints, or to the vintage
    # buckets. That mismatch is the trap `backfill.py`'s `fraction_note` already exists to
    # prevent; it shipped here on 2026-08-03 and is fixed now.
    def _bucket(side_pred):
        pool = [m for m in measured
                if m.composite is not None and not m.immature and side_pred(m.composite)]
        if not pool:
            return None, 0
        return sum(1 for m in pool if m.measurable) / len(pool), len(pool)

    high_frac, n_high_pool = _bucket(lambda c: c >= median)
    low_frac, n_low_pool = _bucket(lambda c: c < median)

    # Per-bucket floor. The spread's whole claim to survive cohort-level suppression is that
    # it differences two buckets measured THE SAME WAY, so a common attrition bias cancels.
    # When a bucket falls below the floor the operator already pre-registered, that premise is
    # untestable and the SPREAD -- not the fractions -- is what must stop being quotable.
    #
    # The fractions are never suppressed: `_suppress_level` exists because attrition biases
    # RETURNS, and a measurable fraction is not biased by attrition, it IS the measurement of
    # it. (The cohort's pooled fraction is likewise printed unsuppressed in the digest.)
    # Blanking the diagnostic while keeping the statistic whose validity it tests was the
    # first draft of this change and was rejected in review.
    #
    # `min_measurable_frac` is the ALREADY-REGISTERED parameter applied to a new population --
    # the same adjudication made for the ds floor -- NOT a post-hoc `|high-low|` tolerance,
    # which would need its own pre-registration.
    bucket_below_floor = None
    if min_measurable_frac is not None:
        bucket_below_floor = any(
            f is not None and f < min_measurable_frac for f in (high_frac, low_frac))

    out = {
        "n_high": len(high),
        "n_low": len(low),
        "n_high_pool": n_high_pool,      # denominators of high_frac/low_frac -- a DIFFERENT
        "n_low_pool": n_low_pool,        # population from n_high/n_low (measurable-only)
        "months": len(spread_rows),
        "effective_blocks": eff,
        "spread_alpha_monthly": alpha,
        "spread_ci": ci,
        "spread_ci_method": "issuer_bootstrap" if ci is not None else "unavailable",
        # None = NOT ADJUDICATED. Previously hard-coded False, which asserted a decision no
        # one had made: a caller that never reached `attach_double_sort` got a dict claiming
        # it had been cleared. An unadjudicated result must look unadjudicated.
        "level_suppressed": None,
        "z0": z0,
        "n_boot_fitted": n_fit,
        "n_boot_discarded": n_discarded,
        "high_ir": high_ir,
        "low_ir": low_ir,
        "high_frac": high_frac,
        "low_frac": low_frac,
    }
    if bucket_below_floor is not None:
        out["bucket_below_floor"] = bucket_below_floor
        out["level_suppressed"] = False
        if bucket_below_floor:
            out.update(spread_alpha_monthly=None, spread_ci=None,
                       spread_ci_method="suppressed_bucket_floor", level_suppressed=True)
    return out


@dataclass
class SignalVerdict:
    signal: str
    verdict: str                       # "KILL" | "HOLD" | "INSUFFICIENT" — never PROMOTE
    ir: float | None
    alpha_monthly: float | None
    alpha_ci: tuple[float, float] | None
    effective_blocks: int
    n_selected: int
    n_measurable: int
    measurable_fraction: float
    sensitivity_flip: bool
    cohort_type: str = "raw"           # "raw" | "scored_gated" (spec R-B5)
    notes: list[str] = field(default_factory=list)
    double_sort: dict | None = None    # high-vs-low composite spread (design B1; additive)
    # v2 design B2 -- appended at the very end (positional back-compat): the pooled
    # old-style (pre-H2-fix) fraction is permanently reconstructable as
    # n_measurable / (n_selected + n_immature) == n_measurable / n_events.
    n_immature: int = 0                 # events excluded from n_selected/measurable_fraction (H2)
    n_events: int = 0                   # RAW count incl. immature -- full transparency
    # True when the measurable-fraction floor (pooled or vintage) rejected this cohort, so
    # `alpha_monthly`/`alpha_ci`/`ir` were BLANKED rather than reported (see `_suppress_level`).
    # Distinguishes "the level exists but is not quotable" from "the level could not be
    # computed" -- both render as None/"-" without it.
    alpha_suppressed: bool = False


_SUPPRESSION_NOTE = (
    "alpha level SUPPRESSED — the measurable-fraction floor rejected this cohort, so its "
    "level is not interpretable (attrition is outcome-correlated: names disappear via "
    "acquisition/delisting, which removes winners non-randomly). The within-cohort "
    "double-sort spread is unaffected — it cancels the common bias. "
    "See docs/audits/2026-07-26-funnel-composition-audit.md §5."
)


def _floor_failures(measurement, prereg: dict):
    """(pooled_below_floor, bad_vintages) — pure, appends no notes.

    Computed BEFORE any verdict branch so the level suppression can be applied on every
    return path (including the unsupported-factor-model early return), while `decide` keeps
    appending its floor notes in the original order.
    """
    floor = prereg.get("min_measurable_frac", 0.90)
    min_bucket_events = prereg.get("min_bucket_events", 5)
    if measurement.measurable_fraction() < floor:
        return True, []
    by_vintage = measurement.measurable_fraction_by_vintage()
    bad = [(yr, n_meas, n_sel, vfrac)
           for yr, (n_meas, n_sel, vfrac) in sorted(by_vintage.items())
           if n_sel >= min_bucket_events and vfrac < floor]
    return False, bad


def _suppress_level(verdict: SignalVerdict) -> SignalVerdict:
    """Blank the cohort's alpha LEVEL (alpha, its CI, and the IR that scales it).

    A committed floor outranks a reading of the numbers, and on 2026-07-26 four successive
    conclusions were retracted because the evaluator printed the level and the INSUFFICIENT
    verdict side by side and the level got quoted anyway. A suppressed field cannot be read
    past; a caveat can (CLAUDE.md: "prefer making the guard mechanical").
    """
    verdict.alpha_monthly = None
    verdict.alpha_ci = None
    verdict.ir = None
    verdict.alpha_suppressed = True
    verdict.notes.append(_SUPPRESSION_NOTE)
    return verdict


# The double-sort's SPREAD legs are differences between two buckets measured the same way, so
# a common attrition bias LARGELY cancels and they survive suppression. `high_ir`/`low_ir` are
# ABSOLUTE per-bucket levels -- they carry exactly the bias the floor rejected, so they must
# not. "Largely" is the audit's own word (§4.4) and is load-bearing: cancellation is exact
# only if both buckets lose names at the same rate AND the missing names' outcome gap is the
# same on both sides, neither of which is established -- and the spread is an FF3 intercept
# fitted on a data-dependent common-month subset, not a difference of means. `high_frac` /
# `low_frac` are reported so that assumption is checkable rather than asserted; enforcing a
# tolerance on their gap needs a PRE-REGISTERED threshold and is deliberately not done here
# (docs/EVALUATOR_CORRECTNESS.md §3.5).
_ABSOLUTE_DOUBLE_SORT_LEGS = ("high_ir", "low_ir")


def attach_double_sort(verdict: SignalVerdict, ds: dict | None, *,
                       ds_floor_failed: bool = False) -> SignalVerdict:
    """Attach a double-sort result to `verdict`, blanking its ABSOLUTE per-bucket legs when
    the verdict's level is suppressed OR the double-sort cohort fails its own floor.

    Assignment goes through this helper rather than `verdict.double_sort = ds` so a
    suppressed verdict can never ship a quotable level one key down from the ones R-0f just
    blanked (the raw `--json` / `scout/validate-latest.json` surface, which is where audits
    are actually written from). `spread_alpha_monthly`/`spread_ci` and every count are left
    alone -- the spread is the statistic this data supports.

    `ds_floor_failed` closes TODO 0g: the double-sort cohort is composite-defined and
    GATE-AGNOSTIC, a strict superset of the gate-filtered cohort the parent verdict measures,
    so it is a different population whose measurable fraction was never tested against the
    floor. Measured on all four committed cohorts it is measured BETTER than the parent on
    both the pooled and the vintage branch, so this guard is PREVENTIVE -- it has never fired,
    and must not be described as correcting an active bias. It ships because a floor added
    only once it has already bitten is worth nothing.
    """
    if ds is not None and (verdict.alpha_suppressed or ds_floor_failed):
        ds = dict(ds)
        for leg in _ABSOLUTE_DOUBLE_SORT_LEGS:
            if leg in ds:
                ds[leg] = None
        ds["level_suppressed"] = True
        if ds_floor_failed and not verdict.alpha_suppressed:
            verdict.notes.append(
                "double-sort per-bucket levels SUPPRESSED — the double-sort cohort "
                "(composite-defined, gate-agnostic) failed the measurable-fraction floor "
                "even though the parent cohort cleared it. The SPREAD is unaffected.")
    verdict.double_sort = ds
    return verdict


def decide(measurement, ctp_rows, ff3, k_months: int, prereg: dict,
           sensitivity_flip: bool = False, cohort_type: str = "raw") -> SignalVerdict:
    """KILL / HOLD / INSUFFICIENT (never PROMOTE) per spec §6.4. Kill is cheap; promote is
    out of scope for v1 (needs live corroboration + regime span + a factor model verdict).

    R-A4: beyond the pooled measurable-fraction floor, INSUFFICIENT also fires if ANY
    vintage bucket (`CohortMeasurement.measurable_fraction_by_vintage()`) with at least
    `min_bucket_events` events falls below the floor -- a pooled pass can hide a recent
    vintage (e.g. still-immature 2024 events) that is nowhere near measurable, which would
    silently bias the measured cohort toward older, more-measured events.

    R-0f: whenever EITHER floor fails, the cohort's alpha LEVEL (`alpha_monthly`,
    `alpha_ci`, `ir`) is SUPPRESSED to None and `alpha_suppressed` is set — a rejected
    cohort must not hand the reader a number to quote (`_suppress_level`). Sample
    diagnostics (fractions, counts, blocks) and the within-cohort double-sort SPREAD are
    untouched: the spread is a difference between two identically-measured buckets, so the
    attrition bias cancels there. The double-sort's ABSOLUTE legs (`high_ir`/`low_ir`) do
    NOT get that exemption — see `attach_double_sort`, which every assignment must go
    through.

    R-B5: a KILL on a "raw" (undifferentiated firehose) cohort is framed as confirmatory,
    not fresh evidence -- the scored/double-sort cohort (post quality/gate filtering) is
    the decision-relevant one; a raw-cohort kill corroborates but doesn't by itself settle it.
    """
    notes: list[str] = []
    n_immature = measurement.n_immature
    n_events = measurement.n_events
    if n_immature > 0:
        # v2 design B2/H2-note: every verdict surface must show the exclusion explicitly,
        # so a reader can reconstruct the old pooled fraction (n_measurable/n_events).
        notes.append(f"n_immature={n_immature} excluded from the denominator (H2)")
    # The floor decides two separate things: the verdict (below) and whether this cohort's
    # alpha LEVEL may be reported at all (R-0f). Compute it once, up front, so the
    # suppression also covers the factor-model early return.
    pooled_below, bad_vintages = _floor_failures(measurement, prereg)
    floor_failed = pooled_below or bool(bad_vintages)
    frac = measurement.measurable_fraction()
    n_months = len(ctp_rows)
    eff = effective_blocks(n_months, k_months)
    ir = information_ratio(ctp_rows, ff3)
    alpha, _betas = ff3_alpha(ctp_rows, ff3)
    # The KILL rule reads `ci`, so it must reflect the dominant uncertainty — WHICH EVENTS
    # this cohort caught — not the smoothness of the flattened CTP series (audit
    # docs/audits/2026-07-26-funnel-composition-audit.md §3a). Fall back to the month
    # bootstrap only when the cohort carries no event list (hand-built / old persisted
    # measurements), so a CI is never silently dropped to None.
    # The month bootstrap is used ONLY when there is no event list at all (hand-built or old
    # persisted CohortMeasurements) -- there, it is the only model the data supports, not a
    # substitute for a better one. When events DO exist and the issuer bootstrap still can't
    # compute, abstain: falling back would swap in the known-too-tight estimator on exactly
    # the thinnest cohorts. A None CI routes to INSUFFICIENT below, which is the honest read.
    _events = getattr(measurement, "events", None) or []
    if _events:
        ci = event_bootstrap_alpha(_events, ff3, k_months)
    else:
        ci = stationary_block_bootstrap_alpha(ctp_rows, ff3, k_months)
    if ir is not None and not floor_failed:
        # `information_ratio` divides by the residual std of the flattened series, which is
        # understated for the same reason. Display-only (no verdict reads it) but it must
        # not be quoted as if it were a real risk-adjusted number. Skipped when the level is
        # about to be suppressed — a caveat about a blanked number only advertises it.
        notes.append("IR is upward-biased (flattened-CTP residual variance) — display only")

    # Enforce the pre-registered factor model. v1 only implements FF3; a CAPM/FF5 prereg must
    # fail loudly (INSUFFICIENT) rather than silently run FF3 and mislabel the result.
    factor_model = prereg.get("factor_model", "ff3")
    if factor_model != "ff3":
        v = SignalVerdict(
            signal=measurement.signal, verdict="INSUFFICIENT", ir=ir, alpha_monthly=alpha,
            alpha_ci=ci, effective_blocks=eff, n_selected=measurement.n_selected,
            n_measurable=measurement.n_measurable, measurable_fraction=frac,
            sensitivity_flip=sensitivity_flip, cohort_type=cohort_type,
            notes=notes + [f"factor_model '{factor_model}' not supported in v1 (only ff3)"],
            n_immature=n_immature, n_events=n_events)
        return _suppress_level(v) if floor_failed else v

    verdict = "HOLD"
    if pooled_below:
        verdict = "INSUFFICIENT"
        notes.append(f"measurable fraction {frac:.2f} < floor")
    elif bad_vintages:
        verdict = "INSUFFICIENT"
        detail = ", ".join(f"{yr}: {vfrac:.2f} ({n_meas}/{n_sel})" for yr, n_meas, n_sel, vfrac in bad_vintages)
        notes.append(f"vintage-stratified measurable fraction below floor for {detail}")

    if verdict == "HOLD" and eff < prereg.get("min_independent_blocks", 2):
        verdict = "INSUFFICIENT"
        notes.append(f"{eff} independent blocks < min")
    elif verdict == "HOLD" and sensitivity_flip:
        verdict = "INSUFFICIENT"
        notes.append("delisting-return sensitivity band flips the sign")
    # "Could not compute a risk-adjusted alpha" is NOT "non-negative alpha" -- a missing alpha
    # (empty/misaligned FF3, e.g. a failed factor fetch) or missing bootstrap CI must read as
    # INSUFFICIENT, never fall through to the HOLD "no negative evidence" branch (verdict honesty).
    elif verdict == "HOLD" and (alpha is None or ci is None):
        verdict = "INSUFFICIENT"
        notes.append("could not compute FF3 alpha (insufficient factor overlap / alignment)")
    elif verdict == "HOLD" and ci is not None and ci[1] < 0:
        verdict = "KILL"
        notes.append(f"alpha 90% CI entirely negative {ci}")
    elif verdict == "HOLD" and alpha is not None and alpha <= 0:
        # Operator decision 2026-07-26: a bare negative point estimate is NOT disproof.
        # This branch used to KILL, which condemns roughly half of all genuinely-null
        # signals by coin flip and produced "KILL on evidence" audit wording that the
        # interval never supported (see docs/audits/2026-07-26-funnel-composition-audit.md
        # §3a). A negative point estimate whose CI still spans zero is inconclusive.
        verdict = "INSUFFICIENT"
        notes.append(f"point alpha {alpha:.4f}/mo <= 0 but CI spans zero — inconclusive")
    elif verdict == "HOLD":
        notes.append("no negative evidence; HOLD (promote requires live corroboration + factor verdict)")

    if verdict == "KILL" and cohort_type == "raw":
        notes.append(
            "raw-cohort KILL is confirmatory, not new evidence — the scored/double-sort "
            "cohort (post quality/gate filtering) is decision-relevant"
        )

    out = SignalVerdict(
        signal=measurement.signal, verdict=verdict, ir=ir, alpha_monthly=alpha, alpha_ci=ci,
        effective_blocks=eff, n_selected=measurement.n_selected,
        n_measurable=measurement.n_measurable, measurable_fraction=frac,
        sensitivity_flip=sensitivity_flip, cohort_type=cohort_type, notes=notes,
        n_immature=n_immature, n_events=n_events)
    return _suppress_level(out) if floor_failed else out


def median_split(events: list[MeasuredEvent]):
    """(high, low, median) — split at the median composite, ties to the HIGH side.

    Shared by `double_sort`'s point estimate and by every bootstrap replicate, so the
    statistic being resampled is the statistic being reported BY CONSTRUCTION rather than by
    two copies of the same arithmetic staying in sync.

    Known degeneracy, by design rather than oversight: on a composite distribution with heavy
    tie mass at the median (a synthetic two-point fixture, say) the median can equal the tied
    value, sending every event to the HIGH side and emptying LOW. Callers treat that as a
    discarded replicate. On the real cohorts, ties at the median run 0.4-1.2% (9/2400 for 13d,
    4/1344 for 8k, 5/535 for buyback, 42/7546 for 8k-neg), so it does not bite in production.
    """
    comps = sorted(m.composite for m in events)
    n = len(comps)
    if n == 0:
        return [], [], None
    median = comps[n // 2] if n % 2 else (comps[n // 2 - 1] + comps[n // 2]) / 2.0
    high = [m for m in events if m.composite >= median]
    low = [m for m in events if m.composite < median]
    return high, low, median


def _live_events(measured: list[MeasuredEvent]) -> list[MeasuredEvent]:
    return [m for m in measured
            if m.measurable and m.ret is not None and m.event_date is not None]


def _lcg(seed: int):
    """Deterministic stdlib LCG — same generator as the block bootstrap, so every CI in this
    module is reproducible across runs and comparable across paths."""
    state = seed & 0xFFFFFFFF

    def _rand():
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    return _rand


def _resample_by_issuer(live: list[MeasuredEvent], rand) -> list[MeasuredEvent]:
    """One bootstrap replicate: resample ISSUERS with replacement (issuer count preserved),
    taking ALL of a drawn issuer's events, relabelled per ISSUER-COPY.

    Two things are load-bearing and were both wrong in the first implementation:

    1. **Resample issuers, not events.** These cohorts are heavily clustered by issuer
       (48-57% of events sit on a multi-event issuer) and the composite is largely a
       FIRM-level attribute (within-issuer composite sd ~1/4 of cross-sectional), so a firm's
       events share a bucket and a firm-level return shock. An i.i.d.-event resample treats
       them as independent.
    2. **Relabel per issuer-COPY, not per draw index.** The old code relabelled every drawn
       event uniquely (`f"{ticker}#{j}"` with j the draw index), which does not merely
       un-dedup repeat DRAWS -- it disables `calendar_time_portfolio`'s same-ticker dedup for
       genuinely distinct events of the same issuer. Measured held-set inflation: +19.6%
       (13d), +23.7% (8k-neg). The bootstrap was therefore applying a different function to
       the resample than the one that produced the reported point estimate, which is not
       consistent for that estimate's sampling distribution. Suffixing by issuer-copy keeps
       dedup active INSIDE a copy while still letting a twice-drawn issuer count twice.

    See docs/EVALUATOR_CORRECTNESS.md §2.2-§2.3.
    """
    by_issuer: dict[str, list[MeasuredEvent]] = {}
    for m in live:
        by_issuer.setdefault(m.ticker, []).append(m)
    keys = list(by_issuer)
    nk = len(keys)
    draw: list[MeasuredEvent] = []
    for copy_idx in range(nk):
        tk = keys[int(rand() * nk) % nk]
        draw.extend(replace(m, ticker=f"{m.ticker}#{copy_idx}") for m in by_issuer[tk])
    return draw


def _bias_corrected_interval(alphas: list[float], theta_hat: float | None,
                             lo: float = 0.05, hi: float = 0.95):
    """(interval, z0) — bias-corrected percentile interval.

    The spread statistic re-splits at each replicate's own median, which introduces a small
    known-signed UPWARD bias (the high-minus-low spread is minimised at the median, so
    E[S(m*)] >= S(m) by Jensen). A naive percentile interval shifts TOWARD that bias, i.e. it
    would be systematically optimistic about the composite's sorting power -- precisely the
    wrong direction for the one statistic this project still leans on. The BC adjustment
    costs one extra pass and no jackknife.

    Abstains to the naive percentile (z0 = 0.0) when z0 is undefined -- every replicate on
    one side of the point estimate -- rather than producing an infinite adjustment.
    """
    b = len(alphas)
    naive = (alphas[int(lo * b)], alphas[int(hi * b)])
    if theta_hat is None:
        return naive, 0.0
    n_less = sum(1 for a in alphas if a < theta_hat)
    if n_less == 0 or n_less == b:
        return naive, 0.0
    nd = NormalDist()
    z0 = nd.inv_cdf(n_less / b)
    i_lo = int(nd.cdf(2 * z0 + nd.inv_cdf(lo)) * b)
    i_hi = int(nd.cdf(2 * z0 + nd.inv_cdf(hi)) * b)
    i_lo = min(max(i_lo, 0), b - 1)
    i_hi = min(max(i_hi, 0), b - 1)
    if i_hi <= i_lo:
        return naive, z0
    return (alphas[i_lo], alphas[i_hi]), z0


def event_bootstrap_alpha(measured: list[MeasuredEvent], ff3, k_months: int,
                          n_boot: int = 500, min_obs: int = 6, seed: int = 12345,
                          weighting: str = "equal"):
    """Issuer-clustered bootstrap CI (5th/95th pct) of the FF3 alpha — resamples ISSUERS with
    replacement, rebuilds the calendar-time portfolio inside each replicate, and refits.

    Why not `stationary_block_bootstrap_alpha`: that one resamples MONTHS of an aggregated
    CTP series, so it measures the smoothness of that series rather than the uncertainty in
    which events the cohort happened to catch — which is how the committed verdicts reached
    an implied monthly tracking error of 0.32% and an IR of -46.97 (audit
    docs/audits/2026-07-26-funnel-composition-audit.md §3a). With ~500-2000 events against
    ~50 months, the dominant uncertainty is cross-sectional.

    Clustering + relabelling rationale: see `_resample_by_issuer`. The month grid is anchored
    to the ORIGINAL cohort so replicates cannot silently shorten the calendar window.
    """
    live = _live_events(measured)
    n = len(live)
    if n < min_obs or k_months <= 0:
        return None
    rand = _lcg(seed)
    span = (min(m.event_date for m in live), max(m.event_date for m in live))
    alphas = []
    for _ in range(n_boot):
        rows = calendar_time_portfolio(_resample_by_issuer(live, rand), k_months,
                                       weighting=weighting, month_span=span)
        a, _b = ff3_alpha(rows, ff3, min_obs=min_obs)
        if a is not None:
            alphas.append(a)
    if len(alphas) < n_boot // 2:
        return None
    alphas.sort()
    point, _betas = ff3_alpha(
        calendar_time_portfolio(live, k_months, weighting=weighting, month_span=span),
        ff3, min_obs=min_obs)
    interval, _z0 = _bias_corrected_interval(alphas, point)
    return interval


def event_bootstrap_spread_alpha(eligible: list[MeasuredEvent], ff3, k_months: int, *,
                                 min_bucket_events: int, n_boot: int = 500,
                                 min_obs: int = 6, seed: int = 12345,
                                 weighting: str = "equal"):
    """(interval, z0, n_fitted, n_discarded) for the high-minus-low composite SPREAD alpha —
    or (None, ...) when it cannot be computed.

    Per replicate: resample issuers (`_resample_by_issuer`), RE-SPLIT at that replicate's own
    median composite, rebuild both calendar-time portfolios on the anchored month grid, take
    the spread over common months, refit.

    **Joint resample + re-split, not a stratified within-bucket resample.** The median split
    is part of the estimator, so the bootstrap has to repeat it; conditioning on a split that
    itself carries sampling error understates the interval. Measured, that choice is worth
    only ~2% of interval width — it is defensible rather than decisive, and the re-split's
    Jensen bias is what `_bias_corrected_interval` exists to absorb.

    A replicate whose re-split violates `min_bucket_events` is DISCARDED AND COUNTED, never
    silently tolerated: `n_discarded` ships in the double-sort dict so selection on the
    resample is visible.
    """
    live = _live_events(eligible)
    if len(live) < min_obs or k_months <= 0:
        return None, 0.0, 0, 0
    rand = _lcg(seed)
    span = (min(m.event_date for m in live), max(m.event_date for m in live))

    def _spread_rows(events):
        high, low, _med = median_split(events)
        if len(high) < min_bucket_events or len(low) < min_bucket_events:
            return None
        hi = {mo: (r, held) for mo, r, held in
              calendar_time_portfolio(high, k_months, weighting=weighting, month_span=span)}
        lo = {mo: (r, held) for mo, r, held in
              calendar_time_portfolio(low, k_months, weighting=weighting, month_span=span)}
        return [(mo, hi[mo][0] - lo[mo][0], min(hi[mo][1], lo[mo][1]))
                for mo in sorted(set(hi) & set(lo))]

    alphas, discarded = [], 0
    for _ in range(n_boot):
        rows = _spread_rows(_resample_by_issuer(live, rand))
        if rows is None:
            discarded += 1
            continue
        a, _b = ff3_alpha(rows, ff3, min_obs=min_obs)
        if a is not None:
            alphas.append(a)
    if len(alphas) < n_boot // 2:
        return None, 0.0, len(alphas), discarded
    alphas.sort()
    base = _spread_rows(live)
    point = ff3_alpha(base, ff3, min_obs=min_obs)[0] if base else None
    interval, z0 = _bias_corrected_interval(alphas, point)
    return interval, z0, len(alphas), discarded


def stationary_block_bootstrap_alpha(ctp_rows, ff3, k_months: int,
                                     n_boot: int = 2000, min_obs: int = 6, seed: int = 12345):
    """Block-bootstrap CI (5th/95th pct) of the FF3 alpha with mean block length = k_months
    (block >= K, spec §6.3/F1). Beta is re-estimated inside each replicate (F13). Uses a
    deterministic stdlib LCG (no Math.random) so the CI is reproducible across runs."""
    y, X = _aligned(ctp_rows, ff3)
    n = len(y)
    if n < min_obs or k_months <= 0:
        return None
    state = seed & 0xFFFFFFFF

    def _rand():
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    p = 1.0 / k_months                                   # geometric block-length param
    alphas = []
    for _ in range(n_boot):
        by, bX = [], []
        while len(by) < n:
            start = int(_rand() * n) % n
            i = start
            by.append(y[i])
            bX.append(X[i])
            while _rand() > p and len(by) < n:           # extend the block
                i = (i + 1) % n
                by.append(y[i])
                bX.append(X[i])
        try:
            b = ols(by, bX)
            alphas.append(b[0])
        except ValueError:
            continue
    if len(alphas) < n_boot // 2:
        return None
    alphas.sort()
    lo = alphas[int(0.05 * len(alphas))]
    hi = alphas[int(0.95 * len(alphas))]
    return (lo, hi)
