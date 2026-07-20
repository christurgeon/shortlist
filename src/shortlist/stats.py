from __future__ import annotations

from datetime import date
from statistics import mean, median, pstdev
from typing import Optional

# Consecutive-fiscal-end spacing window for the asset-growth / accruals signals.
# Admits 52/53-week years; excludes gap-spanning ratios (a missing year would put
# the prior end ~2yr back) and transition/stub-period annuals. SINGLE SOURCE for
# this window: providers/_xbrl_facts.py imports these for its annual_series
# period guard (same window, applied there to duration spans and here to INSTANT
# balance-sheet dates, which annual_series does not length-check).
_FY_MIN_DAYS = 350
_FY_MAX_DAYS = 380


def median_pe(pes: list[Optional[float]], min_points: int = 2) -> Optional[float]:
    """Median of historical annual PE ratios; the `pe_median_5y` anchor that
    `pe_vs_history()` measures the current PE against.

    Drops falsy values (None and 0 — a 0 PE is a missing/degenerate reading);
    keeps negatives. Returns None with fewer than `min_points` usable points.
    This is the single source of truth for the formula used by BOTH the screener
    FMP provider and the harness FMP source — do not reinline it."""
    vals = [p for p in pes if p]
    if len(vals) < min_points:
        return None
    return median(vals)


def avg_roic(roics: list[Optional[float]], min_points: int = 2) -> Optional[float]:
    """Mean of historical annual ROIC; the `roic_5y_avg` moat input that smooths
    a one-off TTM spike into a persistent return-on-capital read.

    Drops None values; keeps zeros and negatives (a real low/negative ROIC year).
    Returns None with fewer than `min_points` usable points."""
    vals = [r for r in roics if r is not None]
    if len(vals) < min_points:
        return None
    return mean(vals)


def cagr(series: list[Optional[float]], most_recent_first: bool = True,
         min_points: int = 3) -> Optional[float]:
    """Compound annual growth rate over a financial series.

    Drops None values (a missing year), then requires >= `min_points` usable
    points. Returns None when either endpoint is <= 0: CAGR is undefined across a
    sign change (a swing through zero makes the ratio meaningless), and a growth
    rate computed off two negative endpoints (or a zero endpoint) is not
    interpretable either — the caller's weight-redistribution handles the gap.
    `most_recent_first=True`
    matches `Statements`' newest-first ordering. Single source of truth for the
    growth-rate legs in BOTH the screener provider and the harness bridge."""
    vals = [v for v in (series or []) if v is not None]
    if len(vals) < min_points:
        return None
    if most_recent_first:
        vals = list(reversed(vals))
    start, end = vals[0], vals[-1]
    if start <= 0 or end <= 0:
        return None
    n = len(vals) - 1
    return (end / start) ** (1 / n) - 1.0


def growth_persistence(series: list[Optional[float]], most_recent_first: bool = True,
                       min_points: int = 3) -> Optional[float]:
    """0..1 consistency proxy: the fraction of consecutive year-over-year periods
    that grew. Sign-safe (works through negative/zero years where `cagr` can't),
    so it rewards a steady compounder over one that booked the same CAGR via a
    single spike year. Drops None values; needs >= `min_points` usable points.
    Note: dropping a None makes the years on either side of the gap adjacent, so a
    missing year is treated as a single YoY step rather than a break — acceptable
    given statement gaps are rare."""
    vals = [v for v in (series or []) if v is not None]
    if len(vals) < min_points:
        return None
    if most_recent_first:
        vals = list(reversed(vals))
    pairs = list(zip(vals, vals[1:], strict=False))  # (older, newer)
    ups = sum(1 for older, newer in pairs if newer > older)
    return ups / len(pairs)


def gross_margin_stability(margins: list[float]) -> Optional[float]:
    """0..1 moat proxy: higher = steadier gross margins.

    `max(0, 1 - stdev/mean)` over >=3 yearly gross margins (population stdev, to
    match the screener FMP provider). Returns None with <3 points or a non-positive
    mean margin (a negative mean is not a meaningful stability base and would push
    the proxy above 1). This is the single source of truth for the formula used by
    BOTH the screener provider and the harness bridge — do not reinline it."""
    if len(margins) < 3:
        return None
    avg = mean(margins)
    if avg <= 0:
        return None
    return max(0.0, 1.0 - (pstdev(margins) / avg))


