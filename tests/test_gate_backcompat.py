"""With gates.leverage / gates.fcf ABSENT from config, check_gates must reproduce
the pre-feature behavior exactly (raw debt_to_equity > max; any negative FCF trips)."""
from shortlist.models import StockMetrics
from shortlist.scoring import check_gates

OLD_GATES = {"min_market_cap": 2.0e9, "max_debt_to_equity": 5.0, "min_insider_sentiment": -0.60}


def gates(m):
    return check_gates(m, OLD_GATES, bucket="unknown", config={"gates": OLD_GATES})


def test_old_over_leveraged_on_raw_dte():
    assert "over_leveraged" in gates(StockMetrics(ticker="T", market_cap=5e9, debt_to_equity=6.0))
    assert "over_leveraged" not in gates(StockMetrics(ticker="T", market_cap=5e9, debt_to_equity=4.0))


def test_old_over_leveraged_ignores_ebitda():
    # Even with a healthy net-debt/EBITDA, the absent-block path uses raw D/E only.
    m = StockMetrics(ticker="T", market_cap=5e9, debt_to_equity=6.0,
                     revenue=100.0, ebitda=10.0, net_debt_to_ebitda=1.0)
    assert "over_leveraged" in gates(m)


def test_old_negative_fcf_trips_unconditionally():
    m = StockMetrics(ticker="T", market_cap=5e9, fcf_positive=False,
                     revenue_cagr=0.50, revenue_growth_persistence=0.99)
    assert "negative_fcf" in gates(m)
