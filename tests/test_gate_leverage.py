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


def test_negative_ebitda_routes_to_fallback_not_signed_ratio():
    # A leveraged money-loser (revenue 1000, EBITDA -490, per the PR #144 audit fix at
    # the bridge level: net_debt_to_ebitda now abstains to None for negative EBITDA
    # rather than sign-flipping to a "net cash"-looking negative ratio). This pins the
    # commit-message claim that the GATE itself was "already safe" regardless: even if
    # a stale/pre-fix caller still hands it a sign-flipped negative net_debt_to_ebitda,
    # `ebitda_usable` requires ebitda > 0 and must reject it outright, falling through
    # to the D/E fallback instead of reading the flipped ratio as healthy net cash.
    m = _m(revenue=1000.0, ebitda=-490.0, net_debt_to_ebitda=-0.78, debt_to_equity=1.0)
    assert "over_leveraged" not in gates(m)           # clean D/E -> fallback spares it
    # ...but a levered, weak-coverage money-loser still trips via the same fallback.
    m2 = _m(revenue=1000.0, ebitda=-490.0, net_debt_to_ebitda=-0.78,
            debt_to_equity=9.0, interest_coverage=1.0)
    assert "over_leveraged" in gates(m2)


def test_dte_exactly_at_ceiling_still_trips():
    # D/E == dte_artifact_ceiling (20.0) is NOT an artifact (strict >); weak/absent
    # coverage in the plausible window -> trips.
    assert "over_leveraged" in gates(_m(debt_to_equity=20.0))


def test_dte_exactly_at_max_does_not_trip():
    # D/E == max_debt_to_equity (5.0) is not over the bar (<= max).
    assert "over_leveraged" not in gates(_m(debt_to_equity=5.0, interest_coverage=0.1))


def test_net_debt_exactly_at_threshold_does_not_trip():
    # net_debt_to_ebitda == max (4.0) is not over the bar (strict >).
    m = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=4.0)
    assert "over_leveraged" not in gates(m)


def test_screener_shape_ebitda_usable_but_no_ratio_falls_back():
    # Screener populates revenue+ebitda but never net_debt_to_ebitda (no balance sheet):
    # falls through to the D/E fallback. Clean D/E -> no trip.
    m = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=None, debt_to_equity=1.0)
    assert "over_leveraged" not in gates(m)
    # ...and a levered, weak-coverage screener name still trips via the fallback.
    m2 = _m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=None,
            debt_to_equity=9.0, interest_coverage=1.0)
    assert "over_leveraged" in gates(m2)
