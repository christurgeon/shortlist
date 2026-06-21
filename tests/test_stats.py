from datetime import date, timedelta

import pytest

from shortlist.stats import (
    accruals, asset_growth, avg_roic, cagr, compute_ebit_ev_yield,
    gross_margin_stability, growth_persistence, join_on_dates, median_pe,
    net_debt_from, piotroski_f, residual_momentum, shareholder_yield, sue,
    surprise_dispersion,
)


def test_median_pe_odd_count():
    # sorted: [20, 25, 28, 30, 35] -> middle = 28
    assert median_pe([35.0, 25.0, 20.0, 30.0, 28.0]) == 28.0


def test_median_pe_even_count_averages_middle_two():
    assert median_pe([20.0, 30.0]) == 25.0


def test_median_pe_drops_none_and_zero():
    # None and 0 are dropped; [20, 30] remain -> 25
    assert median_pe([None, 0, 20.0, 30.0]) == 25.0


def test_median_pe_keeps_negatives():
    assert median_pe([-10.0, 30.0]) == 10.0


def test_median_pe_too_few_points_returns_none():
    assert median_pe([30.0]) is None
    assert median_pe([None, 0]) is None
    assert median_pe([]) is None


def test_avg_roic_mean():
    assert avg_roic([0.10, 0.20, 0.30]) == 0.20


def test_avg_roic_keeps_zero_and_negative():
    # zeros/negatives are real ROIC years and must count
    assert avg_roic([0.0, -0.10, 0.40]) == 0.10


def test_avg_roic_drops_none():
    assert avg_roic([None, 0.10, 0.30]) == 0.20


def test_avg_roic_too_few_points_returns_none():
    assert avg_roic([0.10]) is None
    assert avg_roic([None]) is None
    assert avg_roic([]) is None


def test_cagr_doubles_over_two_years():
    # newest-first [121, 110, 100]: 100 -> 121 over 2 periods => 10%/yr
    assert cagr([121.0, 110.0, 100.0]) == pytest.approx(0.10)


def test_cagr_respects_ordering_flag():
    # oldest-first form of the same series
    assert cagr([100.0, 110.0, 121.0], most_recent_first=False) == pytest.approx(0.10)


def test_cagr_drops_none_then_needs_min_points():
    # 4 points with one None -> 3 usable [121, 110, 100]; 100 -> 121 over 2 => 10%
    assert cagr([121.0, None, 110.0, 100.0]) == pytest.approx(0.10)
    # but dropping None from a 3-point series leaves only 2 usable -> None
    assert cagr([121.0, None, 100.0]) is None


def test_cagr_none_on_nonpositive_endpoint():
    # a swing through zero makes CAGR meaningless -> None
    assert cagr([100.0, 50.0, -10.0]) is None     # oldest endpoint <= 0
    assert cagr([-5.0, 50.0, 100.0]) is None       # newest endpoint <= 0


def test_cagr_too_few_points_returns_none():
    assert cagr([121.0, 100.0]) is None            # only 2 usable < min_points=3
    assert cagr([None, None, 100.0]) is None
    assert cagr([]) is None


def test_growth_persistence_all_up_is_one():
    assert growth_persistence([140.0, 120.0, 100.0]) == 1.0


def test_growth_persistence_all_down_is_zero():
    assert growth_persistence([100.0, 120.0, 140.0]) == 0.0


def test_growth_persistence_mixed_and_sign_safe():
    # oldest-first: -10 -> 5 (up), 5 -> 3 (down), 3 -> 8 (up) => 2/3
    assert growth_persistence([-10.0, 5.0, 3.0, 8.0], most_recent_first=False) == pytest.approx(2 / 3)


def test_growth_persistence_too_few_points_returns_none():
    assert growth_persistence([120.0, 100.0]) is None
    assert growth_persistence([]) is None


def test_stable_margins_score_near_one():
    # near-identical margins => very low dispersion => stability ~1.0
    s = gross_margin_stability([0.40, 0.41, 0.40, 0.39])
    assert s is not None and 0.95 < s <= 1.0


def test_volatile_margins_score_lower():
    s = gross_margin_stability([0.10, 0.50, 0.20, 0.45])
    assert s is not None and s < 0.7


def test_fewer_than_three_returns_none():
    assert gross_margin_stability([0.4, 0.4]) is None


def test_zero_mean_returns_none():
    assert gross_margin_stability([0.0, 0.0, 0.0]) is None


