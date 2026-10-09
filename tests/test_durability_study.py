import math
import random

import pytest

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_study import (
    TESTS,
    Firm,
    Row,
    avg_ranks,
    bootstrap,
    build_cohort,
    build_firm,
    comparison_count,
    comparison_panel,
    cross_section,
    discovery_rules,
    exit_rate_by_tercile,
    no_revenue_count,
    fit,
    gap_spikes,
    holdout_rule,
    level_slope,
    measure,
    passes,
    pct_rank,
    quintile_floor,
    raw_tercile_spread,
    reproduction_failures,
    sample,
    share_alone_in_cell,
    state_rate_by_tercile,
    state_shares,
    zero_debt_share,
)
from shortlist.durability import TAX, YearRow


def _yr(year, roic, *, revenue=1e9, ic=1000.0, assets=None, gp=None, op_income=None, debt=0.0):
    oi = roic * ic / (1 - TAX) if op_income is None else op_income
    return YearRow(end=f"{year}-12-31", revenue=revenue, op_income=oi, equity=ic - debt, debt=debt,
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


def _facts(filed="2016-02-20", oi=100.0, revenue=2e9):
    def fact(val, **kw):
        return {"end": "2015-12-31", "val": val, "filed": filed, "form": "10-K", **kw}
    return {"facts": {"us-gaap": {t: {"units": {"USD": [row]}} for t, row in {
        "Revenues": fact(revenue, start="2015-01-01"),
        "OperatingIncomeLoss": fact(oi, start="2015-01-01"),
        "StockholdersEquity": fact(400.0), "Assets": fact(1000.0)}.items()}}}


def test_the_sector_mask_can_be_left_off_the_universe():
    firms = [_firm(1, {2015: {2015: _yr(2015, 0.20)}}),
             _firm(2, {2015: {2015: _yr(2015, 0.20)}}, masked=True)]
    assert [int(c) for c in cross_section(firms, 2015, 2015)] == [1]
    assert [int(c) for c in cross_section(firms, 2015, 2015, masked=False)] == [1, 2]


def test_the_comparison_panel_reads_latest_values_from_10k_and_foreign_annual_forms():
    restated = _facts()
    restated["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"].append(
        {"start": "2015-01-01", "end": "2015-12-31", "val": 55.0, "filed": "2017-02-20",
         "form": "10-K"})
    assert comparison_panel(restated)[2015].op_income == 55.0              # not as first reported
    late = _facts(filed="2016-09-01")                                       # after the 120 days
    assert build_firm("1", "3571", False, late, [2015]).snaps[2015] == {}
    assert comparison_panel(late)[2015].status == "ok"
    # A filer on form 20-F: no study facts at all, and still in the count.
    empty = {"facts": {"us-gaap": {}}}
    assert comparison_panel({**empty, "foreign_forms": _facts()})[2015].op_income == 100.0
    assert comparison_panel(empty) == {}
    # Both: the two sets of rows are read as one, and the later filing wins.
    both = {**_facts(), "foreign_forms": _facts(filed="2018-03-01", oi=70.0)}
    assert comparison_panel(both)[2015].op_income == 70.0


def test_the_comparison_count_applies_the_universe_floors_to_the_panels():
    panels = [{2015: _yr(2015, 0.20)}, {2015: _yr(2015, 0.20, revenue=9e7)},
              {2015: _yr(2015, 0.20, ic=10.0, assets=1000.0)}, {2014: _yr(2014, 0.20)}, {}]
    assert comparison_count(panels, 2015) == 1 and comparison_count(panels, 2013) == 0


def test_build_firm_keeps_one_point_in_time_snapshot_per_year():
    facts = _facts()
    facts["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"].append(
        {"start": "2015-01-01", "end": "2015-12-31", "val": 55.0, "filed": "2017-02-20",
         "form": "10-K"})
    f = build_firm("0000000001", "3571", False, facts, range(2014, 2017))
    assert sorted(f.snaps) == [2015] and f.last_bucket == 2015
    assert f.snaps[2015][2015].op_income == 100.0          # as first reported
    assert build_firm("0000000002", None, False, {"facts": {}}, range(2014, 2017)) is None


def test_no_revenue_count_is_the_firms_with_a_roic_and_no_revenue_tag():
    # The universe needs revenue for its floor, so these firms are outside it for a tag.
    firms = [_firm(1, {2015: {2015: _yr(2015, 0.20)}}),
             _firm(2, {2015: {2015: _yr(2015, 0.20, revenue=None)}}),
             _firm(3, {2015: {2015: _yr(2015, 0.20, revenue=None)}}, masked=True),
             _firm(4, {2015: {2015: _yr(2015, 0.20, revenue=None, ic=10.0, assets=1000.0)}}),
             _firm(5, {2015: {2015: _yr(2015, 0.20, revenue=9e7)}})]
    assert no_revenue_count(firms, 2015) == 1 and no_revenue_count(firms, 2014) == 0
    # `_yr` gives assets of 2,000: only a firm that large can be compared with the frames count
    assert no_revenue_count(firms, 2015, min_assets=2000.0) == 1
    assert no_revenue_count(firms, 2015, min_assets=2001.0) == 0


def test_zero_debt_share_is_over_the_universe_or_a_named_part_of_it():
    firms = [_firm(1, {2015: {2015: _yr(2015, 0.20)}}),
             _firm(2, {2015: {2015: _yr(2015, 0.20, debt=300.0)}}),
             _firm(3, {2015: {2015: _yr(2015, 0.20)}}),
             _firm(4, {2015: {2015: _yr(2015, 0.20)}}, masked=True)]     # not in the universe
    assert zero_debt_share(firms, 2015) == pytest.approx(2 / 3)
    assert zero_debt_share(firms, 2015, {"0000000002", "0000000003"}) == 0.5
    assert zero_debt_share(firms, 2014) is None


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


def test_cohort_rows_carry_the_sic2_and_sic3_sector():
    firms = _world()
    firms[19].sic = None
    rows = {int(r.cik): r for r in build_cohort(firms, 2015)}
    assert (rows[18].sic2, rows[18].sic3) == ("35", "357")
    assert (rows[19].sic2, rows[19].sic3) == (ds.NO_SIC, ds.NO_SIC)


def test_low_ic_with_an_operating_loss_did_not_hold():
    firms = _world()
    firms[18].snaps[2018][2018] = _yr(2018, 0.0, ic=10.0, assets=1000.0, op_income=-5.0)
    rows = {int(r.cik): r for r in build_cohort(firms, 2015)}
    assert rows[18].state == "low_ic" and rows[18].held is False


def test_observed_under_the_later_floor_did_not_hold():
    firms = _world()
    firms[19].snaps[2018][2018] = _yr(2018, 0.10, revenue=1.5e9)     # the 2018 floor is 0.28
    row = {int(r.cik): r for r in build_cohort(firms, 2015)}[19]
    assert row.state == "observed" and row.held is False and row.rank_t3 < 0.5


def test_held_is_judged_against_the_floor_of_the_outcome_year_not_the_start_year():
    # The 2015 floor is 0.34 and the 2018 floor is 0.30. A firm at 0.31 in 2018 held.
    firms = _world()
    firms[19].snaps[2018][2018] = _yr(2018, 0.31)
    row = {int(r.cik): r for r in build_cohort(firms, 2015)}[19]
    assert row.held is True and row.rank_t3 == pytest.approx(16 / 17)


def test_a_last_year_end_in_the_outcome_bucket_is_a_gap_not_an_exit():
    firms = _world()
    firms[16].last_bucket = 2018
    assert {int(r.cik): r for r in build_cohort(firms, 2015)}[16].state == "gap"


def test_compounded_needs_held_and_above_median_revenue_growth():
    rows = {int(r.cik): r for r in build_cohort(_world(), 2015)}
    assert rows[19].rev_ratio == 1.5 and rows[18].rev_ratio == pytest.approx(1.2)
    assert rows[19].compounded is True          # held, 1.5 >= median(1.5, 1.2)
    assert rows[18].compounded is False         # held, but below the median
    assert rows[16].compounded is None and rows[17].compounded is None


def test_compounded_is_false_for_a_firm_that_did_not_hold_however_fast_it_grew():
    firms = _world()
    firms[19].snaps[2018][2018] = _yr(2018, 0.10, revenue=1.5e9)     # fastest growth, not held
    row = {int(r.cik): r for r in build_cohort(firms, 2015)}[19]
    assert row.rev_ratio == 1.5 and row.compounded is False


def test_a_row_with_no_revenue_ratio_is_out_of_outcome_b_whether_it_held_or_not():
    # Dropping only the held ones would select the outcome-B sample on outcome A.
    firms = _world()
    firms[19].snaps[2018][2018] = _yr(2018, 0.10, revenue=None)      # did not hold, no revenue
    row = {int(r.cik): r for r in build_cohort(firms, 2015)}[19]
    assert row.held is False and row.compounded is None
    firms[19].snaps[2018][2018] = _yr(2018, 0.40, revenue=None)      # held, no revenue
    row = {int(r.cik): r for r in build_cohort(firms, 2015)}[19]
    assert row.held is True and row.compounded is None


def test_the_outcome_b_median_is_over_rows_with_a_determined_held_and_a_ratio():
    firms = _world()
    firms[16].last_bucket = 2030
    firms[16].snaps[2018] = {2018: _yr(2018, 0.05, revenue=0.5e9)}   # not held, ratio 0.5
    firms[17].snaps[2018] = {2018: YearRow(end="2018-12-31", revenue=3e9, op_income=None,
                                           equity=None, debt=0.0, assets=None, gross_profit=None)}
    rows = {int(r.cik): r for r in build_cohort(firms, 2015)}
    assert (rows[17].state, rows[17].rev_ratio, rows[17].compounded) == ("gap", 3.0, None)
    # The median of 0.5, 1.2 and 1.5 is 1.2. With the gap row's 3.0 in it, or with the row that
    # did not hold left out, it is 1.35 and firm 18 falls under it.
    assert [rows[i].compounded for i in (16, 18, 19)] == [False, True, True]


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


def test_a_history_year_under_the_revenue_floor_still_counts_for_track():
    p = _preds({**{y: _yr(y, 0.40, revenue=5e7) for y in range(2012, 2015)}, 2015: _yr(2015, 0.40)})
    assert p["track"] == 1.0 and p["stability"] == 0.0


def test_track_abstains_under_three_observed_years():
    p = _preds({2014: _yr(2014, 0.40), 2015: _yr(2015, 0.40)})
    assert p["track"] is None and p["stability"] is None
    three = _preds({y: _yr(y, 0.40) for y in range(2013, 2016)})
    assert three["track"] == 1.0


def test_track_and_stability_are_undefined_for_start_year_2011():
    # In 2011 the history buckets reach 2008, before XBRL for all but the largest filers.
    def top(year):
        firms = [_flat(i, 0.02 * (i + 1), years=(year, year + 3)) for i in range(19)]
        firms.append(_firm(19, {year: {y: _yr(y, 0.40, ic=1000.0 + y) for y in range(year - 3, year + 1)},
                                year + 3: {year + 3: _yr(year + 3, 0.40)}}))
        return {int(r.cik): r for r in build_cohort(firms, year)}[19].preds
    assert (top(2012)["track"], top(2012)["stability"]) == (1.0, 0.0)
    early = top(2011)
    assert early["track"] is None and early["stability"] is None
    assert early["investment"] is not None                 # the other predictors are untouched


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
    # 5 peers: share goes 1/5 -> 3/7. The change is RELATIVE (a log ratio): an absolute change
    # grows with the share itself and ranks firms by their size inside the industry.
    assert _preds(rows, sic="7372", extra_firms=peers)["share_stability"] == pytest.approx(
        -math.log((3 / 7) / (1 / 5)))
    lost = {2012: _yr(2012, 0.40, revenue=3e9), 2015: _yr(2015, 0.40, revenue=1e9)}
    assert _preds(lost, sic="7372", extra_firms=peers)["share_stability"] == pytest.approx(
        -math.log((3 / 7) / (1 / 5)))                     # losing share is as unstable as gaining
    assert _preds(rows, sic="7372", extra_firms=peers[:3])["share_stability"] is None
    assert _preds(rows, sic=None, extra_firms=peers)["share_stability"] is None


# ---------------------------------------------------------------- registered constants

def test_eleven_tests_and_investment_is_not_tested_on_compounded():
    assert len(TESTS) == 11
    assert ("investment", "held") in TESTS and ("investment", "compounded") not in TESTS


def test_windows_do_not_overlap_and_the_holdout_stops_before_the_censored_year():
    assert list(ds.DISCOVERY) == [2011, 2012, 2013, 2014, 2015, 2016, 2017]
    assert list(ds.HOLDOUT) == [2018, 2019, 2020, 2021]

# ---------------------------------------------------------------- sample

def _row(cik, year, *, state="observed", held=True, sic2="35", c0=0.5, p=0.5, rank=0.9,
         compounded=None):
    return Row(cik=cik, year=year, sic2=sic2, roic=0.3, revenue=1e9, preds={}, state=state,
               held=held, rank_t3=rank, rev_ratio=None, compounded=compounded, c0=c0, c2=0.5,
               p={"x": p})


def test_sample_codes_exits_only_in_a_bounds_run_and_never_takes_gaps():
    rows = [_row("a", 2011), _row("b", 2011, state="exit", held=None, rank=None),
            _row("c", 2011, state="gap", held=None, rank=None),
            _row("d", 2011, state="low_ic", held=False, rank=None)]
    assert [(r.cik, y) for r, y in sample(rows, "x", "held")] == [("a", 1.0), ("d", 0.0)]
    assert [y for _, y in sample(rows, "x", "held", exits=True)] == [1.0, 1.0, 0.0]
    assert [y for _, y in sample(rows, "x", "held", exits=False)] == [1.0, 0.0, 0.0]
    assert [r.cik for r, _ in sample(rows, "x", "rank")] == ["a"]          # observed only
    assert sample(rows, "missing_pred", "held") == []
    assert len(sample(rows, None, "held")) == 2                            # no predictor filter


def test_bounds_runs_code_an_exit_as_compounded_then_not_compounded():
    rows = [_row("a", 2011, compounded=True), _row("b", 2011, compounded=False),
            _row("c", 2011, state="exit", held=None, rank=None)]
    assert [y for _, y in sample(rows, "x", "compounded")] == [1.0, 0.0]
    assert [y for _, y in sample(rows, "x", "compounded", exits=True)] == [1.0, 0.0, 1.0]
    assert [y for _, y in sample(rows, "x", "compounded", exits=False)] == [1.0, 0.0, 0.0]


def test_sample_leaves_out_a_firm_with_no_sic_only_when_a_predictor_is_regressed():
    rows = [_row("a", 2011), _row("b", 2011, sic2="none"), _row("c", 2011, held=False)]
    assert [r.cik for r, _ in sample(rows, "x", "held")] == ["a", "c"]
    assert [r.cik for r, _ in sample(rows, "x", "held", exits=True)] == ["a", "c"]
    assert [r.cik for r, _ in sample(rows, "x", "rank")] == ["a", "c"]
    assert [r.cik for r, _ in sample(rows, None, "held")] == ["a", "b", "c"]   # the gates keep it


# ---------------------------------------------------------------- the sector control

def _label_world(kind, seed=5, firms=1800, years=(2011, 2012, 2013), sectors=12):
    """A predictor `label` with NO effect inside any group. Half its variance is the hold-rate
    shift of the firm's group; the group is its SIC-2 sector ("sector"), its sector in that
    start year ("sector_year"), or its sub-industry ("sic3"). The outcome depends on that shift
    and on nothing else."""
    rng = random.Random(seed)
    shift: dict = {}
    rows = []
    for y in years:
        batch = []
        for i in range(firms):
            sec, sub = i % sectors, (i // sectors) % 3
            key = {"sector": (sec,), "sector_year": (sec, y), "sic3": (sec, sub)}[kind]
            if key not in shift:
                shift[key] = rng.uniform(-0.3, 0.3)
            batch.append(Row(cik=f"f{i}", year=y, sic2=f"{sec:02d}", roic=rng.random(),
                             revenue=rng.random(),
                             preds={"label": shift[key] / 0.173 + rng.gauss(0, 1)},
                             state="observed", held=rng.random() < 0.5 + shift[key],
                             rank_t3=None, rev_ratio=None, sic3=f"{sec:02d}{sub}"))
        ds.PREDICTORS, keep = ("label",), ds.PREDICTORS
        try:
            ds._finish(batch)
        finally:
            ds.PREDICTORS = keep
        rows += batch
    return rows


def test_fit_gives_a_sector_label_no_credit():
    samp = sample(_label_world("sector"), "label", "held")
    assert abs(fit(samp, "label")) < 0.15
    # THE WRONG METRIC says the label is strong. A control that leaks sector agrees with it.
    assert raw_tercile_spread(samp, "label") > 0.20


def test_small_sectors_are_not_pooled():
    # 180 sectors of 15 rows: every one is under the 20-row line at which the note's original
    # control pooled. Merged into one group, the label keeps its whole sector effect (0.44 here).
    samp = sample(_label_world("sector", firms=900, sectors=180), "label", "held")
    assert abs(fit(samp, "label")) < 0.15


def test_fit_gives_a_sector_by_year_label_no_credit():
    # A sector cycle: the label tracks its sector's shock in THAT start year. A sector fixed
    # effect that is not also by start year leaves all of it in beta.
    samp = sample(_label_world("sector_year"), "label", "held")
    assert abs(fit(samp, "label")) < 0.15
    assert raw_tercile_spread(samp, "label") > 0.20


def test_sic2_cells_do_not_hold_a_sub_industry_fixed_and_sic3_cells_do():
    # The registered limit of the control, pinned: a SIC-3 trait still earns beta under SIC-2
    # cells. Pass rule 5 reads the SIC-3 cut for this reason.
    samp = sample(_label_world("sic3"), "label", "held")
    assert fit(samp, "label") > 0.20
    assert abs(fit(samp, "label", digits=3)) < 0.15
    with pytest.raises(ValueError):
        fit(samp, "label", digits=4)


def test_fit_reads_the_effect_inside_a_cell_and_a_lone_row_adds_nothing():
    rng = random.Random(1)
    samp = []
    for sec, year, base in (("10", 2011, 0.9), ("10", 2012, 0.1), ("20", 2011, 0.4)):
        for i in range(30):
            r = _row(f"{sec}-{i}", year, sic2=sec, c0=rng.random(), p=rng.random())
            r.c2 = rng.random()
            samp.append((r, base + 0.3 * r.p["x"]))      # the cell's level, plus 0.3 x P
    assert fit(samp, "x") == pytest.approx(0.3, abs=1e-9)
    lone = _row("lone", 2011, sic2="99", c0=0.1, p=0.99)  # the only row of its cell
    assert fit(samp + [(lone, 1e6)], "x") == pytest.approx(0.3, abs=1e-6)


# ---------------------------------------------------------------- the regression

def _synthetic(seed=7, firms=1500, years=(2011, 2012, 2013)):
    """Outcome depends on ROIC level and on a genuinely independent predictor `real`.
    `proxy` is level plus noise: correlated with the outcome, with no effect of its own."""
    rng = random.Random(seed)
    rows = []
    for y in years:
        batch = []
        for i in range(firms):
            level, real = rng.random(), rng.random()
            prob = 0.2 + 0.5 * level + 0.2 * (real - 0.5)
            batch.append(Row(cik=f"f{i}", year=y, sic2=f"{i % 5}", roic=level, revenue=rng.random(),
                             preds={"real": real, "proxy": level + rng.random()},
                             state="observed", held=rng.random() < prob, rank_t3=level,
                             rev_ratio=None))
        ds.PREDICTORS, keep = ("real", "proxy"), ds.PREDICTORS
        try:
            ds._finish(batch)
        finally:
            ds.PREDICTORS = keep
        rows += batch
    return rows


def test_fit_recovers_a_real_effect_and_zeroes_a_level_proxy():
    rows = _synthetic()
    real = fit(sample(rows, "real", "held"), "real")
    proxy_samp = sample(rows, "proxy", "held")
    proxy = fit(proxy_samp, "proxy")
    assert 0.14 < real < 0.26                      # planted 0.20
    assert abs(proxy) < 0.10                       # no effect once level is held fixed
    # THE WRONG METRIC says the proxy is strong. This is the trap the regression exists for.
    assert raw_tercile_spread(proxy_samp, "proxy") > 0.15


def _confounded(prob, proxy, seed=3):
    """Rows whose outcome follows `prob(level, size)` and whose predictor `proxy(level, size)`
    has no effect of its own."""
    rng = random.Random(seed)
    rows = []
    for y in (2011, 2012, 2013):
        batch = []
        for i in range(1500):
            level, size = rng.random(), rng.random()
            batch.append(Row(cik=f"f{i}", year=y, sic2=f"{i % 5}", roic=level, revenue=size,
                             preds={"proxy": proxy(level, size) + rng.random()}, state="observed",
                             held=rng.random() < prob(level, size), rank_t3=None, rev_ratio=None))
        ds.PREDICTORS, keep = ("proxy",), ds.PREDICTORS
        try:
            ds._finish(batch)
        finally:
            ds.PREDICTORS = keep
        rows += batch
    return sample(rows, "proxy", "held")


def test_fit_holds_size_fixed():
    samp = _confounded(lambda level, size: 0.2 + 0.5 * size, lambda level, size: size)
    assert abs(fit(samp, "proxy")) < 0.10
    assert raw_tercile_spread(samp, "proxy") > 0.15


def test_fit_holds_the_level_curve_fixed():
    # The hold rate is U-shaped in level, and the proxy is the squared distance from the
    # middle. A straight line in the level rank cannot absorb that; the squared term does.
    samp = _confounded(lambda level, size: 0.2 + 2.4 * (level - 0.5) ** 2,
                       lambda level, size: 8 * (level - 0.5) ** 2)
    assert abs(fit(samp, "proxy")) < 0.20          # 0.54 with the squared term dropped
    assert raw_tercile_spread(samp, "proxy") > 0.15


def test_level_slope_is_positive_when_level_drives_the_outcome():
    assert level_slope(sample(_synthetic(), None, "held")) > 0.3


def test_level_slope_is_within_the_start_year():
    # Pooled, a higher rank goes with holding: 2012 has the higher ranks and all but one of the
    # holds. Inside each start year the firm with the higher rank is the one that did not hold.
    samp = [(_row("a", 2011, c0=0.1), 1.0), (_row("b", 2011, c0=0.3), 0.0),
            (_row("c", 2011, c0=0.2), 0.0), (_row("d", 2012, c0=0.7), 1.0),
            (_row("e", 2012, c0=0.8), 1.0), (_row("f", 2012, c0=0.9), 0.0)]
    assert level_slope(samp) < 0


def test_fit_raises_on_a_predictor_that_is_the_level_rank():
    rows = _synthetic()
    for r in rows:
        r.p["clone"] = r.c0
    with pytest.raises(ValueError):
        fit(sample(rows, "clone", "held"), "clone")


def test_bootstrap_is_seeded_and_resamples_firms():
    samp = sample(_synthetic(firms=120), "real", "held")
    a = bootstrap(samp, "real", reps=40, seed=1)
    assert a == bootstrap(samp, "real", reps=40, seed=1)
    assert a != bootstrap(samp, "real", reps=40, seed=2)
    assert a["firms"] == 120 and a["reps"] == 40 and a["singular"] == 0
    assert a["se"] > 0 and a["lo"] < a["hi"]


def test_bootstrap_resamples_firms_and_not_rows():
    # Each firm repeats one predictor value and one outcome over six start years, so its rows
    # are one observation. A row-level resample would read them as six and shrink the SE by
    # about the square root of six; giving every row its own CIK shows that smaller number.
    rng = random.Random(11)
    rows = []
    for i in range(100):
        p, held = rng.random(), rng.random() < 0.5
        for y in range(2011, 2017):
            r = _row(f"f{i}", y, sic2=f"{i % 3}", c0=rng.random(), p=p, held=held)
            r.c2 = rng.random()
            rows.append(r)
    samp = sample(rows, "x", "held")
    by_firm = bootstrap(samp, "x", reps=120, seed=1)["se"]
    for k, (r, _) in enumerate(samp):
        r.cik = f"row{k}"
    assert by_firm > 1.6 * bootstrap(samp, "x", reps=120, seed=1)["se"]


def test_bootstrap_with_no_replication_that_can_be_fitted_raises():
    samp = [(_row(f"f{i}", 2011, sic2=f"{i}"), 1.0) for i in range(6)]     # every row alone
    with pytest.raises(ValueError, match="none of 20 replications"):
        bootstrap(samp, "x", reps=20, seed=1)


# ---------------------------------------------------------------- the pass rule

def test_discovery_bar_is_the_larger_of_ten_points_and_two_standard_errors():
    ok = {"bound_held": 0.05, "bound_not": 0.05, "rank_beta": 0.1, "beta_sic3": 0.1}
    assert discovery_rules(0.10, 0.04, **ok)["magnitude"] is True
    assert discovery_rules(0.099, 0.04, **ok)["magnitude"] is False
    assert discovery_rules(0.11, 0.06, **ok)["magnitude"] is False     # needs 0.12


def test_bounds_need_three_points_in_both_runs_and_the_rank_check_a_positive_sign():
    assert discovery_rules(0.2, 0.01, 0.03, 0.09, 0.1, 0.1)["bounds"] is True
    assert discovery_rules(0.2, 0.01, 0.029, 0.09, 0.1, 0.1)["bounds"] is False
    assert discovery_rules(0.2, 0.01, 0.09, -0.01, 0.1, 0.1)["bounds"] is False
    assert discovery_rules(0.2, 0.01, 0.09, 0.09, 0.0, 0.1)["continuous_sign"] is False


def test_sub_industry_rule_needs_half_of_beta_and_an_uncomputable_cut_fails():
    assert discovery_rules(0.2, 0.01, 0.09, 0.09, 0.1, 0.10)["sub_industry"] is True
    assert discovery_rules(0.2, 0.01, 0.09, 0.09, 0.1, 0.099)["sub_industry"] is False
    assert discovery_rules(0.2, 0.01, 0.09, 0.09, 0.1, None)["sub_industry"] is False
    # a negative beta cannot pass by having a less negative SIC-3 cut
    assert discovery_rules(-0.2, 0.01, 0.09, 0.09, 0.1, -0.05)["sub_industry"] is False
    assert set(discovery_rules(0.2, 0.01, 0.09, 0.09, 0.1, 0.1)) == {
        "magnitude", "bounds", "continuous_sign", "sub_industry"}


def test_holdout_bar_and_the_conjunction():
    assert holdout_rule(0.06, 0.03, 0.03) is True
    assert holdout_rule(0.059, 0.03, 0.03) is False
    assert holdout_rule(0.07, 0.05, 0.07) is False                     # needs 0.082
    assert holdout_rule(0.06, 0.03, 0.029) is False                    # under half of beta
    assert holdout_rule(0.06, 0.03, None) is False
    good = {"magnitude": True, "bounds": True, "continuous_sign": True, "sub_industry": True}
    assert passes(good, True) is True
    assert passes(good, False) is False
    assert passes({**good, "bounds": False}, True) is False


# ---------------------------------------------------------------- gates

def test_reproduction_gate_flags_years_outside_the_tolerance():
    expected = {2011: 2000, 2012: 2000, 2013: 2000}
    assert reproduction_failures({2011: 1700, 2012: 2300, 2013: 2000}, expected) == {}
    assert reproduction_failures({2011: 1699, 2012: 2301}, expected) == {
        2011: (1699, 2000), 2012: (2301, 2000), 2013: (0, 2000)}


def test_gap_spike_needs_five_points_over_both_neighbours():
    rates = {2014: 0.05, 2015: 0.05, 2016: 0.12, 2017: 0.05, 2018: 0.11, 2019: 0.09}
    assert gap_spikes(rates) == [2016]            # 2018 is only 2 points over 2019
    assert gap_spikes({2014: 0.05, 2015: 0.20}) == []     # an end year has one neighbour


def test_state_shares_sum_to_one():
    rows = [_row("a", 2011), _row("b", 2011, state="exit", held=None),
            _row("c", 2011, state="gap", held=None), _row("d", 2011, state="low_ic")]
    assert state_shares(rows) == {"observed": 0.25, "low_ic": 0.25, "gap": 0.25, "exit": 0.25}
    assert state_shares([]) == {}


def test_exit_rate_by_tercile_reads_the_predictor_rank():
    rows = [_row("a", 2011, p=0.1, state="exit", held=None), _row("b", 2011, p=0.2),
            _row("c", 2011, p=0.5), _row("d", 2011, p=0.9), _row("e", 2011, p=0.95)]
    assert exit_rate_by_tercile(rows, "x") == [0.5, 0.0, 0.0]


def test_state_rate_by_tercile_reads_any_state():
    rows = [_row("a", 2011, p=0.1, state="gap", held=None), _row("b", 2011, p=0.2),
            _row("c", 2011, p=0.5, state="low_ic"), _row("d", 2011, p=0.9, state="low_ic"),
            _row("e", 2011, p=0.95)]
    assert state_rate_by_tercile(rows, "x", "gap") == [0.5, 0.0, 0.0]
    assert state_rate_by_tercile(rows, "x", "low_ic") == [0.0, 1.0, 0.5]
    assert state_rate_by_tercile(rows, "x", "exit") == exit_rate_by_tercile(rows, "x") == [0, 0, 0]


def test_measure_reports_every_field_and_never_raises():
    rows = _synthetic(firms=200)
    m = measure(rows, "real", "held", reps=20, with_bounds=True)
    assert {"n", "beta", "raw_tercile_spread", "se", "lo", "hi", "bound_held", "bound_not",
            "rank_beta", "beta_sic3_cells"} <= set(m)
    for r in rows:
        r.p["clone"] = r.c0
    bad = measure(rows, "clone", "held", reps=20, with_bounds=True)
    assert bad["n"] == 600 and bad["error"].startswith("ValueError")
    empty = measure([], "real", "held", reps=20, with_bounds=False)
    assert empty["n"] == 0 and "error" in empty


def test_measure_fits_the_rank_outcome_and_each_bounds_run_on_its_own_sample():
    rows = _synthetic(firms=300)
    for r in rows:
        r.rank_t3 = 1.0 - r.p["real"]                    # the rank outcome runs AGAINST `real`
    for r in rows:
        if r.p["real"] > 0.8 and int(r.cik[1:]) % 2:     # exits sit at the favourable end
            r.state, r.held, r.rank_t3 = "exit", None, None
    m = measure(rows, "real", "held", reps=10, with_bounds=True)
    assert m["rank_beta"] < -0.8 and m["beta"] > 0.05
    assert m["bound_held"] > m["beta"] > m["bound_not"]  # exits coded held raise it, not held lower it


def test_the_bootstrap_se_is_on_the_scale_of_its_own_interval():
    b = bootstrap(sample(_synthetic(firms=300), "real", "held"), "real", reps=200, seed=3)
    assert 0.75 < b["se"] / ((b["hi"] - b["lo"]) / 3.92) < 1.25


def test_measure_counts_the_rows_the_sector_control_cannot_use():
    rows = _synthetic(firms=200)
    for r in rows[:7]:
        r.sic2 = "none"                                  # no SIC: out of the regression
    rows[0].state, rows[0].held = "exit", None           # ... and out of the bounds runs
    rows[10].sic2 = "77"                                 # the only row of its cell
    rows[11].sic3 = "777"                                # alone at SIC-3, not at SIC-2
    m = measure(rows, "real", "held", reps=20, with_bounds=True)
    assert (m["n"], m["n_no_sic"], m["n_no_sic_exit"], m["n_alone_in_cell"]) == (593, 6, 1, 1)
    assert m["n_alone_in_sic3_cell"] == 1                # row 11; the fixture gives no other a sic3
    bad = measure(rows, "missing_pred", "held", reps=20, with_bounds=False)
    assert "error" in bad
    assert [bad[k] for k in ("n", "n_no_sic", "n_no_sic_exit", "n_alone_in_cell",
                             "n_alone_in_sic3_cell")] == [0, 0, 0, 0, 0]


def test_measure_keeps_beta_when_the_sic3_cut_cannot_be_computed():
    rows = _synthetic(firms=200)
    for i, r in enumerate(rows):
        r.sic3 = f"u{i}"                                 # every row alone in its SIC-3 cell
    m = measure(rows, "real", "held", reps=20, with_bounds=False)
    assert "error" not in m and m["beta"] > 0 and m["beta_sic3_cells"] is None
    assert m["n_alone_in_sic3_cell"] == m["n"] == 600


def test_measure_keeps_beta_when_a_third_of_the_predictor_is_empty():
    # `track` takes at most seven values. With over two thirds of the rows tied at the top
    # their shared rank is under 2/3, the top third is empty and THE WRONG METRIC has no value.
    rows = _synthetic(firms=200)
    rng = random.Random(2)
    for y in (2011, 2012, 2013):
        batch = [r for r in rows if r.year == y]
        ranks = avg_ranks({i: (1.0 if i % 10 < 8 else rng.random()) for i in range(len(batch))})
        for i, r in enumerate(batch):
            r.p["tied"] = ranks[i]
    m = measure(rows, "tied", "held", reps=20, with_bounds=True)
    assert "error" not in m and m["raw_tercile_spread"] is None
    assert {"beta", "se", "bound_held", "bound_not", "rank_beta"} <= set(m)


def test_a_bootstrap_with_a_replication_that_cannot_be_fitted_fails_the_test():
    # Ten firms: 59 of 60 resamples are singular and the one survivor gives SE = 0, under
    # which any positive beta clears the 2 x SE arm.
    rows = _synthetic(firms=10, years=(2011,))
    m = measure(rows, "real", "held", reps=60, seed=1, with_bounds=False)
    assert m["error"] == "ValueError: bootstrap: 59 of 60 replications could not be fitted"
    assert m["beta"] > 0.10 and m["se"] == 0.0 and (m["reps"], m["singular"]) == (1, 59)


def test_one_replication_that_cannot_be_fitted_is_enough_to_fail_the_test(monkeypatch):
    real, calls = ds.fit, []

    def fit_once_singular(samp, pred, digits=2):
        calls.append(1)
        if len(calls) == 5:                              # the fourth resample
            raise ValueError("ols: singular normal-equations matrix")
        return real(samp, pred, digits)

    monkeypatch.setattr(ds, "fit", fit_once_singular)
    m = measure(_synthetic(firms=200), "real", "held", reps=20, with_bounds=False)
    assert m["error"] == "ValueError: bootstrap: 1 of 20 replications could not be fitted"
    assert (m["reps"], m["singular"]) == (19, 1) and m["se"] > 0


def test_share_alone_in_cell_ignores_firms_with_no_sic():
    rows = [_row("a", 2011), _row("b", 2011), _row("c", 2012), _row("d", 2011, sic2="none")]
    rows[0].sic3, rows[1].sic3, rows[2].sic3 = "351", "352", "351"
    assert share_alone_in_cell(rows, 2) == pytest.approx(1 / 3)      # c is alone in (35, 2012)
    assert share_alone_in_cell(rows, 3) == 1.0
    assert share_alone_in_cell([rows[3]], 2) is None
