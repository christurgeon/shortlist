"""The computed capital-allocation section on the Telegram report path."""
from datetime import date

from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.sections import Detail, _Research
from shortlist.bot.report.viewmodel import (
    AssessmentVM,
    LeaderVM,
    MetricsVM,
    ReportVM,
    _assessment_vm,
)
from shortlist.research.capital_allocation import HEADING, NOT_SHOWN_SECTOR

_BODY = ("Accounting data from SEC filings over FY2023–FY2025 (3 fiscal years). Not <a> judgement.\n"
         "- ROIC: FY2023 12.5% & FY2025 15.0%.\n"
         "- Basis: ROIC is operating income × (1 − 25%).")


def _rec(**over) -> dict:
    rec = {"thesis": {"bull_case": "b", "bear_case": "x", "what_would_change_my_mind": [],
                      "takeaway": "t"}, "management_capital_allocation": ""}
    rec.update(over)
    return rec


def _vm(a: AssessmentVM) -> ReportVM:
    ld = LeaderVM(ticker="X", name=None, composite=70, subscores={}, masked=set(),
                  gates=[], flags=[], confidence=0.8, thin=False, scored=True,
                  coverage_note=None, metrics=MetricsVM(), assessment=a)
    return ReportVM(session=date(2026, 10, 21), leaders=[ld], notes=[])


def test_the_section_reaches_the_viewmodel_from_the_record():
    vm = _assessment_vm(_rec(capital_allocation_line=_BODY, capital_allocation_status="shown"))
    assert (vm.capital_allocation_computed, vm.capital_allocation_computed_shown) == (_BODY, True)
    old = _assessment_vm(_rec())          # a brief from before the section existed
    assert (old.capital_allocation_computed, old.capital_allocation_computed_shown) == ("", False)


def test_the_html_report_folds_the_section_word_for_word_and_escaped():
    html = _Research().render_html(
        _vm(AssessmentVM(capital_allocation_computed=_BODY, capital_allocation_computed_shown=True)),
        HtmlBuilder())
    assert f"<summary>{HEADING}</summary>" in html
    assert "Not &lt;a&gt; judgement." in html and "<a>" not in html
    assert "<li>ROIC: FY2023 12.5% &amp; FY2025 15.0%.</li>" in html
    assert HEADING not in _Research().render_html(_vm(AssessmentVM()), HtmlBuilder())


def test_the_chat_message_points_to_the_report():
    def lines(a):
        return [ln for ln in _Research().render_text(_vm(a), Detail.FULL)
                if "Capital allocation" in ln]

    (line,) = lines(AssessmentVM(capital_allocation_computed=_BODY,
                                 capital_allocation_computed_shown=True))
    assert line == ("   💵 Capital allocation (computed from SEC data): cash flow, payouts, "
                    "share count and ROIC are in the report")
    assert "12.5%" not in line
    (line,) = lines(AssessmentVM(capital_allocation_computed=NOT_SHOWN_SECTOR))
    assert line == "   💵 Capital allocation: not shown: not meaningful for this sector."
    assert not lines(AssessmentVM())