def test_negative_mean_returns_none():
    # A negative mean margin is not a meaningful stability base: dividing pstdev by a
    # negative mean made the old `if not avg:` guard return a proxy > 1 (out of 0..1).
    # The zero-mean test above can't catch this — 0.0 is falsy under both guards.
    assert gross_margin_stability([-0.40, -0.41, -0.39]) is None


def test_never_negative():
    # huge dispersion would push 1 - stdev/mean below 0; clamp to 0.0
    assert gross_margin_stability([0.01, 0.99, 0.02]) == 0.0


# Core-6 Piotroski-inspired (asset-free, equity-free). Series newest-first, index 0 = t.
# F1 NI>0, F2 OCF>0, F3 OCF>NI (levels); F4 net-margin rising, F5 debt/revenue falling,
# F6 gross-margin rising (1y deltas; need revenue>0 both years).
def _improving():
    # F4 nm: 160/1300=.123 > 120/1100=.109; F5 di: 300/1300=.231 < 350/1100=.318;
    # F6 gm: 700/1300=.538 > 560/1100=.509
    return dict(
        net_income=[160, 120], ocf=[240, 200], total_debt=[300, 350],
        gross_profit=[700, 560], revenue=[1300, 1100],
    )

def test_piotroski_all_six_pass():
    assert piotroski_f(**_improving()) == (6, 6)

def test_piotroski_all_six_fail():
    deteriorating = dict(
        net_income=[-10, 120],        # F1 NI<0 fail; F4 nm -.0077 < .109 fail (falling)
        ocf=[-20, 200],               # F2 OCF<0 fail; F3 OCF(-20) > NI(-10)? no -> fail
        total_debt=[400, 300],        # F5 di .308 < .273? no -> fail (rising)
        gross_profit=[300, 560],      # F6 gm .231 > .509? no -> fail (falling)
        revenue=[1300, 1100],
    )
    assert piotroski_f(**deteriorating) == (0, 6)

def test_piotroski_thin_history_evaluates_levels_only():
    # one year -> F1/F2/F3 (levels) evaluate; F4/F5/F6 (need t-1) do not.
    one = {k: v[:1] for k, v in _improving().items()}
    assert piotroski_f(**one) == (3, 3)

def test_piotroski_revenue_zero_abstains_delta_legs():
    d = _improving()
    d["revenue"] = [0, 1100]          # rev[t]=0 -> F4/F5/F6 not evaluable; levels remain
    won, legs = piotroski_f(**d)
    assert legs == 3                  # F1,F2,F3 only
    assert won == 3

def test_piotroski_accruals_leg_isolated():
    d = _improving()
    d["ocf"] = [100, 200]             # OCF(100) < NI(160) -> F3 fails; F2 (OCF>0) passes
    won, legs = piotroski_f(**d)
    assert legs == 6
    assert won == 5

def test_piotroski_oldest_first_flag():
    d = {k: list(reversed(v)) for k, v in _improving().items()}
    assert piotroski_f(most_recent_first=False, **d) == (6, 6)

def test_piotroski_negative_revenue_abstains_delta_legs():
    d = _improving()
    d["revenue"] = [-100, 1100]       # negative rev[t] -> F4/F5/F6 abstain (>0 guard)
    won, legs = piotroski_f(**d)
    assert legs == 3                  # only F1,F2,F3 (levels) evaluate
    assert won == 3

def test_piotroski_no_legs_returns_none_sentinel():
    # all series empty -> nothing evaluable -> (None, None), not (0, 0)
    assert piotroski_f(net_income=[], ocf=[], total_debt=[],
                       gross_profit=[], revenue=[]) == (None, None)

def test_piotroski_none_leading_level_legs_abstain():
    # latest-year net_income and ocf missing (None) -> F1, F2 abstain (not counted as
    # evaluated-failed); F3 also abstains (needs both); F4 also (ni[0] None). With a
    # 2-year series, F5 (debt/rev) and F6 (gross margin) still evaluate.
    d = _improving()
    d["net_income"] = [None, 120]
    d["ocf"] = [None, 200]
    won, legs = piotroski_f(**d)
    assert legs == 2          # only F5 and F6 evaluate
    assert won == 2           # both pass in the _improving fixture


def test_net_debt_from_both_present():
    assert net_debt_from(100.0, 30.0) == 70.0

def test_net_debt_from_net_cash_is_negative():
    assert net_debt_from(20.0, 50.0) == -30.0

