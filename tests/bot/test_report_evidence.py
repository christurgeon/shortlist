"""The /deep grounding layer on the bot's report path.

Quotes, per-finding verification state and the two counts reach CLI/file readers
through research/report.py. These pin the same information onto the viewmodel and
the HTML report — the artifact Telegram actually delivers.
"""
from datetime import date

import pytest

from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.sections import Detail, _Research
from shortlist.bot.report.viewmodel import (
    AssessmentVM,
    FindingVM,
    LeaderVM,
    MetricsVM,
    ReconciliationVM,
    ReportVM,
    _assessment_vm,
)

_QUOTE = "our results depend on a small number of customers"


def _rec(**over) -> dict:
    rec = {
        "business_model_summary": "Sells widgets.",
        "moat": {"summary": "Scale."},
        "thesis": {"bull_case": "b", "bear_case": "x",
                   "what_would_change_my_mind": [], "takeaway": "t"},
        "reconciliation": [], "risks": [], "red_flags": [],
        "management_capital_allocation": "",
    }
    rec.update(over)
    return rec


# ---- viewmodel: findings keep their evidence ----

def test_risk_keeps_its_quote_and_verified_status():
    vm = _assessment_vm(_rec(risks=[{"claim": "Customer concentration",
                                     "evidence": _QUOTE, "verified": True,
                                     "source": "10-K Item 1A"}]))
    assert vm.risks == [FindingVM(claim="Customer concentration", evidence=_QUOTE,
                                  source="10-K Item 1A", status="verified")]


def test_quoted_but_unlocatable_risk_is_unverified():
    vm = _assessment_vm(_rec(risks=[{"claim": "c", "evidence": _QUOTE,
                                     "verified": False, "source": ""}]))
    assert vm.risks[0].status == "unverified"


def test_empty_quote_in_moat_sources_is_an_inference_not_a_failure():
    """The one distinction the markdown brief is careful about: an empty quote is a
    legal declared inference in moat.sources / management_findings only."""
    vm = _assessment_vm(_rec(moat={"summary": "Scale.",
                                   "sources": [{"claim": "brand", "evidence": "",
                                                "verified": False}]}))
    assert vm.moat_sources[0].status == "inference"


def test_empty_quote_in_risks_is_unverified_not_an_inference():
    vm = _assessment_vm(_rec(risks=[{"claim": "c", "evidence": "", "verified": False}]))
    assert vm.risks[0].status == "unverified"


def test_finding_predating_the_verified_field_is_unknown_not_unverified():
    """A brief written before verification existed was never checked. Reporting it
    as 'unverified' would assert a failure that never happened."""
    vm = _assessment_vm(_rec(risks=[{"claim": "c", "evidence": _QUOTE}]))
    assert vm.risks[0].status == "unknown"


def test_bare_string_finding_still_parses():
    vm = _assessment_vm(_rec(risks=["a plain claim"]))
    assert vm.risks == [FindingVM(claim="a plain claim", status="unknown")]


def test_added_risks_and_management_findings_reach_the_viewmodel():
    vm = _assessment_vm(_rec(
        added_risks=[{"claim": "new tariff risk", "evidence": _QUOTE, "verified": True}],
        management_findings=[{"claim": "buybacks above IV", "evidence": "", "verified": False}]))
    assert vm.added_risks[0].claim == "new tariff risk"
    assert vm.management_findings[0].status == "inference"


def test_counts_come_from_the_record_not_recomputed():
    vm = _assessment_vm(_rec(unverified_count=3, inference_count=2))
    assert (vm.unverified_count, vm.inference_count) == (3, 2)


def test_reconciliation_keeps_the_filing_quote():
    vm = _assessment_vm(_rec(reconciliation=[
        {"signal": "value", "tension": "cheap vs declining", "filing_says": _QUOTE,
         "verdict": "confirms", "verified": True, "source": "10-K Item 7"}]))
    assert vm.reconciliation == [ReconciliationVM(
        signal="value", tension="cheap vs declining", filing_says=_QUOTE,
        source="10-K Item 7", status="verified")]


