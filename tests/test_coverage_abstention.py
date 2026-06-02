from shortlist.coverage import build_coverage
from shortlist.models import ScoreCard, StockMetrics


def _card(**kw):
    base = dict(
        ticker="SCHW", composite=40.0, quality=50.0, moat=None, growth=50.0,
        momentum=50.0, value=None, opportunity=50.0, insider=50.0,
        sic_bucket="financials",
        abstentions=[
            {"field": "moat", "reason": "inapplicable", "scope": "subscore"},
            {"field": "fcf_yield", "reason": "inapplicable", "scope": "leg"},
        ],
        metrics=StockMetrics(ticker="SCHW", sic="6211"),
    )
    base.update(kw)
    return ScoreCard(**base)


def test_inapplicable_subscore_not_listed_as_coverage_gap():
    cov = build_coverage({"fmp": "gated_402", "edgar": "ok"}, {"edgar"}, _card())
    # 'moat' is None by masking, NOT a coverage gap -> excluded from unavailable.
    assert cov is not None
    assert "moat" not in cov.unavailable


def test_missing_subscore_still_listed_as_coverage_gap():
    # A sub-score that is None for a NON-masking reason stays a coverage gap.
    card = _card(quality=None, abstentions=[
        {"field": "moat", "reason": "inapplicable", "scope": "subscore"},
    ])
    cov = build_coverage({"fmp": "gated_402", "edgar": "ok"}, {"edgar"}, card)
    assert "quality" in cov.unavailable     # genuinely missing
    assert "moat" not in cov.unavailable    # masked
