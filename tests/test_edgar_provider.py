def test_stockmetrics_has_conviction_fields():
    from shortlist.models import StockMetrics
    m = StockMetrics(ticker="X")
    assert m.insider_distinct_buyers is None
    assert m.insider_role_weighted_buy_value is None
    assert m.insider_planned_sell_value is None
