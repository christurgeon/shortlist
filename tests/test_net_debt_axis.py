from shortlist.models import StockMetrics
from shortlist.scoring import net_debt_to_ebitda_score

T = {"net_debt_to_ebitda": [6.0, 0.0]}  # inverted: less leverage -> higher score


def test_zero_leverage_tops_band():
    assert net_debt_to_ebitda_score(StockMetrics(ticker="T", net_debt_to_ebitda=0.0), T) == 100.0


def test_high_leverage_bottoms_band():
    assert net_debt_to_ebitda_score(StockMetrics(ticker="T", net_debt_to_ebitda=6.0), T) == 0.0


def test_net_cash_clamps_to_top():
    assert net_debt_to_ebitda_score(StockMetrics(ticker="T", net_debt_to_ebitda=-2.0), T) == 100.0


def test_none_when_band_or_signal_absent():
    assert net_debt_to_ebitda_score(StockMetrics(ticker="T", net_debt_to_ebitda=3.0), {}) is None
    assert net_debt_to_ebitda_score(StockMetrics(ticker="T"), T) is None
