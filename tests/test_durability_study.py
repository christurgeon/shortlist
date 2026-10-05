import pytest

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_study import (
    TESTS,
    Firm,
    avg_ranks,
    build_cohort,
    cross_section,
    pct_rank,
    quintile_floor,
)
from shortlist.durability import TAX, YearRow


def _yr(year, roic, *, revenue=1e9, ic=1000.0, assets=None, gp=None, op_income=None):
    oi = roic * ic / (1 - TAX) if op_income is None else op_income
    return YearRow(end=f"{year}-12-31", revenue=revenue, op_income=oi, equity=ic, debt=0.0,
                   assets=2 * abs(ic) if assets is None else assets, gross_profit=gp)


def _firm(i, snaps, *, sic="3571", masked=False, last=2030):
    return Firm(cik=f"{i:010d}", sic=sic, masked=masked, snaps=snaps, last_bucket=last)


def _flat(i, roic, years=(2015, 2018), **kw):
    """A firm with the same ROIC in every bucket of every snapshot."""
    return _firm(i, {s: {y: _yr(y, roic) for y in range(s - 3, s + 1)} for s in years}, **kw)


# ---------------------------------------------------------------- helpers

def test_quintile_floor_is_the_lowest_value_in_the_top_fifth():
    assert quintile_floor([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9
    assert quintile_floor([5, 4, 3, 2, 1]) == 5
    assert quintile_floor([1, 2, 3, 4]) is None


def test_pct_rank_counts_values_at_or_below():
    assert pct_rank(3, [1, 2, 3, 4]) == 0.75
    assert pct_rank(0, [1, 2, 3, 4]) == 0.0
    assert pct_rank(9, [1, 2, 3, 4]) == 1.0


def test_avg_ranks_share_ties_and_stay_inside_zero_one():
    r = avg_ranks({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0})
    assert r == {"a": 0.125, "b": 0.5, "c": 0.5, "d": 0.875}
    assert avg_ranks({"only": 7.0}) == {"only": 0.5}
    assert avg_ranks({}) == {}


def test_cross_section_applies_the_universe_rules():
    firms = [
        _flat(1, 0.20),
        _flat(2, 0.20, masked=True),                                    # financial: out
        _firm(3, {2015: {2015: _yr(2015, 0.20, revenue=9.9e7)}}),       # under $100M: out
        _firm(4, {2015: {2015: _yr(2015, 0.20, ic=10.0, assets=1000)}}),  # low_ic: out
        _firm(5, {2015: {2014: _yr(2014, 0.20)}}),                      # no row for the bucket
    ]
    assert list(cross_section(firms, 2015, 2015)) == ["0000000001"]


# ---------------------------------------------------------------- cohort + outcome states

def _world():
    """20 firms, ROIC 2%..40% in 2015. The top fifth is firms 16-19. Three years later:
    19 is observed and held, 18 has shrunk its capital under the floor while still profitable,
    17 files but has no usable ROIC, 16 stopped filing."""
    firms = [_flat(i, 0.02 * (i + 1)) for i in range(16)]
    firms.append(_firm(16, {2015: {y: _yr(y, 0.34) for y in range(2012, 2016)}}, last=2016))
    firms.append(_firm(17, {2015: {y: _yr(y, 0.36) for y in range(2012, 2016)}}, last=2019))
    firms.append(_firm(18, {2015: {y: _yr(y, 0.38) for y in range(2012, 2016)},
                            2018: {2018: _yr(2018, 0.0, revenue=1.2e9, ic=10.0, assets=1000.0,
                                             op_income=50.0)}}))
    firms.append(_firm(19, {2015: {y: _yr(y, 0.40) for y in range(2012, 2016)},
                            2018: {2018: _yr(2018, 0.40, revenue=1.5e9)}}))
    return firms


def test_cohort_is_the_top_quintile_with_one_state_each():
    rows = {int(r.cik): r for r in build_cohort(_world(), 2015)}
    assert sorted(rows) == [16, 17, 18, 19]
    assert [rows[i].state for i in (16, 17, 18, 19)] == ["exit", "gap", "low_ic", "observed"]
    assert [rows[i].held for i in (16, 17, 18, 19)] == [None, None, True, True]
    assert rows[19].rank_t3 == 1.0 and rows[18].rank_t3 is None


def test_low_ic_with_an_operating_loss_did_not_hold():
    firms = _world()
    firms[18].snaps[2018][2018] = _yr(2018, 0.0, ic=10.0, assets=1000.0, op_income=-5.0)
    rows = {int(r.cik): r for r in build_cohort(firms, 2015)}
    assert rows[18].state == "low_ic" and rows[18].held is False


def test_compounded_needs_held_and_above_median_revenue_growth():
    rows = {int(r.cik): r for r in build_cohort(_world(), 2015)}
    assert rows[19].rev_ratio == 1.5 and rows[18].rev_ratio == pytest.approx(1.2)
    assert rows[19].compounded is True          # held, 1.5 >= median(1.5, 1.2)
    assert rows[18].compounded is False         # held, but below the median
    assert rows[16].compounded is None and rows[17].compounded is None


def test_cohort_is_empty_when_a_cross_section_is_too_thin():
    assert build_cohort(_world()[:4], 2015) == []
    assert build_cohort(_world(), 2016) == []   # no 2016 snapshots at all


def test_ranks_are_within_the_cohort_year():
    rows = {int(r.cik): r for r in build_cohort(_world(), 2015)}
    assert [rows[i].c0 for i in (16, 17, 18, 19)] == [0.125, 0.375, 0.625, 0.875]


# ---------------------------------------------------------------- predictors

def _preds(firm_rows, *, sic="3571", extra_firms=()):
    """Predictors of one custom firm placed at the top of the 2015 cohort."""
    firms = [f for f in _world() if f.cik != "0000000019"] + list(extra_firms)
    firms.append(_firm(19, {2015: firm_rows, 2018: {2018: _yr(2018, 0.40)}}, sic=sic))
    return {int(r.cik): r for r in build_cohort(firms, 2015)}[19].preds


def test_track_and_stability_from_four_years_of_history():
    p = _preds({y: _yr(y, 0.40) for y in range(2012, 2016)})
    assert p["track"] == 1.0 and p["stability"] == 0.0
    mixed = _preds({2012: _yr(2012, 0.01), 2013: _yr(2013, 0.40), 2014: _yr(2014, 0.40),
                    2015: _yr(2015, 0.40)})
    assert mixed["track"] == 0.75 and mixed["stability"] < 0


def test_track_abstains_under_three_observed_years():
    p = _preds({2014: _yr(2014, 0.40), 2015: _yr(2015, 0.40)})
    assert p["track"] is None and p["stability"] is None
    three = _preds({y: _yr(y, 0.40) for y in range(2013, 2016)})
    assert three["track"] == 1.0


def test_investment_is_oriented_so_low_growth_is_favourable():
    grew = _preds({2014: _yr(2014, 0.40, ic=1000.0), 2015: _yr(2015, 0.40, ic=1500.0)})
    assert grew["investment"] == pytest.approx(-0.5)
    assert _preds({2015: _yr(2015, 0.40)})["investment"] is None


def test_incremental_roic_needs_capital_growth_over_five_percent():
    base, now = _yr(2012, 0.20, ic=1000.0), _yr(2015, 0.40, ic=2000.0)
    p = _preds({2012: base, 2015: now})
    assert p["incremental_roic"] == pytest.approx((now.nopat - base.nopat) / 1000.0)
    flat = _preds({2012: _yr(2012, 0.20, ic=1000.0), 2015: _yr(2015, 0.40, ic=1040.0)})
    assert flat["incremental_roic"] is None


def test_gross_margin_abstains_when_untagged():
    assert _preds({2015: _yr(2015, 0.40, gp=6e8)})["gross_margin"] == pytest.approx(0.6)
    assert _preds({2015: _yr(2015, 0.40)})["gross_margin"] is None


def test_share_stability_needs_five_peers_with_revenue_in_both_years():
    rows = {2012: _yr(2012, 0.40, revenue=1e9), 2015: _yr(2015, 0.40, revenue=3e9)}
    peers = [_firm(100 + k, {2015: {2012: _yr(2012, 0.01, revenue=1e9),
                                    2015: _yr(2015, 0.01, revenue=1e9)}}, sic="7372")
             for k in range(4)]
    # 5 peers: share goes 1/5 -> 3/7
    assert _preds(rows, sic="7372", extra_firms=peers)["share_stability"] == pytest.approx(
        -(3 / 7 - 1 / 5))
    assert _preds(rows, sic="7372", extra_firms=peers[:3])["share_stability"] is None
    assert _preds(rows, sic=None, extra_firms=peers)["share_stability"] is None


# ---------------------------------------------------------------- registered constants

def test_eleven_tests_and_investment_is_not_tested_on_compounded():
    assert len(TESTS) == 11
    assert ("investment", "held") in TESTS and ("investment", "compounded") not in TESTS


def test_windows_do_not_overlap_and_the_holdout_stops_before_the_censored_year():
    assert list(ds.DISCOVERY) == [2011, 2012, 2013, 2014, 2015, 2016, 2017]
    assert list(ds.HOLDOUT) == [2018, 2019, 2020, 2021]
