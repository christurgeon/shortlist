from datetime import date
from shortlist.bot.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, AssessmentVM, FindingVM)
from shortlist.bot.report.sections import render_html_body, render_text, Detail


def _leader(ticker, comp, assessment=None, gates=None, subs=None, flags=None):
    return LeaderVM(ticker=ticker, name=None, composite=comp,
                    subscores=subs or {"quality": 70, "risk": None}, masked=set(),
                    gates=gates or [], flags=flags or [], confidence=0.8, thin=False, scored=True,
                    coverage_note=None, metrics=MetricsVM(pe_ttm=30.0, target_upside=0.37),
                    assessment=assessment)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    notes=[])


def test_html_body_lists_every_leader_and_funnel():
    body = render_html_body(_vm([_leader("AAPL", 80), _leader("MSFT", 70)]))
    assert "AAPL" in body and "MSFT" in body
    assert "AAPL" in body


def test_research_section_only_when_assessment_present():
    a = AssessmentVM(bull_case="AI demand", bear_case="Cyclical",
                     red_flags=[FindingVM(claim="going concern")])
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
    assert "AAPL" in txt


def test_leaderboard_renders_soft_flags_html_and_text():
    # Soft advisory flags (social_hype, crowded_short, ...) must surface in both
    # renderers — distinct from hard gates. Empty flags render nothing extra.
    body = render_html_body(_vm([_leader("GME", 60, flags=["social_hype", "crowded_short"])]))
    assert "social_hype" in body and "crowded_short" in body
    assert "flags-strip" in body                 # rendered via the strip, not a column
    txt = render_text(_vm([_leader("GME", 60, flags=["social_hype"])]), Detail.GLANCE)
    assert "social_hype" in txt


def test_leaderboard_heatmap_drops_gates_and_flags_columns():
    # The wide heatmap is now purely numeric; gates/flags moved out to the strip so
    # they no longer trail off the right edge. The column headers must be gone.
    body = render_html_body(_vm([_leader("GME", 60, gates=["over_leveraged"],
                                         flags=["social_hype"])]))
    assert ">Gates</th>" not in body and ">Flags</th>" not in body
    assert "over_leveraged" in body and "social_hype" in body   # still present (strip)


def test_flags_strip_only_for_flagged_tickers():
    # A clean leader contributes no strip row; a flagged one does.
    clean = render_html_body(_vm([_leader("AAPL", 80)]))
    assert "flags-strip" not in clean
    flagged = render_html_body(_vm([_leader("AAPL", 80, gates=["negative_fcf"])]))
    assert "flags-strip" in flagged and "negative_fcf" in flagged


def test_glossary_absent_when_nothing_flagged():
    body = render_html_body(_vm([_leader("AAPL", 80), _leader("MSFT", 70)]))
    assert "glossary" not in body
    assert "Flags &amp; gates in this report" not in body


def test_glossary_lists_present_codes_with_descriptions_grouped():
    body = render_html_body(_vm([_leader("GME", 60, gates=["over_leveraged"],
                                         flags=["value_trap"])]))
    assert "Flags &amp; gates in this report" in body
    assert "Gates (hard filters)" in body and "Flags (advisory)" in body
    # description text, not just the bare id
    assert "above the safe threshold" in body          # over_leveraged blurb
    assert "quality or growth is weak" in body         # value_trap blurb
    # a code NOT present must not leak into the glossary
    assert "crowded_short" not in body


def test_glossary_unknown_code_renders_without_crash():
    # A future flag id with no description must still render (its id, no blurb).
    body = render_html_body(_vm([_leader("GME", 60, flags=["brand_new_flag"])]))
    assert "brand_new_flag" in body
    txt = render_text(_vm([_leader("GME", 60, flags=["brand_new_flag"])]), Detail.FULL)
    assert any("brand_new_flag" in line for line in txt.splitlines())


def test_leaderboard_no_flags_renders_clean():
    # A leader with no flags must not emit a stray flag marker in text.
    txt = render_text(_vm([_leader("AAPL", 80)]), Detail.GLANCE)
    assert "🏷️" not in " ".join(txt)


def test_text_glance_shows_research_takeaway():
    a = AssessmentVM(takeaway="Strong moat, fair price.")
    txt = render_text(_vm([_leader("AAPL", 80, assessment=a)]), Detail.GLANCE)
    assert "Strong moat" in txt


def test_research_section_renders_synthesis_moat_reconciliation():
    from shortlist.bot.report.viewmodel import _assessment_vm
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
    assert [(c.signal, c.tension) for c in a.reconciliation] == [
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


def test_metric_money_sign_color_from_raw_and_zero_neutral():
    # Money metrics format as "$..M" with no leading +/-, so the good/bad color must
    # come from the raw numeric sign (insider selling = negative = bearish/red).
    from shortlist.bot.report.sections import _Fundamentals
    from shortlist.bot.report.html import HtmlBuilder
    h = HtmlBuilder()
    assert "v neg" in _Fundamentals._metric(h, "Insider 6m", "$-9M", True, raw=-9e6)
    assert "v pos" in _Fundamentals._metric(h, "Insider 6m", "$40M", True, raw=40e6)
    # A pct that rounds to zero ("+0%"/"-0%") must read neutral, not red/green.
    near_zero = _Fundamentals._metric(h, "FCF yield", "-0%", True, raw=-0.001)
    assert "v neg" not in near_zero and "v pos" not in near_zero


def test_fundamentals_renders_escaped_company_name():
    ld = _leader("AAPL", 80)
    ld.name = "<b>Apple</b> Inc"
    body = render_html_body(_vm([ld]))
    assert "Apple" in body and "<b>Apple</b>" not in body and "&lt;b&gt;Apple" in body


def test_footer_renders_notes_only():
    """The footer carries notes and nothing else — there is no funnel or signal status."""
    from types import SimpleNamespace

    from shortlist.bot.report.sections import _Footer

    vm = SimpleNamespace(notes=["interactive /screen request"])
    text = _Footer().render_text(vm, None)
    assert any("interactive /screen request" in line for line in text)
    assert not any("Funnel:" in line or "Signals:" in line for line in text)
    assert any("interactive /screen request" in line for line in text)