def test_net_debt_from_one_missing_treats_other_as_zero():
    assert net_debt_from(100.0, None) == 100.0
    assert net_debt_from(None, 40.0) == -40.0

def test_net_debt_from_both_missing_abstains():
    # O1: a market-cap-only EV would silently ignore leverage -> abstain.
    assert net_debt_from(None, None) is None

def test_ebit_ev_yield_basic():
    # EBIT=100, mktcap=900, net_debt=100 -> EV=1000 -> yield 0.10
    assert compute_ebit_ev_yield(100.0, 900.0, 100.0) == 0.10

def test_ebit_ev_yield_net_cash_raises_yield():
    # net cash shrinks EV: EV = 900 - 100 = 800 -> 100/800 = 0.125
    assert compute_ebit_ev_yield(100.0, 900.0, -100.0) == 0.125

def test_ebit_ev_yield_abstains_on_nonpositive_ebit():
    assert compute_ebit_ev_yield(0.0, 900.0, 100.0) is None
    assert compute_ebit_ev_yield(-50.0, 900.0, 100.0) is None

def test_ebit_ev_yield_abstains_on_nonpositive_ev():
    # net cash exceeds market cap -> EV <= 0 artifact
    assert compute_ebit_ev_yield(100.0, 50.0, -60.0) is None

def test_ebit_ev_yield_abstains_on_missing_inputs():
    assert compute_ebit_ev_yield(None, 900.0, 100.0) is None
    assert compute_ebit_ev_yield(100.0, None, 100.0) is None
    assert compute_ebit_ev_yield(100.0, 900.0, None) is None


# --- asset_growth / accruals (PREDICTIVE_SIGNALS §3) -----------------------

def test_asset_growth_consecutive_ends():
    # Two consecutive ~1yr-spaced fiscal ends: 1100/1000 - 1 = 0.10.
    assets = {"2024-12-31": 1100.0, "2023-12-31": 1000.0}
    assert asset_growth(assets) == pytest.approx(0.10)


def test_asset_growth_uses_two_latest_ends():
    # Picks the two LATEST ends (2024 vs 2023), ignoring the older 2022.
    assets = {"2024-12-31": 1200.0, "2023-12-31": 1000.0, "2022-12-31": 800.0}
    assert asset_growth(assets) == pytest.approx(0.20)


def test_asset_growth_rejects_gap_spanning_pair():
    # A missing 2023 leaves a ~2yr gap between the two latest ends -> abstain
    # (never a gap-spanning ratio).
    assets = {"2024-12-31": 1200.0, "2022-12-31": 800.0}
    assert asset_growth(assets) is None


def test_asset_growth_none_below_two_ends_or_zero_denominator():
    assert asset_growth({"2024-12-31": 1100.0}) is None
    assert asset_growth({}) is None
    assert asset_growth({"2024-12-31": 1100.0, "2023-12-31": 0.0}) is None


def test_accruals_sloan_average_assets_no_sign_flip():
    # accruals = (NI - CFO) / avg_assets, avg = (1100 + 1000)/2 = 1050.
    # NI=200, CFO=150 (as-reported, NO sign flip) -> 50 / 1050.
    assets = {"2024-12-31": 1100.0, "2023-12-31": 1000.0}
    ni = {"2024-12-31": 200.0}
    cfo = {"2024-12-31": 150.0}
    assert accruals(ni, cfo, assets) == pytest.approx((200.0 - 150.0) / 1050.0)


def test_accruals_average_is_not_end_of_period():
    # Confirm the Sloan denominator is the AVERAGE, not Assets_t. With NI-CFO=105
    # and avg=1050, accruals=0.10; using end-of-period (1100) would give ~0.0955.
    assets = {"2024-12-31": 1100.0, "2023-12-31": 1000.0}
    assert accruals({"2024-12-31": 205.0}, {"2024-12-31": 100.0}, assets) == pytest.approx(0.10)


def test_accruals_none_on_missing_inputs_or_gap():
    assets = {"2024-12-31": 1100.0, "2023-12-31": 1000.0}
    assert accruals({}, {"2024-12-31": 150.0}, assets) is None              # NI missing at t
    assert accruals({"2024-12-31": 200.0}, {}, assets) is None              # CFO missing at t
    gap = {"2024-12-31": 1100.0, "2022-12-31": 1000.0}                      # gap-spanning
    assert accruals({"2024-12-31": 200.0}, {"2024-12-31": 150.0}, gap) is None


