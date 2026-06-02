from shortlist.providers import _construct
from shortlist.providers.edgar import EdgarProvider


def test_construct_passes_conviction_config(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_: None, raising=False)
    cfg = {"insider": {"conviction": {"enabled": True, "min_cluster_buy_value": 1000,
                                      "role_weights": {"c_suite": 1.5}}}}
    p = _construct("edgar", EdgarProvider, cfg)
    assert p._conviction == cfg["insider"]["conviction"]


def test_construct_edgar_no_config_is_none(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_: None, raising=False)
    p = _construct("edgar", EdgarProvider, {})
    assert p._conviction is None


def test_stockmetrics_has_conviction_fields():
    from shortlist.models import StockMetrics
    m = StockMetrics(ticker="X")
    assert m.insider_distinct_buyers is None
    assert m.insider_role_weighted_buy_value is None
    assert m.insider_planned_sell_value is None
