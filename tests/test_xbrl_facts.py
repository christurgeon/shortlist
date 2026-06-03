# tests/test_xbrl_facts.py
from datetime import date
from shortlist.providers._xbrl_facts import annual_series

def _row(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}

def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}

def _us(concept, rows, unit="USD"):
    return {"facts": {"us-gaap": {concept: {"units": {unit: rows}}}}}

def test_annual_series_filters_future_filings_and_picks_latest_restatement():
    cf = _us("Revenues", [
        _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
        _row("2021-01-01", "2021-12-31", 105, "2023-02-01"),
        _row("2022-01-01", "2022-12-31", 130, "2024-02-01"),
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2023, 6, 1)) == {"2021-12-31": 105.0}

def test_annual_series_drops_non_annual_periods():
    cf = _us("Revenues", [
        _row("2022-10-01", "2022-12-31", 30, "2023-02-01"),
        _row("2022-01-01", "2022-12-31", 120, "2023-02-01"),
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2022-12-31": 120.0}

def test_annual_series_priority_wins_on_shared_end_not_latest_filed():
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_row("2024-02-01", "2025-01-31", 681, "2025-03-01")]}},
        "SalesRevenueNet": {"units": {"USD": [_row("2024-02-01", "2025-01-31", 674, "2025-03-20")]}},
    }}}
    s = annual_series(cf, ["Revenues", "SalesRevenueNet"], as_of=date(2026, 1, 1))
    assert s == {"2025-01-31": 681.0}

def test_annual_series_falls_through_aliases_by_year():
    cf = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _row("2018-01-01", "2018-12-31", 110, "2019-02-01")]}},
        "SalesRevenueNet": {"units": {"USD": [
            _row("2017-01-01", "2017-12-31", 90, "2018-02-01")]}},
    }}}
    s = annual_series(
        cf, ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
        as_of=date(2020, 1, 1))
    assert s == {"2018-12-31": 110.0, "2017-12-31": 90.0}

def test_annual_series_instant_concept_has_no_start():
    cf = _us("StockholdersEquity", [_inst("2022-12-31", 500, "2023-02-01")])
    s = annual_series(cf, ["StockholdersEquity"], as_of=date(2024, 1, 1), instant=True)
    assert s == {"2022-12-31": 500.0}

def test_annual_series_skips_rows_with_unparseable_dates():
    cf = _us("Revenues", [
        _row("2021-01-01", "2021-12-31", 100, ""),           # empty filed -> skipped
        _row("not-a-date", "2020-12-31", 80, "2021-02-01"),  # bad start -> skipped
        _row("2019-01-01", "2019-12-31", 90, "2020-02-01"),  # good
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2019-12-31": 90.0}

def test_annual_series_filed_equal_as_of_is_inclusive():
    cf = _us("Revenues", [_row("2021-01-01", "2021-12-31", 100, "2022-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2022, 2, 1)) == {"2021-12-31": 100.0}


from shortlist.providers._xbrl_facts import align_fcf, sum_aligned, ratio_latest, desc, latest

def test_align_fcf_subtracts_capex_only_on_shared_ends():
    fcf = align_fcf({"2022-12-31": 200.0, "2021-12-31": 180.0, "2020-12-31": 160.0},
                    {"2022-12-31": 50.0, "2021-12-31": 40.0})   # 2020 capex missing
    assert fcf == {"2022-12-31": 150.0, "2021-12-31": 140.0}    # 2020 dropped

def test_sum_aligned_adds_only_on_shared_ends():
    assert sum_aligned({"2022-12-31": 300.0, "2021-12-31": 280.0},
                       {"2022-12-31": 30.0}) == {"2022-12-31": 330.0}

def test_ratio_latest_uses_latest_common_end_and_guards_zero():
    assert ratio_latest({"2022-12-31": 150.0, "2021-12-31": 130.0},
                        {"2022-12-31": 1200.0, "2020-12-31": 1000.0}) == 150.0 / 1200.0
    assert ratio_latest({"2022-12-31": 1.0}, {"2022-12-31": 0.0}) is None   # div-by-zero
    assert ratio_latest({"2022-12-31": 1.0}, {"2021-12-31": 2.0}) is None   # no common end

def test_desc_returns_values_newest_first():
    assert desc({"2020-12-31": 1.0, "2022-12-31": 3.0, "2021-12-31": 2.0}) == [3.0, 2.0, 1.0]

def test_latest_returns_most_recent_or_none():
    assert latest({"2020-12-31": 1.0, "2022-12-31": 3.0, "2021-12-31": 2.0}) == 3.0
    assert latest({}) is None
