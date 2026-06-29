from shortlist.models import ScoreCard, StockMetrics
from shortlist.screen import _card_dict


def _c(**kw):
    m = StockMetrics(ticker="X")
    m.pct_to_52w_high = 0.9123
    m.max_daily_return = 0.0456
    m.vol_scaled_momentum = 1.234
    base = dict(ticker="X", composite=60.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None, metrics=m)
    base.update(kw)
    return ScoreCard(**base)


def test_card_dict_surfaces_price_refinement_axes():
    d = _card_dict(_c())
    assert d["pct_to_52w_high"] == 0.9123
    assert d["max_daily_return"] == 0.0456
    assert d["vol_scaled_momentum"] == 1.234


def test_card_dict_price_axes_none_safe():
    d = _card_dict(_c(metrics=StockMetrics(ticker="X")))
    assert d["pct_to_52w_high"] is None
    assert d["max_daily_return"] is None
    assert d["vol_scaled_momentum"] is None
