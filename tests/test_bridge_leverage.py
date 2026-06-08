from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import Statements, TickerSnapshot


def test_bridge_derives_net_debt_to_ebitda_from_edgar():
    st = Statements(
        fiscal_years=[2024], fiscal_period_end=["2024-12-31"],
        revenue=[1000.0], net_income=[100.0],
        operating_cash_flow=[230.0], free_cash_flow=[180.0],
        total_debt=[500.0], cash_and_equivalents=[120.0],
        operating_income=[200.0], dep_amort=[50.0], interest_expense=[20.0],
    )
    snap = TickerSnapshot(ticker="X", statements=st)
    m = snapshot_to_metrics(snap)
    assert m.revenue == 1000.0
    assert m.ebitda == 250.0                       # 200 + 50
    assert m.cash_and_equivalents == 120.0
    assert m.net_debt_to_ebitda == (500.0 - 120.0) / 250.0   # 1.52
    assert m.interest_coverage == 200.0 / 20.0     # 10.0 (EDGAR fallback, FMP absent)
