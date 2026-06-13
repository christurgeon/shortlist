"""Pure-function tests for the reverse-DCF implied-growth leaf."""
import math

from shortlist.research.reverse_dcf import implied_growth, format_line

CFG = {"enabled": True, "discount_rate": 0.10, "base_years": 3,
       "run_rate_flag_ratio": 1.5, "display_floor": -0.50}


def test_closed_form_g_equals_R_minus_fcf_yield():
    # F0=median([100,100,100])=100, P=2000 -> FCF yield 5% -> g = 0.10-0.05 = 0.05
    ig = implied_growth([100.0, 100.0, 100.0], 2000.0, CFG)
    assert ig is not None
    assert math.isclose(ig.rate, 0.05, abs_tol=1e-9)
    assert ig.n_positive_years == 3
    assert math.isclose(ig.base_fcf, 100.0)
    assert ig.distressed is False


def test_monotonic_higher_price_higher_implied_growth():
    lo = implied_growth([100.0, 100.0, 100.0], 1500.0, CFG).rate
    hi = implied_growth([100.0, 100.0, 100.0], 3000.0, CFG).rate
    assert hi > lo  # higher price embeds higher growth (toward R)


def test_high_fcf_yield_gives_negative_implied_growth():
    # P=500, F0=100 -> yield 20% -> g = 0.10-0.20 = -0.10 (price embeds decline)
    ig = implied_growth([100.0, 100.0, 100.0], 500.0, CFG)
    assert math.isclose(ig.rate, -0.10, abs_tol=1e-9)
    assert ig.distressed is False


def test_median_rejects_capex_spike():
    # newest-first [100, 100, 10] -> median 100 (the down-spike is rejected)
    ig = implied_growth([100.0, 100.0, 10.0], 2000.0, CFG)
    assert math.isclose(ig.base_fcf, 100.0)
    assert ig.n_positive_years == 3


def test_fewer_than_base_years_positive_reports_real_count():
    ig = implied_growth([-50.0, 0.0, 80.0], 2000.0, CFG)
    assert ig is not None
    assert ig.n_positive_years == 1
    assert math.isclose(ig.base_fcf, 80.0)
    line = format_line(ig)
    assert "1 positive FCF yr " in line          # singular, real count
    assert "positive FCF yrs" not in line          # not the plural


def test_run_rate_caveat_when_latest_far_above_base():
    # latest (newest-first first cell) 300 vs base median 100 -> >1.5x -> caveat
    ig = implied_growth([300.0, 100.0, 100.0, 100.0], 5000.0, CFG)
    assert ig.run_rate_understated is True
    assert "run-rate" in format_line(ig)


def test_no_run_rate_caveat_when_latest_near_base():
    ig = implied_growth([110.0, 100.0, 100.0], 2000.0, CFG)
    assert ig.run_rate_understated is False
    assert "run-rate" not in format_line(ig)


def test_abstain_no_positive_fcf():
    assert implied_growth([-1.0, -2.0, 0.0], 2000.0, CFG) is None


def test_abstain_missing_or_nonpositive_market_cap():
    assert implied_growth([100.0], None, CFG) is None
    assert implied_growth([100.0], 0.0, CFG) is None
    assert implied_growth([100.0], -5.0, CFG) is None


def test_nonfinite_fcf_cells_are_filtered_not_fatal():
    # a NaN cell is dropped; the remaining positive year drives the base
    ig = implied_growth([float("nan"), 100.0, 100.0], 2000.0, CFG)
    assert ig is not None and math.isclose(ig.base_fcf, 100.0)


def test_abstain_all_nonfinite():
    assert implied_growth([float("inf"), float("nan")], 2000.0, CFG) is None
    assert implied_growth([100.0], float("inf"), CFG) is None
    assert implied_growth([100.0], float("nan"), CFG) is None


def test_disabled_returns_none():
    assert implied_growth([100.0], 2000.0, {"enabled": False}) is None
    assert implied_growth([100.0], 2000.0, None) is None


def test_distressed_clamps_and_relabels():
    # P=10, F0=100 -> g = 0.10 - 10 = -9.9 < floor -> distressed label, no raw rate
    ig = implied_growth([100.0, 100.0, 100.0], 10.0, CFG)
    assert ig.distressed is True
    line = format_line(ig)
    assert "distressed" in line
    assert "-990" not in line  # absurd raw rate never shown
    assert "-50%/yr FCF DCF" in line


def test_format_line_always_points_to_cagr():
    ig = implied_growth([100.0, 100.0, 100.0], 2000.0, CFG)
    assert "Compare to revenue/FCF CAGR above." in format_line(ig)