def piotroski_f(*, net_income: list[Optional[float]], ocf: list[Optional[float]],
                total_debt: list[Optional[float]], gross_profit: list[Optional[float]],
                revenue: list[Optional[float]],
                most_recent_first: bool = True) -> tuple[Optional[int], Optional[int]]:
    """Core-6 Piotroski-inspired fundamental-quality score (asset-free, equity-free).

    Each arg is a financial series; index alignment is positional (newest-first by
    default), matching the rest of stats.py — a rare missing year makes neighbors
    adjacent (same tradeoff cagr/growth_persistence make).

    Six tests; F1-F3 are single-year LEVELS (latest year only), F4-F6 are 1-year
    DELTAS (latest t vs prior t-1, requiring revenue>0 each year — the only division
    guard):
      F1 net_income>0, F2 ocf>0, F3 ocf>net_income (accruals quality),
      F4 net margin rising (net_income/revenue), F5 debt-intensity falling
      (total_debt/revenue), F6 gross margin rising (gross_profit/revenue).

    Deliberately asset-free AND equity-free: total assets is not extracted on either
    stack, and equity denominators darken/distort on buyback-heavy firms (see spec
    §3). Revenue is the natural denominator; a non-positive (zero or negative) revenue year abstains the three delta legs.

    Returns RAW (won, evaluated) — no min-legs floor here, so this leaf needs no
    config (the floor is applied by consumers in scoring/backtest). A 1-year input
    yields (<=3, <=3) from the level legs; when NO leg is evaluable (all series empty)
    it returns (None, None) — the model's 'no data' sentinel. Pure; no I/O."""
    def _series(s: Optional[list[Optional[float]]]) -> list[Optional[float]]:
        s = list(s or [])
        return s if most_recent_first else list(reversed(s))

    ni = _series(net_income)
    cf = _series(ocf)
    dbt = _series(total_debt)
    gp = _series(gross_profit)
    rev = _series(revenue)

    def _t(s: list[Optional[float]]) -> tuple[Optional[float], Optional[float]]:
        # (latest, prior) or (None, None) if no prior year
        return (s[0], s[1]) if len(s) >= 2 else (None, None)

    won = 0
    legs = 0

    # F1 profitability: net income positive (level; abstain if latest value missing)
    if ni and ni[0] is not None:
        legs += 1
        if ni[0] > 0:
            won += 1
    # F2 cash generation: OCF positive (level; abstain if latest value missing)
    if cf and cf[0] is not None:
        legs += 1
        if cf[0] > 0:
            won += 1
    # F3 accruals quality: OCF > net income (level)
    if ni and cf and ni[0] is not None and cf[0] is not None:
        legs += 1
        if cf[0] > ni[0]:
            won += 1
    # F4 net-margin trend: net_income/revenue rising (delta; revenue>0 guard)
    ni_t, ni_p = _t(ni)
    rev_t, rev_p = _t(rev)
    if None not in (ni_t, ni_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (ni_t / rev_t) > (ni_p / rev_p):
            won += 1
    # F5 debt-intensity trend: total_debt/revenue falling (delta; revenue>0 guard)
    d_t, d_p = _t(dbt)
    if None not in (d_t, d_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (d_t / rev_t) < (d_p / rev_p):
            won += 1
    # F6 gross-margin trend: gross_profit/revenue rising (delta; revenue>0 guard)
    gp_t, gp_p = _t(gp)
    if None not in (gp_t, gp_p, rev_t, rev_p) and rev_t > 0 and rev_p > 0:
        legs += 1
        if (gp_t / rev_t) > (gp_p / rev_p):
            won += 1

    return (won, legs) if legs else (None, None)


def net_debt_from(total_debt: Optional[float],
                  cash: Optional[float]) -> Optional[float]:
    """Net debt = total_debt - cash (signed; net cash -> negative). Returns None
    only when BOTH inputs are missing — a market-cap-only enterprise value would
    silently ignore leverage, so we abstain rather than guess (spec O1). A single
    missing side is treated as zero (conservative)."""
    if total_debt is None and cash is None:
        return None
    return (total_debt or 0.0) - (cash or 0.0)


def _consecutive_ends(assets_by_end: dict[str, float]) -> Optional[tuple[str, str]]:
    """The two latest fiscal ends in `assets_by_end` (ISO-date keys) spaced ~1yr
    apart, or None. Guards against gap-spanning ratios when an intermediate year
    is missing (the prior end would then sit ~2yr back, outside the window) and
    against stub/transition periods. Ends with a None value are ignored."""
    ends = sorted((e for e, v in assets_by_end.items() if v is not None), reverse=True)
    if len(ends) < 2:
        return None
    e_t, e_p = ends[0], ends[1]
    try:
        span = (date.fromisoformat(e_t) - date.fromisoformat(e_p)).days
    except ValueError:
        return None
    if not (_FY_MIN_DAYS <= span <= _FY_MAX_DAYS):
        return None
    return e_t, e_p


def asset_growth(assets_by_end: dict[str, float]) -> Optional[float]:
    """Asset-growth anomaly (Cooper-Gulen-Schill 2008): Assets_t/Assets_{t-1} - 1
    over the two latest CONSECUTIVE (~1yr-spaced) fiscal ends. A NEGATIVE predictor
    (high growth -> low future returns) — the scorer inverts the band, not this.
    None when there is no consecutive pair or the prior denominator is 0/None."""
    pair = _consecutive_ends(assets_by_end)
    if pair is None:
        return None
    e_t, e_p = pair
    a_t, a_p = assets_by_end[e_t], assets_by_end[e_p]
    if not a_p:                      # zero (or falsy) denominator -> abstain
        return None
    return a_t / a_p - 1.0


def accruals(net_income_by_end: dict[str, float], cfo_by_end: dict[str, float],
             assets_by_end: dict[str, float]) -> Optional[float]:
    """Accruals anomaly (Sloan 1996, cash-flow form): (NetIncome - CFO) / average
    total assets, where average assets = (Assets_t + Assets_{t-1})/2 over the two
    latest CONSECUTIVE fiscal ends. CFO is used AS-REPORTED (no sign flip). NI and
    CFO are taken at the latest end `t`. A NEGATIVE predictor (high accruals -> low
    future returns) — the scorer inverts the band. None when any input is missing
    at the aligned ends or average assets is 0."""
    pair = _consecutive_ends(assets_by_end)
    if pair is None:
        return None
    e_t, e_p = pair
    ni, cfo = net_income_by_end.get(e_t), cfo_by_end.get(e_t)
    a_t, a_p = assets_by_end.get(e_t), assets_by_end.get(e_p)
    if ni is None or cfo is None or a_t is None or a_p is None:
        return None
    avg_assets = (a_t + a_p) / 2.0
    if not avg_assets:
        return None
    return (ni - cfo) / avg_assets


def shareholder_yield(dividends: Optional[float], repurchases: Optional[float],
                      debt_repayments: Optional[float], debt_issuance: Optional[float],
                      market_cap: Optional[float]) -> Optional[float]:
    """Total shareholder yield (Boudoukh-Michaely-Richardson-Roberts 2007; Faber):
    (dividends + net buybacks + net debt reduction) / market_cap — cash RETURNED to
    owners, the legs FCF-yield misses (PREDICTIVE_SIGNALS §5).

    SIGN DISCIPLINE (the subtle part):
      * `dividends` / `repurchases` are OUTFLOW MAGNITUDES taken as their absolute
        value at the call site — a bigger payment is a bigger yield, so they ADD
        (never negated). abs() here normalizes the two source conventions: raw SEC
        companyfacts report `PaymentsOfDividends` / `PaymentsForRepurchaseOfCommonStock`
        as POSITIVE magnitudes, while edgartools' to_dataframe() sign-flips them to
        NEGATIVE (cash-flow direction). abs() on each leg makes both paths agree.
      * The net-debt leg is `debt_repayments - debt_issuance` with SIGN PRESERVED:
        a NET DEBT ISSUER yields a NEGATIVE leg (do NOT clamp to 0) — issuing debt is
        the opposite of returning cash. Both inputs are abs()'d magnitudes, so the
        subtraction is repayment-minus-issuance regardless of source sign.

    Any missing leg is treated as 0 (a company that didn't repurchase still has a
    dividend yield), BUT abstains (None) if ALL three legs are missing (no financing
    data at all) or market_cap is missing/non-positive."""
    legs = (dividends, repurchases, debt_repayments, debt_issuance)
    if all(x is None for x in legs):
        return None
    if not market_cap or market_cap <= 0:
        return None
    div = abs(dividends) if dividends is not None else 0.0
    repur = abs(repurchases) if repurchases is not None else 0.0
    repay = abs(debt_repayments) if debt_repayments is not None else 0.0
    issue = abs(debt_issuance) if debt_issuance is not None else 0.0
    net_debt_reduction = repay - issue          # signed: net issuer -> negative leg
    return (div + repur + net_debt_reduction) / market_cap


def surprise_dispersion(surprise_pcts: list[Optional[float]],
                        min_quarters: int = 3) -> Optional[float]:
    """Population std-dev of a firm's own recent earnings-surprise percentages —
    the denominator for SUE (PREDICTIVE_SIGNALS §1; Bernard-Thomas 1989).

    Drops None entries; requires >= `min_quarters` usable points (a 1-quarter or
    empty history has no dispersion). Returns None below that floor. NOTE: this can
    legitimately return 0.0 (all surprises identical — the common 4-equal case);
    the SUE guard, not this helper, is responsible for never dividing by ~0."""
    vals = [s for s in (surprise_pcts or []) if s is not None]
    if len(vals) < min_quarters:
        return None
    return pstdev(vals)


def sue(last_surprise_pct: Optional[float], dispersion: Optional[float],
        days_since_last_report: Optional[int], *,
        sigma_floor: float = 1.0, decay_trading_days: int = 60) -> Optional[float]:
    """Standardized earnings surprise (SUE), time-decayed over the post-announcement
    drift window (PREDICTIVE_SIGNALS §1; Bernard-Thomas 1989, Novy-Marx 2015).

    SUE = last_surprise_pct / dispersion  (a beat -> positive -> a higher score),
    then linearly DECAYED to 0 over `decay_trading_days` trading days since the last
    announcement, keyed off `days_since_last_report` (NOT days-to-NEXT — that report
    is unannounced, so anchoring decay there would leak look-ahead).

    MANDATORY GUARD (the σ≈0 trap): abstain (return None) when dispersion is None
    (too few quarters — see surprise_dispersion's min floor) OR below `sigma_floor`
    (the all-equal-surprises case, dispersion ~0, would otherwise divide to ±inf/NaN).
    Also abstain when last_surprise_pct is None. A stale print past the decay window
    decays to exactly 0 (a real, non-abstaining "no drift left" reading). Pure; no I/O.

    UNITS: last_surprise_pct and the dispersion are both in PERCENT (Finnhub's
    surprisePercent is already a percent), so SUE is dimensionless. `sigma_floor` is
    therefore in percentage points."""
    if last_surprise_pct is None or dispersion is None or dispersion < sigma_floor:
        return None
    raw = last_surprise_pct / dispersion
    if days_since_last_report is None:
        return raw                              # no decay anchor -> undecayed (fresh prior)
    if days_since_last_report < 0:
        return raw                              # future-dated artifact -> treat as fresh
    if decay_trading_days <= 0:
        return raw
    weight = max(0.0, 1.0 - days_since_last_report / decay_trading_days)
    return raw * weight


# Residual-momentum window (Blitz-Huij-Martens 2011, PREDICTIVE_SIGNALS §2). The 12-1
# convention in MONTHLY terms: regress on the trailing ~12 months, then take residuals
# over t-12..t-2 (skip the most recent month). Expressed in trading days at ~21/month.
_RESID_MOM_LOOKBACK_DAYS = 252   # ~12 months of trailing daily closes for the regression
_RESID_MOM_SKIP_DAYS = 21        # ~1 month skip (the 12-1 convention) — drop the most recent month
_RESID_MOM_MIN_POINTS = 120      # min joined daily returns for a usable beta + residual series


def join_on_dates(dates_a: list[date], closes_a: list[float],
                  dates_b: list[date], closes_b: list[float],
                  ) -> tuple[list[date], list[float], list[float]]:
    """INNER-JOIN two dated close series on their shared dates, ascending.

    Returns (dates, closes_a, closes_b) covering only dates present in BOTH series.
    This is the load-bearing correctness step for the residual-momentum regression:
    position-pairing closes_a[i] with closes_b[i] silently misaligns whenever the two
    series have different listing dates, trading halts, or lengths — producing a garbage
    beta. Joining on the date keys guarantees each paired (stock, market) close is the
    SAME calendar day. Pure; no I/O. Assumes each input's dates are ascending & unique
    (the Yahoo chart parser guarantees this); duplicate dates take the last close."""
    map_b = dict(zip(dates_b, closes_b, strict=False))
    out_d: list[date] = []
    out_a: list[float] = []
    out_b: list[float] = []
    seen: set[date] = set()
    for d, ca in zip(dates_a, closes_a, strict=False):
        if d in map_b and d not in seen:
            seen.add(d)
            out_d.append(d)
            out_a.append(ca)
            out_b.append(map_b[d])
    return out_d, out_a, out_b


def _simple_returns(closes: list[float]) -> list[float]:
    """Period-over-period simple returns; a non-positive prior close yields 0.0 (a
    halt/placeholder, not a real move) rather than a divide-by-zero."""
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out.append(closes[i] / prev - 1.0 if prev > 0 else 0.0)
    return out


def residual_momentum(stock_dates: list[date], stock_closes: list[float],
                      mkt_dates: list[date], mkt_closes: list[float], *,
                      lookback_days: int = _RESID_MOM_LOOKBACK_DAYS,
                      skip_days: int = _RESID_MOM_SKIP_DAYS,
                      min_points: int = _RESID_MOM_MIN_POINTS) -> Optional[float]:
    """Residual (idiosyncratic) momentum (Blitz-Huij-Martens 2011, PREDICTIVE_SIGNALS §2).

    Estimate the stock's CAPM beta over the trailing window by OLS of stock returns on
    market (SPY) returns — stdlib only, no numpy — over the DATE-INNER-JOINED series, take
    the residuals e_t = r_stock,t - (alpha + beta * r_mkt,t), then form the 12-1 momentum
    of the residuals: mean(residuals over t-12..t-2, i.e. EXCLUDING the most recent ~month)
    STANDARDIZED by the residual std-dev over that same window:

        iMOM = mean(resid_{t-12..t-2}) / sd(resid_{t-12..t-2})

    POINT-IN-TIME: pass series already truncated to as_of (dates <= as_of). The whole
    computation — beta, residuals, the 12-1 sum — uses only the supplied closes, so there
    is no look-ahead by construction; the caller truncates via PriceHistory.closes_through.

    The 12-1 SKIP is preserved: `skip_days` (~1 month) of the most recent residuals are
    dropped before the mean (raw momentum reverses in the latest month). The market excess
    is taken against itself (no risk-free leg — a daily T-bill rate is a negligible, non-
    keyless input; the alpha intercept absorbs the constant drift).

    GUARDS (never divide by ~0): returns None when the joined series is too short
    (< min_points returns), when the regression's market-return variance is ~0 (a flat
    market window -> beta undefined), when fewer than ~2 residuals survive the skip, or
    when the residual std-dev over the scoring window is 0 (a degenerate flat residual).
    Pure; no I/O."""
    _, sa, mb = join_on_dates(stock_dates, stock_closes, mkt_dates, mkt_closes)
    # Use the most recent `lookback_days` JOINED closes (returns need N+1 closes for N rets).
    sa = sa[-(lookback_days + 1):]
    mb = mb[-(lookback_days + 1):]
    rs = _simple_returns(sa)
    rm = _simple_returns(mb)
    n = min(len(rs), len(rm))
    if n < min_points:
        return None
    rs, rm = rs[:n], rm[:n]

    # OLS of rs on rm: beta = cov(rs, rm) / var(rm), alpha = mean(rs) - beta*mean(rm).
    mean_s = sum(rs) / n
    mean_m = sum(rm) / n
    var_m = sum((m - mean_m) ** 2 for m in rm) / n
    if var_m <= 0:                       # flat market window -> beta undefined
        return None
    cov = sum((s - mean_s) * (m - mean_m) for s, m in zip(rs, rm, strict=False)) / n
    beta = cov / var_m
    alpha = mean_s - beta * mean_m
    resid = [s - (alpha + beta * m) for s, m in zip(rs, rm, strict=False)]

    # 12-1 skip: drop the most recent ~month of residuals before forming the signal.
    scoring_window = resid[: len(resid) - skip_days] if skip_days > 0 else resid
    if len(scoring_window) < 2:
        return None
    sd = pstdev(scoring_window)
    if sd <= 0:                          # degenerate flat residual -> abstain (no /0)
        return None
    return (sum(scoring_window) / len(scoring_window)) / sd


def compute_ebit_ev_yield(ebit: Optional[float], market_cap: Optional[float],
                          net_debt: Optional[float]) -> Optional[float]:
    """EBIT/EV earnings yield (higher = cheaper). EV = market_cap + net_debt.
    Abstains (None) on EBIT <= 0 (unprofitable is growth/quality's job, not a
    valuation of negative earnings) and on EV <= 0 (net-cash-exceeds-market-cap
    artifact). UNITS: ebit/market_cap/net_debt all absolute USD."""
    if ebit is None or market_cap is None or net_debt is None or ebit <= 0:
        return None
    ev = market_cap + net_debt
    if ev <= 0:
        return None
    return ebit / ev
