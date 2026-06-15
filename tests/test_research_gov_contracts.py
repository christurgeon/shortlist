from shortlist.models import StockMetrics
from shortlist.research.gov_contracts import context_line
from shortlist.research import assess


def _m(**kw):
    return StockMetrics(ticker="LMT", **kw)


CFG = {"enabled": True, "min_confidence": 0.8}


def test_line_renders_when_material_and_confident():
    line = context_line(_m(gov_contract_ttm_usd=1.2e10, gov_contract_prior_ttm_usd=1.0e10,
                           gov_contract_yoy_growth=0.2, gov_contract_to_revenue=0.18,
                           gov_contract_match_confidence=0.98), CFG)
    assert line is not None
    assert "Government contracts" in line
    assert "subsidiaries" in line  # the caveat is always present


def test_abstains_when_disabled():
    assert context_line(_m(gov_contract_ttm_usd=1e10,
                           gov_contract_match_confidence=0.98),
                        {"enabled": False, "min_confidence": 0.8}) is None


def test_abstains_when_low_confidence():
    assert context_line(_m(gov_contract_ttm_usd=1e10,
                           gov_contract_match_confidence=0.5), CFG) is None


def test_abstains_when_zero_or_missing():
    assert context_line(_m(gov_contract_ttm_usd=0.0,
                           gov_contract_match_confidence=0.98), CFG) is None
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


def test_quant_context_includes_gov_line():
    m = _m(gov_contract_ttm_usd=1.2e10, gov_contract_to_revenue=0.18,
           gov_contract_match_confidence=0.98)
    out = assess._quant_context(_Card(m), "", None, CFG)
    assert "Government contracts" in out


def test_quant_context_omits_gov_line_when_none():
    out = assess._quant_context(_Card(_m()), "", None, CFG)
    assert "Government contracts" not in out
