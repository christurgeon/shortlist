"""The lobbying feature must NOT touch the composite/flags in v1."""
import yaml
from pathlib import Path
from shortlist.models import StockMetrics
from shortlist.scoring import score


def _cfg():
    return yaml.safe_load(Path("config.yaml").read_text())


def test_lobbying_fields_do_not_change_composite_or_flags():
    cfg = _cfg()
    base = StockMetrics(ticker="LMT", gross_margin=0.12, net_margin=0.09,
                        revenue=6.5e10, market_cap=1.1e11)
    withlb = StockMetrics(ticker="LMT", gross_margin=0.12, net_margin=0.09,
                          revenue=6.5e10, market_cap=1.1e11,
                          lobbying_ttm_usd=1.3e7, lobbying_yoy_growth=0.2,
                          lobbying_match_confidence=0.99)
    a, b = score(base, cfg), score(withlb, cfg)
    assert a.composite == b.composite
    assert a.flags == b.flags
