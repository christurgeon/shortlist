from shortlist.models import StockMetrics
from shortlist.scoring import check_gates

# Minimal gates config with the leverage block ON (mirrors shipped config.yaml).
GATES_ON = {
    "min_market_cap": 2.0e9,
    "max_debt_to_equity": 5.0,
    "min_insider_sentiment": -0.60,
    "leverage": {
        "enabled": True,
        "max_net_debt_to_ebitda": 4.0,
        "min_ebitda_margin": 0.03,
        "dte_artifact_ceiling": 20.0,
        "min_interest_coverage_for_gate": 2.0,
    },
}


def _m(**kw):
    return StockMetrics(ticker="T", market_cap=5e9, **kw)


def gates(m):
    # bucket="unknown" so gate_applicable never masks; config carries the block.
    return check_gates(m, GATES_ON, bucket="unknown", config={"gates": GATES_ON})


def test_high_net_debt_to_ebitda_trips():
    m = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=5.0)
    assert "over_leveraged" in gates(m)


def test_healthy_net_debt_to_ebitda_does_not_trip():
    m = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=2.0)
    assert "over_leveraged" not in gates(m)


def test_net_cash_never_trips():
    m = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=-1.5)
    assert "over_leveraged" not in gates(m)


def test_negative_equity_artifact_never_trips():
    # Buyback compounder: negative D/E, no EBITDA data.
    m = _m(debt_to_equity=-8.0)
    assert "over_leveraged" not in gates(m)


def test_explosive_dte_artifact_never_trips():
    # Thin positive equity inflates D/E past the ceiling -> artifact, abstain.
    m = _m(debt_to_equity=55.0)
    assert "over_leveraged" not in gates(m)


def test_fallback_plausible_leverage_weak_coverage_trips():
    m = _m(debt_to_equity=8.0, interest_coverage=1.2)
    assert "over_leveraged" in gates(m)


def test_fallback_plausible_leverage_no_coverage_trips():
    m = _m(debt_to_equity=8.0)  # interest_coverage None -> still trips (fail-closed)
    assert "over_leveraged" in gates(m)


def test_fallback_strong_coverage_spares():
    m = _m(debt_to_equity=8.0, interest_coverage=6.0)
    assert "over_leveraged" not in gates(m)


def test_near_zero_ebitda_margin_routes_to_fallback():
    # EBITDA margin 1% < 3% floor -> not usable -> fallback; D/E clean -> no trip.
    m = _m(revenue=100.0, ebitda=1.0, net_debt_to_ebitda=50.0, debt_to_equity=1.0)
    assert "over_leveraged" not in gates(m)
