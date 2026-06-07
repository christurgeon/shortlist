from types import SimpleNamespace
from shortlist.models import Coverage
from shortlist.research.coverage_caveat import coverage_caveats


def _card(**kw):
    base = dict(quality=80, moat=70, growth=60, momentum=50, value=40, insider=30,
                coverage=None, abstentions=[], sic_bucket="unknown", metrics=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_clean_card_no_caveats():
    assert coverage_caveats(_card()) == ([], [])


def test_none_card():
    assert coverage_caveats(None) == ([], [])


def test_real_gap_from_coverage_fmp_gated():
    cov = Coverage(providers={"fmp": "gated_402"},
                   unavailable=["value", "upside_to_target"], note="…")
    dw, na = coverage_caveats(_card(value=None, coverage=cov))
    assert na == []
    assert len(dw) == 1
    assert "value axis" in dw[0]
    assert "FMP gated this symbol" in dw[0]


def test_null_axis_without_coverage_block():
    # harness path: a leg is None but every provider was "ok" so coverage is None
    dw, na = coverage_caveats(_card(insider=None, coverage=None))
    assert na == []
    assert dw and "insider axis" in dw[0]


def test_structural_na_is_not_a_gap():
    abst = [{"field": "moat", "reason": "inapplicable", "scope": "subscore"},
            {"field": "roic", "reason": "inapplicable", "scope": "leg"}]
    dw, na = coverage_caveats(_card(moat=None, abstentions=abst, sic_bucket="financials"))
    assert dw == []                         # masked != gap
    assert len(na) == 1
    assert "moat" in na[0] and "financials" in na[0]
    assert "roic" not in na[0]              # scope==leg excluded
