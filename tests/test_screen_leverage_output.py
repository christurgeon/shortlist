import yaml
from pathlib import Path

from shortlist.models import StockMetrics
from shortlist.scoring import score


def _cfg():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def test_scorecard_copies_leverage_fields():
    m = StockMetrics(ticker="X", sic="3571", revenue=100.0, ebitda=10.0,
                     net_debt_to_ebitda=1.5)
    card = score(m, _cfg())
    assert card.ebitda == 10.0
    assert card.net_debt_to_ebitda == 1.5


def test_net_cash_display_floor_in_json():
    from shortlist.screen import _card_dict
    m = StockMetrics(ticker="X", sic="3571", revenue=100.0, ebitda=10.0,
                     net_debt_to_ebitda=-2.0)   # net cash
    card = score(m, _cfg())
    d = _card_dict(card)
    assert d["net_debt_to_ebitda"] == 0.0   # floored for display; gate saw -2.0 raw
    assert card.net_debt_to_ebitda == -2.0  # stored value stays signed
