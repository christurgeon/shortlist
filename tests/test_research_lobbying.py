from shortlist.models import StockMetrics
from shortlist.research.lobbying import context_line
from shortlist.research import assess


def _m(**kw):
    return StockMetrics(ticker="LMT", **kw)


CFG = {"enabled": True, "min_confidence": 0.85}


def test_line_renders_when_material_and_confident():
    line = context_line(_m(lobbying_ttm_usd=1.3e7, lobbying_prior_ttm_usd=1.1e7,
                           lobbying_yoy_growth=0.18, lobbying_registrant_count=22,
                           lobbying_match_confidence=0.99), CFG)
    assert line is not None
    assert "Federal lobbying" in line
    assert "22 registrants" in line


def test_line_discloses_partial_when_truncated():
    line = context_line(_m(lobbying_ttm_usd=1.3e7, lobbying_match_confidence=0.99,
                           lobbying_truncated=True, lobbying_total_filings=500), CFG)
    assert "PARTIAL" in line


def test_abstains_when_disabled():
    assert context_line(_m(lobbying_ttm_usd=1e7, lobbying_match_confidence=0.99),
                        {"enabled": False, "min_confidence": 0.85}) is None


def test_abstains_when_low_confidence():
    assert context_line(_m(lobbying_ttm_usd=1e7, lobbying_match_confidence=0.6), CFG) is None


def test_abstains_when_zero_or_missing():
    assert context_line(_m(lobbying_ttm_usd=0.0, lobbying_match_confidence=0.99), CFG) is None
    assert context_line(_m(), CFG) is None


class _Card:
    def __init__(self, m):
        self.metrics = m
        self.composite = 50.0
        self.gates = []
        self.flags = []
        for k in ("quality", "moat", "growth", "momentum", "value", "insider",
                  "risk", "confidence", "sic_bucket"):
            setattr(self, k, None)


def test_quant_context_includes_lobbying_line():
    m = _m(lobbying_ttm_usd=1.3e7, lobbying_registrant_count=22,
           lobbying_match_confidence=0.99)
    out = assess._quant_context(_Card(m), "", None, CFG)
    assert "Federal lobbying" in out


def test_quant_context_omits_lobbying_line_when_none():
    out = assess._quant_context(_Card(_m()), "", None, CFG)
    assert "Federal lobbying" not in out
