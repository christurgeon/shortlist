from datetime import date
from shortlist.models import ScoreCard, StockMetrics
from shortlist.scout.models import RunManifest, SignalStatus
from shortlist.scout.report.viewmodel import build_view_model


def _card(ticker, comp, **kw):
    base = dict(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                momentum=80, value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _manifest():
    return RunManifest(session=date(2026, 6, 4),
                       signals=[SignalStatus("edgar_form4", True, "2 clusters")],
                       raw=10, after_dedup=8, after_prefilter=5, screened=2,
                       dropped_for_budget=1, researched=["AAPL"], notes=["hi"])


def test_leaders_sorted_by_scored_then_composite():
    cards = [_card("LOW", 40.0), _card("HIGH", 90.0),
             _card("NS1", 99.0, scored=False), _card("NS2", 50.0, scored=False)]
    vm = build_view_model(cards, _manifest(), assessments={})
    assert [l.ticker for l in vm.leaders] == ["HIGH", "LOW", "NS1", "NS2"]


def test_target_upside_uses_metrics_property():
    m = StockMetrics(ticker="AAPL", price=100.0, target_median=137.0)
    vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(), assessments={})
    assert abs(vm.leaders[0].metrics.target_upside - 0.37) < 1e-6


def test_target_upside_none_for_missing_or_zero_price():
    for p in (None, 0.0):
        m = StockMetrics(ticker="AAPL", price=p, target_median=137.0)
        vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(), assessments={})
        assert vm.leaders[0].metrics.target_upside is None


def test_assessment_present_only_for_researched():
    rec = {"business_model_summary": "Chips.", "synthesis": "Cheap-ish AI leader.",
           "thesis": {"bull_case": "AI demand", "bear_case": "Cyclical",
                      "takeaway": "Cheap-ish AI leader.",
                      "what_would_change_my_mind": ["margin compression"]},
           "risks": [{"claim": "China export limits"}],
           "red_flags": [], "management_capital_allocation": "Buybacks"}
    cards = [_card("AAPL", 80.0), _card("MSFT", 70.0)]
    vm = build_view_model(cards, _manifest(), assessments={"AAPL": rec})
    a = {l.ticker: l for l in vm.leaders}
    assert a["AAPL"].assessment.bull_case == "AI demand"
    assert a["AAPL"].assessment.risks == ["China export limits"]
    assert a["AAPL"].assessment.takeaway == "Cheap-ish AI leader."
    assert a["MSFT"].assessment is None


def test_funnel_and_subscores_carried():
    vm = build_view_model([_card("AAPL", 80.0)], _manifest(), assessments={})
    assert vm.funnel.screened == 2
    assert vm.leaders[0].subscores["quality"] == 70
    assert vm.leaders[0].subscores["risk"] is None


def test_masked_derived_from_abstentions_not_data_gaps():
    # A sector-inapplicable subscore (moat) -> masked. A plain data-gap None (risk) -> NOT masked.
    c = _card("BNK", 50.0, moat=None, risk=None,
              abstentions=[{"field": "moat", "reason": "inapplicable", "scope": "subscore"},
                           {"field": "roe", "reason": "inapplicable", "scope": "leg"}])
    vm = build_view_model([c], _manifest(), assessments={})
    assert vm.leaders[0].masked == {"moat"}       # subscore-scope inapplicable only
    assert "risk" not in vm.leaders[0].masked     # data-gap None is not masked
