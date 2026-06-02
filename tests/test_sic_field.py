from shortlist.data.models import Profile
from shortlist.models import StockMetrics


def test_stockmetrics_has_sic_default_none():
    assert StockMetrics(ticker="X").sic is None
    assert StockMetrics(ticker="X", sic="6211").sic == "6211"


def test_profile_has_sic_default_none():
    assert Profile().sic is None
    assert Profile(sic="6798").sic == "6798"
