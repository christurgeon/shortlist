from datetime import date

from statistics import pstdev

from shortlist import durability
from shortlist.backtest import durability_data, durability_study
from shortlist.durability import (
    YearRow,
    as_of_for,
    count_filed_before_period_end,
    fiscal_ends,
    fy_bucket,
    history_predictors,
    investment,
    panel_rows,
    snapshot,
)


def _dur(end, val, filed, form="10-K"):
    y, m, d = (int(x) for x in end.split("-"))
    start = date(y - 1, m, d).isoformat() if (m, d) != (2, 29) else date(y - 1, 2, 28).isoformat()
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}


def _facts(**tags):
    return {"facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}}}


def _firm(end="2015-12-31", filed="2016-02-20", oi=100.0, eq=400.0, assets=1000.0, **extra):
    tags = {"Revenues": [_dur(end, 2000.0, filed)], "OperatingIncomeLoss": [_dur(end, oi, filed)],
            "StockholdersEquity": [_inst(end, eq, filed)], "Assets": [_inst(end, assets, filed)]}
    tags.update(extra)
    return _facts(**tags)


def test_bucket_shifts_early_year_ends_back():
    assert fy_bucket("2012-01-28") == 2011      # January retailer year end
    assert fy_bucket("2011-12-31") == 2011
    assert fy_bucket("2012-06-30") == 2011      # June year end belongs to the prior bucket
    assert fy_bucket("2012-09-30") == 2012


def test_a_year_end_just_after_june_can_leave_a_bucket_empty():
    # A registered limitation, pinned: a 52/53-week filer whose year ends near 30 June.
    assert [fy_bucket(e) for e in ("2015-06-28", "2016-07-03", "2017-07-02", "2018-07-01")] == [
        2014, 2016, 2017, 2017]
    # The line is between 1 and 2 July, and a day earlier in a leap year.
    assert [fy_bucket(e) for e in ("2015-07-01", "2015-07-02", "2016-06-30", "2016-07-01")] == [
        2014, 2015, 2015, 2016]


def test_roic_is_flat_tax_over_equity_plus_debt():
    f = _firm(LongTermDebtNoncurrent=[_inst("2015-12-31", 100.0, "2016-02-20")])
    row = snapshot(f, 2015)[2015]
    assert row.status == "ok"
    assert row.ic == 500.0
    assert abs(row.roic - 100.0 * 0.79 / 500.0) < 1e-12


def test_debt_free_firm_has_roic():
    # _xbrl_facts._roic_series drops this firm (no debt tag). Here missing debt is zero debt.
    row = snapshot(_firm(), 2015)[2015]
    assert row.debt == 0.0 and row.status == "ok"
    assert abs(row.roic - 100.0 * 0.79 / 400.0) < 1e-12


def test_long_term_debt_total_is_not_double_counted():
    f = _firm(LongTermDebt=[_inst("2015-12-31", 300.0, "2016-02-20")],
              LongTermDebtCurrent=[_inst("2015-12-31", 50.0, "2016-02-20")])
    assert snapshot(f, 2015)[2015].debt == 300.0     # the total already includes the 50


def test_noncurrent_plus_current_are_summed():
    f = _firm(LongTermDebtNoncurrent=[_inst("2015-12-31", 250.0, "2016-02-20")],
              LongTermDebtCurrent=[_inst("2015-12-31", 50.0, "2016-02-20")])
    assert snapshot(f, 2015)[2015].debt == 300.0


def test_low_ic_when_capital_is_under_a_tenth_of_assets_or_negative():
    assert snapshot(_firm(eq=99.0), 2015)[2015].status == "low_ic"
    assert snapshot(_firm(eq=100.0), 2015)[2015].status == "ok"      # exactly at the floor
    neg = snapshot(_firm(eq=-50.0), 2015)[2015]
    assert neg.status == "low_ic" and neg.roic is None


def test_missing_when_an_input_is_untagged():
    f = _firm()
    del f["facts"]["us-gaap"]["Assets"]
    row = snapshot(f, 2015)[2015]
    assert row.status == "missing" and row.roic is None


def test_snapshot_excludes_values_filed_after_the_as_of_date():
    # FY2015 first filed in February 2016, then RESTATED in the FY2016 10-K a year later.
    f = _firm()
    f["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"].append(
        _dur("2015-12-31", 55.0, "2017-02-20"))
    assert snapshot(f, 2015)[2015].op_income == 100.0
    assert panel_rows(f, date(2017, 6, 1))[2015].op_income == 55.0   # a later reader sees 55


def test_late_filer_is_missing_at_its_own_as_of():
    late = _firm(filed="2016-09-01")                 # 245 days after year end
    assert as_of_for("2015-12-31") == date(2016, 4, 29)
    assert fiscal_ends(late) == {2015: "2015-12-31"}
    assert snapshot(late, 2015) == {}                # nothing filed yet: no row at all


def test_a_fact_filed_before_its_period_ended_is_never_read():
    # A context typed with the wrong year. Both periods are a full year long, so only the
    # filing date shows that nobody could have known them: no value, no bucket, no later
    # "last year end" that would turn a dead filer's exit into a gap.
    f = _firm()                                                   # FY2015, filed 2016-02-20
    for tag in ("Revenues", "OperatingIncomeLoss"):
        f["facts"]["us-gaap"][tag]["units"]["USD"] += [_dur("2016-12-31", 7.0, "2016-02-20"),
                                                       _dur("2105-12-31", 7.0, "2016-02-20")]
    assert fiscal_ends(f) == {2015: "2015-12-31"}
    assert snapshot(f, 2016) is None
    assert sorted(panel_rows(f, date(2018, 1, 1))) == [2015]
    assert snapshot(f, 2015)[2015].op_income == 100.0             # the real year is untouched
    # ... and counted, so a large number on real data shows if "end <= filed" is a wrong premise
    assert count_filed_before_period_end(f) == 4 and count_filed_before_period_end(_firm()) == 0
    same_day = _firm(filed="2015-12-31")                          # filed on the day the year ended
    assert fiscal_ends(same_day) == {2015: "2015-12-31"} and count_filed_before_period_end(same_day) == 0


def test_snapshot_is_none_without_a_year_end_in_the_bucket():
    assert snapshot(_firm(), 2014) is None


def test_comparatives_give_history_inside_one_snapshot():
    f = _firm()
    for tag, fn, val in (("Revenues", _dur, 1800.0), ("OperatingIncomeLoss", _dur, 90.0),
                         ("StockholdersEquity", _inst, 380.0), ("Assets", _inst, 950.0)):
        f["facts"]["us-gaap"][tag]["units"]["USD"].append(fn("2014-12-31", val, "2016-02-20"))
    rows = snapshot(f, 2015)
    assert sorted(rows) == [2014, 2015] and rows[2014].op_income == 90.0


def test_two_year_ends_in_one_bucket_keep_the_later():
    f = _firm(end="2015-09-30", filed="2015-11-20")
    for tag, fn in (("Revenues", _dur), ("OperatingIncomeLoss", _dur)):
        f["facts"]["us-gaap"][tag]["units"]["USD"].append(fn("2015-12-31", 7.0, "2016-02-20"))
    assert fiscal_ends(f)[2015] == "2015-12-31"


def test_yearrow_properties_on_a_bare_row():
    r = YearRow(end="2015-12-31", revenue=None, op_income=None, equity=None, debt=0.0,
                assets=None, gross_profit=None)
    assert r.ic is None and r.nopat is None and r.roic is None and r.status == "missing"


def test_a_foreign_annual_form_is_never_read_from_raw_facts():
    # `annual_series` admits 20-F and 20-F/A. The study is 10-K filers only, and the filter must
    # hold on RAW company facts too: left in, a later-filed 20-F/A row replaces the 10-K value.
    f = _firm()
    f["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"].append(
        _dur("2015-12-31", 999.0, "2016-03-01", form="20-F/A"))
    assert snapshot(f, 2015) == snapshot(_firm(), 2015)
    assert snapshot(f, 2015)[2015].op_income == 100.0

    foreign = _firm()
    for node in foreign["facts"]["us-gaap"].values():
        for row in node["units"]["USD"]:
            row["form"] = "20-F"
    assert fiscal_ends(foreign) == {}
    assert panel_rows(foreign, date(2017, 1, 1)) == {}
    assert snapshot(foreign, 2015) is None


def test_the_study_and_the_live_path_share_one_compaction_and_one_set_of_helpers():
    assert durability_data.compact_facts is durability.compact_facts
    assert durability_study.quintile_floor is durability.quintile_floor
    assert durability_study.pct_rank is durability.pct_rank
    assert {"10-K", "10-K/A"} == durability.STUDY_FORMS


def _yr(end, oi, eq, assets=1000.0):
    return YearRow(end=end, revenue=2e8, op_income=oi, equity=eq, debt=0.0, assets=assets,
                   gross_profit=None)


def test_investment_is_high_for_low_capital_growth_and_needs_a_defined_prior_year():
    now = _yr("2015-12-31", 100.0, 500.0)
    assert investment({2014: _yr("2014-12-31", 100.0, 400.0), 2015: now}, 2015) == -(500.0 / 400.0 - 1.0)
    fell = investment({2014: _yr("2014-12-31", 100.0, 625.0), 2015: now}, 2015)
    assert abs(fell - 0.2) < 1e-12                       # capital fell by a fifth
    assert investment({2015: now}, 2015) is None
    # A prior year under the capital floor has no ROIC, so the growth from it is not read.
    assert investment({2014: _yr("2014-12-31", 100.0, 50.0), 2015: now}, 2015) is None


def test_history_predictors_count_seen_years_and_need_three_of_them():
    grid = [0.05, 0.10, 0.15, 0.20, 0.25]
    hist = dict.fromkeys(range(2012, 2016), grid)
    floors = dict.fromkeys(range(2012, 2016), 0.15)
    snap = {2013: _yr("2013-12-31", 100.0, 400.0),      # ROIC 0.1975 -> rank 0.6, in the top
            2014: _yr("2014-12-31", 50.0, 400.0),       # ROIC 0.09875 -> rank 0.2, below
            2015: _yr("2015-12-31", 150.0, 400.0)}      # ROIC 0.29625 -> rank 1.0, in the top
    track, stability, in_top, seen = history_predictors(snap, 2015, hist, floors)
    assert (in_top, seen) == (2, 3)
    assert track == 2 / 3
    assert stability == -pstdev([0.6, 0.2, 1.0])

    del snap[2013]
    assert history_predictors(snap, 2015, hist, floors) == (None, None, 1, 2)
    # A year with no floor (a universe under 5 firms) is not seen.
    assert history_predictors(snap, 2015, hist, {**floors, 2014: None})[3] == 1


def test_history_predictors_start_in_2012():
    grid = [0.05, 0.10, 0.15, 0.20, 0.25]
    snap = {y: _yr(f"{y}-12-31", 100.0, 400.0) for y in (2009, 2010, 2011)}
    out = history_predictors(snap, 2011, dict.fromkeys(snap, grid), dict.fromkeys(snap, 0.15))
    assert out == (None, None, 3, 3)


def test_the_tags_the_basis_reads_are_pinned():
    # These lists come from providers/_xbrl_facts.py, which the backtest also edits. A change
    # there moves what BOTH the study's compaction and the live section read, and no digest of
    # the compacted file can see a tag that was never kept. Change this list on purpose.
    assert durability.KEEP_TAGS == (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
        "OperatingIncomeLoss", "StockholdersEquity", "Assets", "LongTermDebtNoncurrent",
        "LongTermDebt", "LongTermDebtCurrent", "DebtCurrent", "GrossProfit",
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")
    raw = {"facts": {"us-gaap": {tag: {"units": {"USD": [_dur("2015-12-31", 1.0, "2016-02-20")]}}
                                 for tag in (*durability.KEEP_TAGS, "Goodwill")}}}
    assert set(durability.compact_facts(raw)["facts"]["us-gaap"]) == set(durability.KEEP_TAGS)