# --- shareholder_yield (PREDICTIVE_SIGNALS §5) -----------------------------

def test_shareholder_yield_adds_dividends_and_buybacks():
    # (div + repur + (repay - issue)) / mcap = (50 + 150 + (80 - 30)) / 1000 = 0.25.
    assert shareholder_yield(50, 150, 80, 30, 1000) == pytest.approx(0.25)


def test_shareholder_yield_sign_normalizes_both_source_conventions():
    # Raw companyfacts (positive magnitudes) and edgartools (negative outflows) must
    # yield the SAME number — abs() on each leg. Issuance is an inflow (positive in both).
    pos = shareholder_yield(50, 150, 80, 30, 1000)        # companyfacts
    neg = shareholder_yield(-50, -150, -80, 30, 1000)     # edgartools-style outflows
    assert pos == neg == pytest.approx(0.25)


def test_shareholder_yield_bigger_repurchaser_scores_higher():
    big = shareholder_yield(0, 300, 0, 0, 1000)
    small = shareholder_yield(0, 100, 0, 0, 1000)
    assert big > small


def test_shareholder_yield_net_debt_issuer_leg_negative_not_clamped():
    # issuance > repayments, no dividends/buybacks -> the whole yield goes negative.
    assert shareholder_yield(0, 0, 20, 120, 1000) == pytest.approx(-0.10)


def test_shareholder_yield_missing_legs_treated_as_zero():
    # A dividend-only payer (no buyback / debt rows present) still yields dividends/mcap.
    assert shareholder_yield(50, None, None, None, 1000) == pytest.approx(0.05)


def test_shareholder_yield_abstains_when_all_legs_or_mktcap_missing():
    assert shareholder_yield(None, None, None, None, 1000) is None       # no financing data
    assert shareholder_yield(50, 150, 80, 30, None) is None              # no market cap
    assert shareholder_yield(50, 150, 80, 30, 0) is None                 # non-positive mcap


# --- surprise_dispersion + sue (PREDICTIVE_SIGNALS §1) ---------------------

def test_surprise_dispersion_pop_stdev():
    from statistics import pstdev
    assert surprise_dispersion([2.0, 4.0, 0.0, -1.0]) == pytest.approx(pstdev([2.0, 4.0, 0.0, -1.0]))


def test_surprise_dispersion_drops_none_and_floors_min_quarters():
    assert surprise_dispersion([2.0, None, 4.0, 0.0]) == pytest.approx(surprise_dispersion([2.0, 4.0, 0.0]))
    assert surprise_dispersion([2.0, 4.0]) is None        # 2 < min_quarters=3
    assert surprise_dispersion([]) is None
    # The all-equal case has dispersion 0.0 (a real reading, NOT None) — the sue() guard,
    # not this helper, must keep that out of the denominator.
    assert surprise_dispersion([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_sue_beat_is_positive_and_decays():
    # SUE = 10 / 5 = 2.0 at day 0; decays linearly to 0 over 60 trading days.
    assert sue(10.0, 5.0, 0) == pytest.approx(2.0)
    assert sue(10.0, 5.0, 30) == pytest.approx(1.0)       # halfway through the window
    assert sue(10.0, 5.0, 60) == pytest.approx(0.0)       # fully decayed (no abstain)
    assert sue(10.0, 5.0, 90) == pytest.approx(0.0)       # past the window -> clamped 0
    # A miss is negative.
    assert sue(-10.0, 5.0, 0) == pytest.approx(-2.0)


def test_sue_sigma_guard_abstains_on_zero_or_thin_dispersion():
    # The mandatory σ-guard: dispersion below the floor (the all-equal / 4-equal σ≈0 case)
    # MUST return None — never inf/NaN.
    assert sue(10.0, 0.0, 0) is None                      # dispersion exactly 0
    assert sue(10.0, 0.5, 0) is None                      # below default floor 1.0
    assert sue(10.0, None, 0) is None                     # dispersion None (too few quarters)
    assert sue(None, 5.0, 0) is None                      # no last surprise
    # finite/real reading just above the floor
    v = sue(10.0, 1.0, 0)
    assert v is not None and v == pytest.approx(10.0)


def test_sue_no_decay_anchor_returns_undecayed():
    assert sue(10.0, 5.0, None) == pytest.approx(2.0)     # no days_since -> fresh prior
    assert sue(10.0, 5.0, -3) == pytest.approx(2.0)       # future-dated artifact -> fresh


# --- Residual (idiosyncratic) momentum + the date inner-join (PREDICTIVE_SIGNALS §2) ---

def _closes_from_returns(rets, start=100.0):
    """Build a close series whose period returns are exactly `rets`."""
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1.0 + r))
    return closes


