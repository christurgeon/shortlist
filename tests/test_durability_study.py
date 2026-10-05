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
    cross_section,
    discovery_rules,
    exit_rate_by_tercile,
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
    state_shares,
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
    # cells. The SIC-3 cut is reported beside every beta for this reason.
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


def test_level_slope_is_positive_when_level_drives_the_outcome():
    assert level_slope(sample(_synthetic(), None, "held")) > 0.3


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


# ---------------------------------------------------------------- the pass rule

def test_discovery_bar_is_the_larger_of_ten_points_and_two_standard_errors():
    ok = {"bound_held": 0.05, "bound_not": 0.05, "rank_beta": 0.1}
    assert discovery_rules(0.10, 0.04, **ok)["magnitude"] is True
    assert discovery_rules(0.099, 0.04, **ok)["magnitude"] is False
    assert discovery_rules(0.11, 0.06, **ok)["magnitude"] is False     # needs 0.12


def test_bounds_need_three_points_in_both_runs_and_the_rank_check_a_positive_sign():
    assert discovery_rules(0.2, 0.01, 0.03, 0.09, 0.1)["bounds"] is True
    assert discovery_rules(0.2, 0.01, 0.029, 0.09, 0.1)["bounds"] is False
    assert discovery_rules(0.2, 0.01, 0.09, -0.01, 0.1)["bounds"] is False
    assert discovery_rules(0.2, 0.01, 0.09, 0.09, 0.0)["continuous_sign"] is False


def test_holdout_bar_and_the_conjunction():
    assert holdout_rule(0.06, 0.03) is True
    assert holdout_rule(0.059, 0.03) is False
    assert holdout_rule(0.07, 0.05) is False                           # needs 0.082
    good = {"magnitude": True, "bounds": True, "continuous_sign": True}
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


def test_measure_reports_every_field_and_never_raises():
    rows = _synthetic(firms=200)
    m = measure(rows, "real", "held", reps=20, with_bounds=True)
    assert {"n", "beta", "raw_tercile_spread", "se", "lo", "hi", "bound_held", "bound_not",
            "rank_beta"} <= set(m)
    for r in rows:
        r.p["clone"] = r.c0
    bad = measure(rows, "clone", "held", reps=20, with_bounds=True)
    assert bad["n"] == 600 and bad["error"].startswith("ValueError")
    empty = measure([], "real", "held", reps=20, with_bounds=False)
    assert empty["n"] == 0 and "error" in empty


def test_measure_counts_the_rows_the_sector_control_cannot_use():
    rows = _synthetic(firms=200)
    for r in rows[:7]:
        r.sic2 = "none"                                  # no SIC: out of the regression
    rows[0].state, rows[0].held = "exit", None           # ... and out of the bounds runs
    rows[10].sic2 = "77"                                 # the only row of its cell
    m = measure(rows, "real", "held", reps=20, with_bounds=True)
    assert (m["n"], m["n_no_sic"], m["n_no_sic_exit"], m["n_alone_in_cell"]) == (593, 6, 1, 1)
    bad = measure(rows, "missing_pred", "held", reps=20, with_bounds=False)
    assert "error" in bad
    assert [bad[k] for k in ("n", "n_no_sic", "n_no_sic_exit", "n_alone_in_cell")] == [0, 0, 0, 0]
