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


def test_panel_to_metrics_net_debt_to_ebitda_abstains_on_negative_ebitda():
    # EBITDA = operating_income + D&A = -490 < 0: the ratio's sign flips and a
    # leveraged money-loser would read as net cash (the inverted backtest leverage
    # axis would score it at the TOP). Abstain instead — mirrors the bridge.
    p = XbrlPanel(
        revenue={"2025-12-31": 1000.0},
        operating_income={"2025-12-31": -500.0},
        dep_amort={"2025-12-31": 10.0},
        total_debt={"2025-12-31": 500.0},
        cash={"2025-12-31": 120.0},
    )
    m = panel_to_metrics(p, ticker="Z", sic=None, price=None, price_at=lambda d: None)
    assert m.ebitda == -490.0
    assert m.net_debt_to_ebitda is None


def test_panel_to_metrics_net_cash_stays_signed_with_positive_ebitda():
    # Genuine net cash (cash > debt, EBITDA > 0) must KEEP its negative sign —
    # the guard is on the denominator only, never a clamp on the ratio.
    p = XbrlPanel(
        revenue={"2025-12-31": 1000.0},
        operating_income={"2025-12-31": 200.0},
        dep_amort={"2025-12-31": 50.0},
        total_debt={"2025-12-31": 100.0},
        cash={"2025-12-31": 400.0},
    )
    m = panel_to_metrics(p, ticker="N", sic=None, price=None, price_at=lambda d: None)
    assert m.net_debt_to_ebitda == (100.0 - 400.0) / 250.0
