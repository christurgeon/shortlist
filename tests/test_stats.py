import pytest

from shortlist.stats import (
    avg_roic, cagr, gross_margin_stability, growth_persistence, median_pe,
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


def test_never_negative():
    # huge dispersion would push 1 - stdev/mean below 0; clamp to 0.0
    assert gross_margin_stability([0.01, 0.99, 0.02]) == 0.0
