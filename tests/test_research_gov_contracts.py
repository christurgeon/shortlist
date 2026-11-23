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


def test_line_discloses_partial_when_truncated():
    line = context_line(_m(gov_contract_ttm_usd=5.3e10, gov_contract_match_confidence=1.0,
                           gov_contract_truncated=True, gov_contract_total_txns=63369,
                           gov_contract_recipient_count=4), CFG)
    assert "PARTIAL" in line
    assert "63,369" in line
    assert "summed across 4 recipients" in line


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


def _line(**kw):
    return context_line(_m(gov_contract_match_confidence=1.0, **kw), CFG)


def test_abstains_on_net_deobligation_that_rounds_to_zero():
    # Real USAspending shape: a few hundred dollars of net de-obligation.
    assert _line(gov_contract_ttm_usd=-1219.06, gov_contract_prior_ttm_usd=3649.58,
                 gov_contract_yoy_growth=-1.334) is None


def test_abstains_when_positive_total_rounds_to_zero():
    assert _line(gov_contract_ttm_usd=400_000.0, gov_contract_to_revenue=0.01) is None


def test_abstains_on_non_finite_total():
    assert _line(gov_contract_ttm_usd=float("nan")) is None


def test_material_net_deobligation_is_stated_not_signed():
    line = _line(gov_contract_ttm_usd=-42e6, gov_contract_prior_ttm_usd=180e6,
                 gov_contract_yoy_growth=-1.233, gov_contract_to_revenue=-0.05)
    assert line is not None
    assert "~$42M net de-obligation" in line
    assert "~$180M obligated in the prior 12m" in line
    assert "$-" not in line
    assert "% of revenue" not in line and "vs prior 12m" not in line


def test_net_deobligation_omits_immaterial_prior():
    line = _line(gov_contract_ttm_usd=-42e6, gov_contract_prior_ttm_usd=3649.58)
    assert "prior 12m" not in line


def test_growth_omitted_when_prior_rounds_to_zero():
    line = _line(gov_contract_ttm_usd=5e6, gov_contract_prior_ttm_usd=3649.58,
                 gov_contract_yoy_growth=1369.0)
    assert line is not None
    assert "vs prior 12m" not in line


def test_flat_growth_never_prints_negative_zero():
    line = _line(gov_contract_ttm_usd=99.7e6, gov_contract_prior_ttm_usd=100e6,
                 gov_contract_yoy_growth=-0.003)
    assert "+0% vs prior 12m" in line
    assert "-0" not in line


def test_positive_total_line_is_unchanged():
    line = _line(gov_contract_ttm_usd=1.2e10, gov_contract_prior_ttm_usd=1.0e10,
                 gov_contract_yoy_growth=0.2, gov_contract_to_revenue=0.18)
    assert line == (
        "Government contracts: ~$12.0B federal contract obligations (trailing 12m, "
        "primary recipient matched at 1.00); ~18% of revenue; +20% vs prior 12m. "
        "Attribution is fuzzy and may exclude subsidiaries booked under other names — "
        "reconcile against the business (a new award is a tailwind; a recompete loss a risk).")
