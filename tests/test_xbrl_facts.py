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


# ---------------------------------------------------------------------------
# Task 5: XbrlPanel + extract_panel
# ---------------------------------------------------------------------------
from shortlist.providers._xbrl_facts import extract_panel


def _annual(concept, rows, unit="USD"):
    return {concept: {"units": {unit: rows}}}


def test_extract_panel_builds_end_keyed_dicts():
    gaap = {}
    gaap.update(_annual("Revenues", [
        _row("2021-01-01", "2021-12-31", 1000, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 1200, "2023-02-01")]))
    gaap.update(_annual("NetIncomeLoss", [
        _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 150, "2023-02-01")]))
    gaap.update(_annual("NetCashProvidedByUsedInOperatingActivities", [
        _row("2022-01-01", "2022-12-31", 200, "2023-02-01")]))
    gaap.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row("2022-01-01", "2022-12-31", 50, "2023-02-01")]))
    gaap.update(_annual("StockholdersEquity", [_inst("2022-12-31", 600, "2023-02-01")]))
    facts = {"facts": {"us-gaap": gaap,
                       "dei": _annual("EntityCommonStockSharesOutstanding",
                                      [_inst("2022-12-31", 10, "2023-02-01")],
                                      unit="shares")}}
    p = extract_panel(facts, as_of=date(2024, 1, 1))
    assert p.revenue == {"2022-12-31": 1200.0, "2021-12-31": 1000.0}
    assert p.net_income == {"2022-12-31": 150.0, "2021-12-31": 100.0}
    assert p.fcf == {"2022-12-31": 150.0}        # 200 - 50, only FY2022 has both
    assert p.total_equity == {"2022-12-31": 600.0}
    assert p.shares == 10.0


