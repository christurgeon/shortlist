import pytest

from shortlist.stats import (
    avg_roic, cagr, compute_ebit_ev_yield, gross_margin_stability,
    growth_persistence, median_pe, net_debt_from, piotroski_f,
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