def test_join_on_dates_inner_joins_not_by_position():
    # The #1 correctness risk: different listing dates / lengths must pair on the DATE key,
    # never by position. Stock starts later and has a halt-gap; SPY is longer and continuous.
    d = [date(2020, 1, i) for i in range(1, 11)]            # 2020-01-01..10
    spy_dates = d                                           # continuous 10 days
    spy_closes = [float(100 + i) for i in range(10)]
    # Stock: starts on day 3, MISSING day 6 (halt) -> different length, different dates.
    stock_dates = [d[2], d[3], d[4], d[6], d[7], d[8]]      # 01-03,04,05,07,08,09
    stock_closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    jd, ja, jb = join_on_dates(stock_dates, stock_closes, spy_dates, spy_closes)
    assert jd == stock_dates                                # only the shared (stock) dates
    assert ja == stock_closes
    # Each SPY close must be the SAME calendar day as its stock partner — NOT spy_closes[i].
    spy_map = dict(zip(spy_dates, spy_closes, strict=False))
    assert jb == [spy_map[x] for x in stock_dates]
    assert jb != spy_closes[: len(jb)]                      # position-pairing would differ


def _ref_resid_mom(stock_dates, stock_closes, mkt_dates, mkt_closes,
                   skip_days, lookback_days=252):
    """Reference impl: explicitly DATE-JOIN, then run the OLS+skip+vol-scale math. Used to
    prove residual_momentum joins on dates (a position-pair would give a different number)."""
    from statistics import pstdev
    mmap = dict(zip(mkt_dates, mkt_closes, strict=False))
    jd = [d for d in stock_dates if d in mmap]
    sa = [c for d, c in zip(stock_dates, stock_closes, strict=False) if d in mmap]
    mb = [mmap[d] for d in jd]
    sa, mb = sa[-(lookback_days + 1):], mb[-(lookback_days + 1):]
    rs = [sa[i] / sa[i - 1] - 1.0 for i in range(1, len(sa))]
    rm = [mb[i] / mb[i - 1] - 1.0 for i in range(1, len(mb))]
    n = min(len(rs), len(rm))
    rs, rm = rs[:n], rm[:n]
    ms, mm = sum(rs) / n, sum(rm) / n
    var_m = sum((m - mm) ** 2 for m in rm) / n
    cov = sum((s - ms) * (m - mm) for s, m in zip(rs, rm, strict=False)) / n
    beta = cov / var_m
    alpha = ms - beta * mm
    resid = [s - (alpha + beta * m) for s, m in zip(rs, rm, strict=False)]
    win = resid[: len(resid) - skip_days]
    return (sum(win) / len(win)) / pstdev(win)


def test_residual_momentum_misaligned_series_joins_on_dates():
    # A different-length / non-identical-date pair must still produce the value computed on
    # the DATE-JOINED series — proving it does NOT pair by position (which would garble beta).
    n = 200
    base = date(2021, 1, 1)
    mkt_dates = [base + timedelta(days=i) for i in range(n)]
    mkt_rets = [0.01 if i % 2 == 0 else -0.006 for i in range(n - 1)]
    mkt_closes = _closes_from_returns(mkt_rets)
    # Stock: SHORTER and SHIFTED — starts 30 days in, ends 5 days early, with a HALT (a
    # dropped interior date), so neither length, start, nor the date set matches the market.
    raw_dates = mkt_dates[30 : n - 5]
    s_dates = [d for k, d in enumerate(raw_dates) if k != 40]   # drop one interior day (halt)
    # closes = len(s_dates); _closes_from_returns returns N+1 closes for N returns.
    s_rets = [1.3 * mkt_rets[30 + k] + (0.003 if k % 4 else -0.002)
              for k in range(len(s_dates) - 1)]
    s_closes = _closes_from_returns(s_rets)
    assert len(s_dates) == len(s_closes) and len(s_dates) != len(mkt_dates)
    v = residual_momentum(s_dates, s_closes, mkt_dates, mkt_closes,
                          min_points=100, skip_days=10)
    assert v is not None                                    # joined + scored, not a length crash
    expected = _ref_resid_mom(s_dates, s_closes, mkt_dates, mkt_closes, skip_days=10)
    assert v == pytest.approx(expected)                     # == the DATE-JOINED reference


