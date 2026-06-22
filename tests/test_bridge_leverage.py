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


def test_bridge_interest_coverage_positive_with_signed_interest_expense():
    # edgartools to_dataframe() reports InterestExpense as a NEGATIVE deduction
    # (e.g. CMCSA -4.409B). Coverage = operating_income / |interest_expense| must
    # stay POSITIVE; dividing by the signed value yields a spurious negative ratio
    # that craters the quality sub-score (regression for the CMCSA/DGX bug).
    st = Statements(
        fiscal_years=[2025], fiscal_period_end=["2025-12-31"],
        operating_income=[20672.0], dep_amort=[10.0], interest_expense=[-4409.0],
        ebitda=[20682.0],
    )
    m = snapshot_to_metrics(TickerSnapshot(ticker="CMCSA", statements=st))
    assert m.interest_coverage == pytest.approx(20672.0 / 4409.0, rel=1e-9)
    assert m.interest_coverage > 0


def test_bridge_interest_coverage_negative_on_real_operating_loss():
    # A genuine operating loss must still read as negative coverage (can't cover
    # interest) — abs() belongs on the denominator only, never the numerator.
    st = Statements(
        fiscal_years=[2025], fiscal_period_end=["2025-12-31"],
        operating_income=[-500.0], dep_amort=[10.0], interest_expense=[-100.0],
        ebitda=[-490.0],
    )
    m = snapshot_to_metrics(TickerSnapshot(ticker="Y", statements=st))
    assert m.interest_coverage == pytest.approx(-5.0, rel=1e-9)


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