def test_extract_panel_gross_profit_falls_back_to_revenue_minus_cogs():
    gaap = {}
    gaap.update(_annual("Revenues", [_row("2022-01-01", "2022-12-31", 1200, "2023-02-01")]))
    gaap.update(_annual("CostOfGoodsAndServicesSold",
                        [_row("2022-01-01", "2022-12-31", 700, "2023-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.gross_profit == {"2022-12-31": 500.0}   # 1200 - 700


def test_extract_panel_total_debt_sums_lt_and_current():
    gaap = {}
    gaap.update(_annual("Revenues", [_row("2022-01-01", "2022-12-31", 1000, "2023-02-01")]))
    gaap.update(_annual("LongTermDebtNoncurrent", [_inst("2022-12-31", 300, "2023-02-01")]))
    gaap.update(_annual("LongTermDebtCurrent", [_inst("2022-12-31", 50, "2023-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.total_debt == {"2022-12-31": 350.0}

def test_extract_panel_diluted_eps_uses_usd_per_share_unit():
    gaap = _annual("EarningsPerShareDiluted",
                   [_row("2022-01-01", "2022-12-31", 3.5, "2023-02-01")], unit="USD/shares")
    # a stray USD-denominated row must NOT be picked up for EPS
    gaap["EarningsPerShareDiluted"]["units"]["USD"] = [
        _row("2022-01-01", "2022-12-31", 999.0, "2023-02-01")]
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.diluted_eps == {"2022-12-31": 3.5}

def test_extract_panel_shares_picks_latest_instant():
    gaap = _annual("Revenues", [_row("2022-01-01", "2022-12-31", 1000, "2023-02-01")])
    dei = _annual("EntityCommonStockSharesOutstanding", [
        _inst("2021-12-31", 8, "2022-02-01"),
        _inst("2022-12-31", 10, "2023-02-01")], unit="shares")
    p = extract_panel({"facts": {"us-gaap": gaap, "dei": dei}}, as_of=date(2024, 1, 1))
    assert p.shares == 10.0


# ---------------------------------------------------------------------------
# Task 6: panel_to_metrics — StockMetrics derivation from XbrlPanel
# ---------------------------------------------------------------------------
from shortlist.providers._xbrl_facts import XbrlPanel, panel_to_metrics

def test_panel_to_metrics_derives_legs_and_price_dependent_value():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100, "2020-12-31": 1000},
        net_income={"2022-12-31": 150, "2021-12-31": 130, "2020-12-31": 100},
        fcf={"2022-12-31": 120, "2021-12-31": 110, "2020-12-31": 90},
        diluted_eps={"2022-12-31": 3.0, "2021-12-31": 2.6, "2020-12-31": 2.0},
        gross_profit={"2022-12-31": 600, "2021-12-31": 550, "2020-12-31": 500},
        total_equity={"2022-12-31": 600, "2021-12-31": 560, "2020-12-31": 520},
        total_debt={"2022-12-31": 300, "2021-12-31": 280, "2020-12-31": 260},
        operating_income={"2022-12-31": 200, "2021-12-31": 180, "2020-12-31": 150},
        interest_expense={"2022-12-31": 20, "2021-12-31": 18, "2020-12-31": 15},
        pretax_income={"2022-12-31": 180, "2021-12-31": 162, "2020-12-31": 135},
        income_tax={"2022-12-31": 36, "2021-12-31": 32, "2020-12-31": 27},
        shares=10.0,
    )
    prices = {date(2022, 12, 31): 60.0, date(2021, 12, 31): 52.0, date(2020, 12, 31): 40.0}
    m = panel_to_metrics(p, ticker="TST", sic="3711", price=60.0,
                         price_at=lambda d: prices.get(d))
    assert m.ticker == "TST" and m.sic == "3711"
    assert round(m.net_margin, 4) == round(150 / 1200, 4)
    assert round(m.roe, 4) == round(150 / 600, 4)
    assert round(m.debt_to_equity, 4) == round(300 / 600, 4)
    assert round(m.gross_margin, 4) == round(600 / 1200, 4)
    assert round(m.interest_coverage, 4) == round(200 / 20, 4)
    assert m.roic is not None and m.roic_5y_avg is not None
    assert m.revenue_cagr is not None and m.fcf_cagr is not None
    assert m.eps_cagr is not None and m.revenue_growth_persistence == 1.0
    assert round(m.fcf_yield, 4) == round(120 / 600, 4)    # market_cap = 60 * 10 = 600
    assert round(m.pe_ttm, 4) == round(60 / 3.0, 4)
    assert m.pe_median_5y is not None                       # >= 2 annual PE points

def test_panel_to_metrics_aligns_ratios_by_end_not_position():
    # gross_profit is MISSING the newest year that revenue has -> must pair by the
    # common fiscal end (2021), never gp_values[0]/rev_values[0].
    p = XbrlPanel(revenue={"2022-12-31": 1200, "2021-12-31": 1100},
                  gross_profit={"2021-12-31": 550})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert round(m.gross_margin, 4) == round(550 / 1100, 4)

def test_panel_to_metrics_value_degrades_when_price_absent():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100, "2020-12-31": 1000},
        net_income={"2022-12-31": 150, "2021-12-31": 130, "2020-12-31": 100},
        gross_profit={"2022-12-31": 600, "2021-12-31": 550, "2020-12-31": 500},
        total_equity={"2022-12-31": 600}, fcf={"2022-12-31": 120},
        diluted_eps={"2022-12-31": 3.0}, shares=10.0)
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.market_cap is None and m.fcf_yield is None and m.pe_ttm is None
    assert m.net_margin is not None and m.revenue_cagr is not None

def test_panel_to_metrics_none_safe_on_empty_panel():
    m = panel_to_metrics(XbrlPanel(), ticker="X", sic=None, price=None,
                         price_at=lambda d: None)
    assert m.net_margin is None and m.fcf_yield is None and m.pe_ttm is None
    assert m.revenue_cagr is None and m.roic is None

def test_panel_to_metrics_roic_none_for_negative_invested_capital():
    # Buyback-driven negative equity: eq + dc < 0 must yield roic None, not a
    # backwards (negative) ROIC for a profitable firm.
    p = XbrlPanel(
        operating_income={"2022-12-31": 200},
        total_equity={"2022-12-31": -500}, total_debt={"2022-12-31": 300},
        pretax_income={"2022-12-31": 180}, income_tax={"2022-12-31": 36})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.roic is None and m.roic_5y_avg is None

def test_panel_to_metrics_pe_median_none_with_single_pe_point():
    p = XbrlPanel(revenue={"2022-12-31": 1000}, diluted_eps={"2022-12-31": 4.0})
    m = panel_to_metrics(p, ticker="X", sic="3711", price=80.0, price_at=lambda d: 80.0)
    assert m.pe_ttm == 80.0 / 4.0      # pe_ttm computes from latest EPS
    assert m.pe_median_5y is None      # median_pe needs >= 2 points
