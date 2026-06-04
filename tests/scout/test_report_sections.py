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


def test_all_none_subscores_render_without_crash():
    nones = {s: None for s in ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]}
    body = render_html_body(_vm([_leader("BNK", 0.0, subs=nones)]))
    txt = render_text(_vm([_leader("BNK", 0.0, subs=nones)]), Detail.FULL)
    assert "BNK" in body and "BNK" in txt
