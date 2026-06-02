from shortlist.models import ScoreCard, StockMetrics
from shortlist.screen import _card_dict


def _c(**kw):
    base = dict(ticker="X", composite=60.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None,
                metrics=StockMetrics(ticker="X"))
    base.update(kw)
    return ScoreCard(**base)


def test_card_dict_includes_abstention_block_when_present():
    c = _c(sic_bucket="financials", confidence=0.8, scored=True,
           abstentions=[{"field": "moat", "reason": "inapplicable", "scope": "subscore"}])
    d = _card_dict(c)
    assert d["sic_bucket"] == "financials"
    assert d["confidence"] == 0.8
    assert d["scored"] is True
    assert d["abstentions"]


def test_card_dict_omits_abstentions_when_empty():
    d = _card_dict(_c())
    assert "abstentions" not in d
    assert d["scored"] is True
