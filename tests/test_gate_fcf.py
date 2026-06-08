from shortlist.models import StockMetrics
from shortlist.scoring import check_flags, check_gates

GATES_ON = {
    "min_market_cap": 2.0e9, "max_debt_to_equity": 5.0, "min_insider_sentiment": -0.60,
    "fcf": {"enabled": True, "excuse_min_revenue_cagr": 0.15, "excuse_min_persistence": 0.70},
}


def gates(m):
    return check_gates(m, GATES_ON, bucket="unknown", config={"gates": GATES_ON})


def test_hyper_grower_negative_fcf_not_gated():
    m = StockMetrics(ticker="T", market_cap=5e9, fcf_positive=False,
                     revenue_cagr=0.30, revenue_growth_persistence=0.80)
    assert "negative_fcf" not in gates(m)


def test_stagnant_burner_gated():
    m = StockMetrics(ticker="T", market_cap=5e9, fcf_positive=False,
                     revenue_cagr=0.04, revenue_growth_persistence=0.80)
    assert "negative_fcf" in gates(m)


def test_low_persistence_grower_gated():
    m = StockMetrics(ticker="T", market_cap=5e9, fcf_positive=False,
                     revenue_cagr=0.30, revenue_growth_persistence=0.50)
    assert "negative_fcf" in gates(m)


def test_positive_fcf_never_gated():
    m = StockMetrics(ticker="T", market_cap=5e9, fcf_positive=True)
    assert "negative_fcf" not in gates(m)


def test_cash_burn_flag_fires_on_any_negative_fcf():
    m = StockMetrics(ticker="T", fcf_positive=False)
    assert "cash_burn" in check_flags(m, {"cash_burn": {"enabled": True}})


def test_cash_burn_flag_off_when_block_absent():
    m = StockMetrics(ticker="T", fcf_positive=False)
    assert "cash_burn" not in check_flags(m, {})
