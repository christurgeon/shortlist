from datetime import date
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, AssessmentVM, FunnelVM, SignalStatusVM)
from shortlist.scout.report.sections import render_html_body, render_text, Detail


def _leader(ticker, comp, assessment=None, gates=None, subs=None):
    return LeaderVM(ticker=ticker, name=None, composite=comp,
                    subscores=subs or {"quality": 70, "risk": None}, masked=set(),
                    gates=gates or [], flags=[], confidence=0.8, thin=False, scored=True,
                    coverage_note=None, metrics=MetricsVM(pe_ttm=30.0, target_upside=0.37),
                    assessment=assessment)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("edgar_form4", True, "2 clusters")],
                    funnel=FunnelVM(10, 8, 5, len(leaders), 1), notes=[])


def test_html_body_lists_every_leader_and_funnel():
    body = render_html_body(_vm([_leader("AAPL", 80), _leader("MSFT", 70)]))
    assert "AAPL" in body and "MSFT" in body
    assert "screened" in body and "edgar_form4" in body


def test_research_section_only_when_assessment_present():
    a = AssessmentVM(bull_case="AI demand", bear_case="Cyclical", red_flags=["going concern"])
    with_res = render_html_body(_vm([_leader("AAPL", 80, assessment=a)]))
    assert "AI demand" in with_res and "going concern" in with_res
    no_res = render_html_body(_vm([_leader("AAPL", 80)]))
    assert "AI demand" not in no_res


def test_html_escapes_injected_text_in_prose_and_ticker():
    a = AssessmentVM(bull_case="<script>alert(1)</script>")
    body = render_html_body(_vm([_leader("<b>AAPL</b>", 80, assessment=a)]))
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    assert "<b>AAPL</b>" not in body and "&lt;b&gt;AAPL" in body


def test_text_glance_has_substring_contract():
    txt = render_text(_vm([_leader("AAPL", 80, gates=["negative_fcf"])]), Detail.GLANCE)
    assert "AAPL" in txt and "80" in txt
    assert "negative_fcf" in txt
    assert "screened" in txt and "edgar_form4" in txt


def test_text_glance_shows_research_takeaway():
    a = AssessmentVM(takeaway="Strong moat, fair price.")
    txt = render_text(_vm([_leader("AAPL", 80, assessment=a)]), Detail.GLANCE)
    assert "Strong moat" in txt


def test_research_section_renders_synthesis_moat_reconciliation():
    from shortlist.scout.report.viewmodel import _assessment_vm
    # On-disk JSON record shape (verified): synthesis is a top-level key,
    # moat.summary holds the prose, reconciliation is a list of {signal, tension}.
    rec = {
        "synthesis": "NVIDIA is the most critical AI infra provider with a widening moat.",
        "moat": {"summary": "CUDA ecosystem lock-in across 7.5M+ developers."},
        "reconciliation": [
            {"signal": "quality",
             "tension": "Quality score of 70 looks generous given 390bps margin compression."}],
        "thesis": {"bull_case": "AI demand", "bear_case": "Cyclical",
                   "what_would_change_my_mind": []},
        "business_model_summary": "Fabless AI infra.", "risks": [], "red_flags": [],
        "management_capital_allocation": "",
    }
    a = _assessment_vm(rec)
    assert a.takeaway == "NVIDIA is the most critical AI infra provider with a widening moat."
    assert a.moat == "CUDA ecosystem lock-in across 7.5M+ developers."
    assert a.reconciliation == [
        ("quality", "Quality score of 70 looks generous given 390bps margin compression.")]
    body = render_html_body(_vm([_leader("NVDA", 78, assessment=a)]))
    assert "NVIDIA is the most critical AI infra" in body      # synthesis surfaced
    assert "CUDA ecosystem lock-in" in body                    # moat summary
    assert "Quality score of 70 looks generous" in body        # reconciliation tension
    txt = render_text(_vm([_leader("NVDA", 78, assessment=a)]), Detail.FULL)
    assert "NVIDIA is the most critical AI infra" in txt
    assert "Quality score of 70 looks generous" in txt


def test_all_none_subscores_render_without_crash():
    nones = dict.fromkeys(["quality", "moat", "growth", "value", "momentum", "insider", "risk"])
    body = render_html_body(_vm([_leader("BNK", 0.0, subs=nones)]))
    txt = render_text(_vm([_leader("BNK", 0.0, subs=nones)]), Detail.FULL)
    assert "BNK" in body and "BNK" in txt


def test_fundamentals_renders_escaped_company_name():
    ld = _leader("AAPL", 80)
    ld.name = "<b>Apple</b> Inc"
    body = render_html_body(_vm([ld]))
    assert "Apple" in body and "<b>Apple</b>" not in body and "&lt;b&gt;Apple" in body


def test_footer_omits_funnel_when_no_signals():
    # Interactive reports set manifest.signals=[] as the marker. The funnel line
    # ("0 deduped … dropped: budget") is meaningless there and must be suppressed;
    # notes must still render.
    from shortlist.scout.report.sections import _Footer
    from types import SimpleNamespace

    vm = SimpleNamespace(
        signals=[],
        funnel=SimpleNamespace(raw=3, after_dedup=3, after_prefilter=3,
                               screened=3, dropped_for_budget=0),
        notes=["interactive /screen request"],
    )
    text = _Footer().render_text(vm, None)
    assert not any("Funnel:" in line for line in text)
    assert not any("Signals:" in line for line in text)
    assert any("interactive /screen request" in line for line in text)
