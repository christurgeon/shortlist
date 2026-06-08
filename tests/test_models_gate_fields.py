from shortlist.models import ScoreCard, StockMetrics


def test_new_leverage_fields_default_none():
    m = StockMetrics(ticker="T")
    assert m.revenue is None
    assert m.ebitda is None
    assert m.cash_and_equivalents is None
    assert m.net_debt_to_ebitda is None


def test_scorecard_carries_leverage_fields():
    c = ScoreCard(
        ticker="T", composite=0.0, quality=None, moat=None, growth=None,
        momentum=None, value=None, opportunity=None, insider=None,
    )
    assert c.ebitda is None
    assert c.net_debt_to_ebitda is None
