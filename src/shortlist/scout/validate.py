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
                seed: int = 12345) -> dict | None:
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

    composites = sorted(m.composite for m in eligible)
    n = len(composites)
    if n % 2 == 1:
        median = composites[n // 2]
    else:
        median = (composites[n // 2 - 1] + composites[n // 2]) / 2.0
    high = [m for m in eligible if m.composite >= median]     # ties -> high side
    low = [m for m in eligible if m.composite < median]
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
    ci = stationary_block_bootstrap_alpha(spread_rows, ff3, k_months, n_boot=n_boot, seed=seed)
    high_ir = information_ratio(ctp_high, ff3)
    low_ir = information_ratio(ctp_low, ff3)

    return {
        "n_high": len(high),
        "n_low": len(low),
        "months": len(spread_rows),
        "effective_blocks": eff,
        "spread_alpha_monthly": alpha,
        "spread_ci": ci,
        "high_ir": high_ir,
        "low_ir": low_ir,
    }


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
    diagnostics (fractions, counts, blocks) and the within-cohort double-sort spread are
    untouched: the spread is a difference between two identically-measured buckets, so the
    attrition bias cancels there.

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
    ci = event_bootstrap_alpha(getattr(measurement, "events", None) or [], ff3, k_months)
    if ci is None:
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


def event_bootstrap_alpha(measured: list[MeasuredEvent], ff3, k_months: int,
                          n_boot: int = 500, min_obs: int = 6, seed: int = 12345,
                          weighting: str = "equal"):
    """Event-level bootstrap CI (5th/95th pct) of the FF3 alpha — resamples the EVENTS with
    replacement, rebuilds the calendar-time portfolio inside each replicate, and refits.

    Why not `stationary_block_bootstrap_alpha`: that one resamples MONTHS of an
    already-flattened CTP series. `calendar_time_portfolio` replaces each event's K-month
    path with a constant monthly rate and then averages across held names, so cross-sectional
    dispersion in event outcomes is gone before the bootstrap runs. The resulting CI measures
    the smoothness of a smoothed series, not the uncertainty in the cohort's mean — which is
    how the committed verdicts reached an implied monthly tracking error of 0.32% and an IR of
    -46.97 (audit docs/audits/2026-07-26-funnel-composition-audit.md §3a). The dominant
    uncertainty is WHICH EVENTS the cohort happened to catch, so that is what we resample.

    Each drawn event is relabelled with a unique ticker inside the replicate: the CTP's
    same-ticker dedup is correct for a real repeat firer, but in a resample a twice-drawn
    event MUST count twice or the bootstrap's reweighting is silently discarded.

    Deterministic stdlib LCG (same generator as the block bootstrap) so the CI is
    reproducible across runs.
    """
    live = [m for m in measured if m.measurable and m.ret is not None and m.event_date is not None]
    n = len(live)
    if n < min_obs or k_months <= 0:
        return None
    state = seed & 0xFFFFFFFF

    def _rand():
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    alphas = []
    for _ in range(n_boot):
        draw = []
        for j in range(n):
            src = live[int(_rand() * n) % n]
            draw.append(replace(src, ticker=f"{src.ticker}#{j}"))
        rows = calendar_time_portfolio(draw, k_months, weighting=weighting)
        a, _b = ff3_alpha(rows, ff3, min_obs=min_obs)
        if a is not None:
            alphas.append(a)
    if len(alphas) < n_boot // 2:
        return None
    alphas.sort()
    return (alphas[int(0.05 * len(alphas))], alphas[int(0.95 * len(alphas))])


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