def test_silent_reconciliation_is_silent_not_unverified():
    vm = _assessment_vm(_rec(reconciliation=[
        {"signal": "value", "tension": "t", "filing_says": "", "verdict": "silent",
         "verified": False}]))
    assert vm.reconciliation[0].status == "silent"


# ---- HTML: the artifact Telegram delivers ----

def _vm(a: AssessmentVM) -> ReportVM:
    ld = LeaderVM(ticker="X", name=None, composite=70, subscores={}, masked=set(),
                  gates=[], flags=[], confidence=0.8, thin=False, scored=True,
                  coverage_note=None, metrics=MetricsVM(), assessment=a)
    return ReportVM(session=date(2026, 8, 22), leaders=[ld], notes=[])


def _html(a: AssessmentVM) -> str:
    return _Research().render_html(_vm(a), HtmlBuilder())


def _text(a: AssessmentVM) -> list[str]:
    return _Research().render_text(_vm(a), Detail.FULL)


def test_html_carries_the_quote_behind_a_disclosure():
    html = _html(AssessmentVM(risks=[FindingVM(claim="Customer concentration",
                                               evidence=_QUOTE, source="10-K Item 1A",
                                               status="verified")]))
    assert "<details" in html
    assert _QUOTE in html
    assert "10-K Item 1A" in html


def test_unverified_finding_is_marked():
    html = _html(AssessmentVM(risks=[FindingVM(claim="c", evidence=_QUOTE,
                                               status="unverified")]))
    assert "unverified" in html


@pytest.mark.parametrize("status", ["verified", "unknown"])
def test_a_finding_we_cannot_call_a_failure_is_not_marked(status):
    """Mark the exception, not the rule — flagging every item doubles the visual
    weight for no information, and 'unknown' is not a failure at all."""
    html = _html(AssessmentVM(risks=[FindingVM(claim="c", evidence=_QUOTE,
                                               status=status)]))
    assert "unverified" not in html
    assert "no filing quote" not in html


def test_declared_inference_is_marked_as_unquoted_not_as_a_failure():
    html = _html(AssessmentVM(moat_sources=[FindingVM(claim="brand", status="inference")]))
    assert "no filing quote" in html
    assert "unverified" not in html


def test_html_reports_both_counts_with_the_brief_s_wording():
    html = _html(AssessmentVM(unverified_count=2, inference_count=3,
                              risks=[FindingVM(claim="c", status="unverified")]))
    assert "2 claim(s) could not be verified" in html
    assert "3 claim(s) stated without a filing quote" in html


def test_zero_counts_render_nothing():
    html = _html(AssessmentVM(risks=[FindingVM(claim="c", status="verified")]))
    assert "claim(s)" not in html


def test_moat_sources_added_risks_and_management_findings_all_render():
    html = _html(AssessmentVM(
        moat_sources=[FindingVM(claim="switching costs", evidence=_QUOTE, status="verified")],
        added_risks=[FindingVM(claim="new tariff risk", evidence=_QUOTE, status="verified")],
        management_findings=[FindingVM(claim="buybacks above IV", status="inference")]))
    assert "switching costs" in html
    assert "new tariff risk" in html
    assert "buybacks above IV" in html


def test_reconciliation_quote_reaches_the_html():
    html = _html(AssessmentVM(reconciliation=[ReconciliationVM(
        signal="value", tension="cheap vs declining", filing_says=_QUOTE,
        status="verified")]))
    assert "cheap vs declining" in html
    assert _QUOTE in html


# ---- text: GLANCE is where message length binds ----

