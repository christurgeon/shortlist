from datetime import date
from shortlist.models import ScoreCard, StockMetrics
from shortlist.bot.report.viewmodel import build_view_model


def _card(ticker, comp, **kw):
    base = dict(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                momentum=80, value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _session():
    return date(2026, 6, 4)


def test_leaders_sorted_by_scored_then_composite():
    cards = [_card("LOW", 40.0), _card("HIGH", 90.0),
             _card("NS1", 99.0, scored=False), _card("NS2", 50.0, scored=False)]
    vm = build_view_model(cards, _session(), assessments={})
    assert [ld.ticker for ld in vm.leaders] == ["HIGH", "LOW", "NS1", "NS2"]


def test_target_upside_uses_metrics_property():
    m = StockMetrics(ticker="AAPL", price=100.0, target_median=137.0)
    vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _session(), assessments={})
    assert abs(vm.leaders[0].metrics.target_upside - 0.37) < 1e-6


def test_target_upside_none_for_missing_or_zero_price():
    for p in (None, 0.0):
        m = StockMetrics(ticker="AAPL", price=p, target_median=137.0)
        vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _session(), assessments={})
        assert vm.leaders[0].metrics.target_upside is None


def test_assessment_present_only_for_researched():
    rec = {"business_model_summary": "Chips.", "synthesis": "Cheap-ish AI leader.",
           "thesis": {"bull_case": "AI demand", "bear_case": "Cyclical",
                      "takeaway": "Cheap-ish AI leader.",
                      "what_would_change_my_mind": ["margin compression"]},
           "risks": [{"claim": "China export limits"}],
           "red_flags": [], "management_capital_allocation": "Buybacks"}
    cards = [_card("AAPL", 80.0), _card("MSFT", 70.0)]
    vm = build_view_model(cards, _session(), assessments={"AAPL": rec})
    a = {ld.ticker: ld for ld in vm.leaders}
    assert a["AAPL"].assessment.bull_case == "AI demand"
    assert a["AAPL"].assessment.risks == ["China export limits"]
    assert a["AAPL"].assessment.takeaway == "Cheap-ish AI leader."
    assert a["MSFT"].assessment is None


def test_subscores_carried():
    vm = build_view_model([_card("AAPL", 80.0)], _session(), assessments={})
    assert vm.leaders[0].subscores["quality"] == 70
    assert vm.leaders[0].subscores["risk"] is None


def test_masked_derived_from_abstentions_not_data_gaps():
    # A sector-inapplicable subscore (moat) -> masked. A plain data-gap None (risk) -> NOT masked.
    c = _card("BNK", 50.0, moat=None, risk=None,
              abstentions=[{"field": "moat", "reason": "inapplicable", "scope": "subscore"},
                           {"field": "roe", "reason": "inapplicable", "scope": "leg"}])
    vm = build_view_model([c], _session(), assessments={})
    assert vm.leaders[0].masked == {"moat"}       # subscore-scope inapplicable only
    assert "risk" not in vm.leaders[0].masked     # data-gap None is not masked


# --- /deep handoff must not be circular -------------------------------------------

def _brief(ticker):
    """Minimal assessment record as build_view_model receives it (keyed by ticker)."""
    return {"business_model_summary": f"{ticker} does things.",
            "screening_call": {"stance": "HOLD", "conviction": "MEDIUM"}}


def test_deep_block_excludes_names_already_researched_in_this_report():
    """A /deep LULU report used to end by telling you to run /deep LULU — the command
    that produced it. A name with an assessment in THIS report is already deep-dived."""
    cards = [_card("LULU", 80.0), _card("GEV", 70.0)]
    vm = build_view_model(cards, _session(), assessments={"LULU": _brief("LULU")})
    assert vm.deep_block == ["GEV"]


def test_deep_block_empty_when_every_leader_was_researched():
    """The /deep path: every present name gets researched, so the handoff has nothing
    left to suggest and the section drops out entirely (_DeepBlock.applies)."""
    cards = [_card("LULU", 80.0)]
    vm = build_view_model(cards, _session(), assessments={"LULU": _brief("LULU")})
    assert vm.deep_block == []


def test_deep_block_unchanged_when_nothing_was_researched():
    """The plain /screen path keeps today's behaviour — this is the handoff's whole
    purpose and must not regress."""
    cards = [_card("LULU", 80.0), _card("GEV", 70.0)]
    vm = build_view_model(cards, _session(), assessments={})
    assert vm.deep_block == ["LULU", "GEV"]


def test_deep_block_still_excludes_gated_and_unscored_names():
    """The pre-existing filters survive: a gated or unscored name can't be passed to
    /deep via the handoff regardless of assessments."""
    cards = [_card("OK", 80.0), _card("GATED", 70.0, gates=["negative_fcf"]),
             _card("UNSCORED", 60.0, scored=False)]
    vm = build_view_model(cards, _session(), assessments={})
    assert vm.deep_block == ["OK"]
