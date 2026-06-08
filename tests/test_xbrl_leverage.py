from shortlist.providers._xbrl_facts import XbrlPanel, panel_to_metrics


def test_panel_to_metrics_net_debt_to_ebitda():
    p = XbrlPanel(
        revenue={"2024-12-31": 1000.0},
        net_income={"2024-12-31": 100.0},
        operating_income={"2024-12-31": 200.0},
        dep_amort={"2024-12-31": 50.0},
        total_debt={"2024-12-31": 500.0},
        cash={"2024-12-31": 120.0},
    )
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.revenue == 1000.0
    assert m.ebitda == 250.0
    assert m.cash_and_equivalents == 120.0
    assert m.net_debt_to_ebitda == (500.0 - 120.0) / 250.0