def test_glance_text_stays_terse():
    lines = _Research().render_text(
        _vm(AssessmentVM(takeaway="t", risks=[FindingVM(claim="c", evidence=_QUOTE,
                                                        status="unverified")])),
        Detail.GLANCE)
    assert _QUOTE not in "\n".join(lines)


def test_full_text_carries_the_quote_and_the_mark():
    lines = _Research().render_text(
        _vm(AssessmentVM(takeaway="t",
                         red_flags=[FindingVM(claim="going concern doubt",
                                              evidence=_QUOTE, status="unverified")])),
        Detail.FULL)
    joined = "\n".join(lines)
    assert _QUOTE in joined
    assert "unverified" in joined


def test_stub_sections_come_from_the_record():
    vm = _assessment_vm(_rec(stub_sections=["Item 7 (MD&A)"]))
    assert vm.stub_sections == ["Item 7 (MD&A)"]
    assert _assessment_vm(_rec()).stub_sections == []


def test_html_names_stub_sections_with_the_brief_s_wording():
    from shortlist.research.models import stub_sections_note
    html = _html(AssessmentVM(stub_sections=["Item 7 (MD&A)"],
                              risks=[FindingVM(claim="c", status="verified")]))
    assert HtmlBuilder().esc(stub_sections_note(["Item 7 (MD&A)"])) in html


# ---- the deterministic "ROIC persistence" section ----

_PERSISTENCE = ("Historical frequencies for an accounting ratio. Not a forecast <for> this company.\n"
                "- This company: ROIC 31.2% & above the cutoff.\n"
                "- Past cohorts: 59% and 54% were still in the top fifth.")


def test_the_persistence_section_reaches_the_viewmodel_from_the_record():
    vm = _assessment_vm(_rec(durability_line=_PERSISTENCE, durability_status="shown"))
    assert (vm.roic_persistence, vm.roic_persistence_shown) == (_PERSISTENCE, True)
    old = _assessment_vm(_rec())                       # a brief from before the section existed
    assert (old.roic_persistence, old.roic_persistence_shown) == ("", False)


def test_the_persistence_section_is_in_the_html_report_word_for_word_and_escaped():
    html = _html(AssessmentVM(roic_persistence=_PERSISTENCE, roic_persistence_shown=True))
    assert "ROIC persistence (computed from SEC data — not LLM-generated, not filing text)" in html
    assert "Not a forecast &lt;for&gt; this company." in html and "<for>" not in html
    assert "<li>This company: ROIC 31.2% &amp; above the cutoff.</li>" in html
    assert "<li>Past cohorts: 59% and 54% were still in the top fifth.</li>" in html
    assert "ROIC persistence" not in _html(AssessmentVM())


def test_the_chat_message_points_to_the_report_and_never_shortens_the_reading():
    shown = _text(AssessmentVM(roic_persistence=_PERSISTENCE, roic_persistence_shown=True))
    (line,) = [ln for ln in shown if "ROIC persistence" in ln]
    assert line == ("   📏 ROIC persistence (a historical pattern in a ratio, not a forecast): this "
                    "company's ROIC is in the top fifth on the study's basis — the history and its "
                    "limits are in the report")
    assert "59%" not in line and "31.2%" not in line
    from shortlist.durability_profile import SECTOR_NOT_COVERED, UNAVAILABLE
    from shortlist.research.durability import not_shown
    (line,) = [ln for ln in _text(AssessmentVM(roic_persistence=not_shown(SECTOR_NOT_COVERED)))
               if "ROIC persistence" in ln]
    assert line == ("   📏 ROIC persistence: not shown: banks, lenders, brokers, insurers and REITs are "
                    "outside the study.")
    (line,) = [ln for ln in _text(AssessmentVM(roic_persistence=not_shown(UNAVAILABLE)))
               if "ROIC persistence" in ln]
    assert line == "   📏 ROIC persistence: not shown: SEC data could not be read."
    assert not [ln for ln in _text(AssessmentVM()) if "ROIC persistence" in ln]