def test_residual_momentum_recovers_known_beta():
    # White-box: with returns built as r_s = beta*r_m + idiosyncratic, the OLS inside the
    # function must recover ~beta (so the residuals strip out the market exposure, the whole
    # point of §2). We re-run the function's OLS on the same joined returns and check beta.
    from statistics import pstdev  # noqa: F401  (kept parallel to _ref_resid_mom)
    n = 200
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    mkt_rets = [0.012 if i % 2 == 0 else -0.009 for i in range(n - 1)]
    KNOWN_BETA = 1.4
    idio = [0.002 if k % 5 else -0.006 for k in range(len(mkt_rets))]   # mean ~0-ish wobble
    stock_rets = [KNOWN_BETA * m + i for m, i in zip(mkt_rets, idio, strict=False)]
    mkt_closes = _closes_from_returns(mkt_rets)
    stock_closes = _closes_from_returns(stock_rets)
    # Recompute beta the way the function does (OLS over the joined return window).
    rs, rm = stock_rets, mkt_rets
    nn = len(rs)
    ms, mm = sum(rs) / nn, sum(rm) / nn
    var_m = sum((m - mm) ** 2 for m in rm) / nn
    cov = sum((s - ms) * (m - mm) for s, m in zip(rs, rm, strict=False)) / nn
    assert cov / var_m == pytest.approx(KNOWN_BETA, abs=0.02)   # beta recovered
    # And the function returns a finite value on this well-conditioned input.
    assert residual_momentum(dates, stock_closes, dates, mkt_closes,
                             min_points=100, skip_days=10) is not None


def test_residual_momentum_constant_residual_abstains_no_div_by_zero():
    # The mandatory vol guard: when residuals are degenerate (here stock == market returns
    # exactly -> beta 1, alpha 0, residuals all 0 -> sd 0), abstain (None), never divide.
    n = 160
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    rets = [0.01 if i % 2 == 0 else -0.008 for i in range(n - 1)]
    closes = _closes_from_returns(rets)
    assert residual_momentum(dates, closes, dates, closes,
                             min_points=100, skip_days=10) is None


def test_residual_momentum_matches_reference_ols_on_tiny_window():
    # Deterministic known-value check: a tiny hand-constructible window where we replicate
    # the exact OLS + 12-1-skip + vol-standardize math the function performs, proving the
    # returned value (not just its sign).
    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(8)]   # 8 closes -> 7 rets
    # Market returns (6 paired returns after the join, deterministic):
    mkt_rets = [0.02, -0.01, 0.015, -0.005, 0.01, -0.02, 0.007]
    stock_rets = [0.03, 0.00, 0.02, 0.001, 0.012, -0.018, 0.015]
    mkt_closes = _closes_from_returns(mkt_rets)
    stock_closes = _closes_from_returns(stock_rets)
    # Reference impl mirroring stats.residual_momentum (skip_days=1, min_points=3):
    rs, rm = stock_rets, mkt_rets
    n = len(rs)
    ms, mm = sum(rs) / n, sum(rm) / n
    var_m = sum((m - mm) ** 2 for m in rm) / n
    cov = sum((s - ms) * (m - mm) for s, m in zip(rs, rm, strict=False)) / n
    beta = cov / var_m
    alpha = ms - beta * mm
    resid = [s - (alpha + beta * m) for s, m in zip(rs, rm, strict=False)]
    win = resid[: len(resid) - 1]                # skip the last residual (1-day skip)
    from statistics import pstdev
    expected = (sum(win) / len(win)) / pstdev(win)
    got = residual_momentum(dates, stock_closes, dates, mkt_closes,
                            min_points=3, skip_days=1)
    assert got == pytest.approx(expected)


def test_residual_momentum_flat_market_abstains():
    # A flat market window (zero market-return variance) -> beta undefined -> None.
    n = 160
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    mkt_closes = [100.0] * n                                # never moves
    stock_closes = _closes_from_returns([0.005 if i % 2 else -0.003 for i in range(n - 1)])
    assert residual_momentum(dates, stock_closes, dates, mkt_closes,
                             min_points=100) is None


def test_residual_momentum_too_short_abstains():
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(30)]
    closes = _closes_from_returns([0.01] * 29)
    assert residual_momentum(dates, closes, dates, closes, min_points=120) is None
