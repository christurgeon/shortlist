import pytest

from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import Profile, Statements, TickerSnapshot


def test_bridge_derives_net_debt_to_ebitda_from_edgar():
    st = Statements(
        fiscal_years=[2024], fiscal_period_end=["2024-12-31"],
        revenue=[1000.0], net_income=[100.0],
        operating_cash_flow=[230.0], free_cash_flow=[180.0],
        total_debt=[500.0], cash_and_equivalents=[120.0],
        operating_income=[200.0], dep_amort=[50.0], interest_expense=[20.0],
        ebitda=[250.0],   # date-aligned at extraction (operating_income + D&A)
    )
    snap = TickerSnapshot(ticker="X", statements=st)
    m = snapshot_to_metrics(snap)
    assert m.revenue == 1000.0
    assert m.ebitda == 250.0                       # 200 + 50
    assert m.cash_and_equivalents == 120.0
    assert m.net_debt_to_ebitda == (500.0 - 120.0) / 250.0   # 1.52
    assert m.interest_coverage == 200.0 / 20.0     # 10.0 (EDGAR fallback, FMP absent)


def test_bridge_derives_ebit_ev_yield():
    # EBIT = operating_income[0] = 200; net debt = 500 - 100 = 400;
    # EV = market_cap + net_debt = 1300 + 400 = 1700 -> yield = 200 / 1700.
    st = Statements(operating_income=[200.0], total_debt=[500.0],
                    cash_and_equivalents=[100.0])
    snap = TickerSnapshot(ticker="X", profile=Profile(market_cap=1300.0),
                          statements=st)
    m = snapshot_to_metrics(snap)
    assert m.ebit_ev_yield == pytest.approx(200.0 / 1700.0, rel=1e-6)


def test_bridge_ebit_ev_yield_none_on_negative_ebit():
    st = Statements(operating_income=[-10.0], total_debt=[500.0],
                    cash_and_equivalents=[100.0])
    snap = TickerSnapshot(ticker="X", profile=Profile(market_cap=1300.0),
                          statements=st)
    m = snapshot_to_metrics(snap)
    assert m.ebit_ev_yield is None
