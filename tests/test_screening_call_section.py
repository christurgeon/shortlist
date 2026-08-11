from datetime import date
from shortlist.bot.report.sections import _Research, Detail
from shortlist.bot.report.viewmodel import AssessmentVM, LeaderVM, MetricsVM, ReportVM
from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.theme import stance_emoji


def _leader():
    a = AssessmentVM(takeaway="t", call_stance="BUY", call_label="Buy",
                     call_conviction="MEDIUM", call_rationale="Durable.",
                     call_watch="margins compress",
                     call_decided_without=["value axis — FMP gated this symbol (402)"])
    return LeaderVM(ticker="X", name=None, composite=70, subscores={}, masked=set(),
                    gates=[], flags=[], confidence=0.8, thin=False, scored=True,
                    coverage_note=None, metrics=MetricsVM(), assessment=a)


def _vm():
    return ReportVM(session=date(2026, 6, 7), leaders=[_leader()], notes=[])


def test_html_has_pill_and_disclaimer():
    html = _Research().render_html(_vm(), HtmlBuilder())
    assert "Buy" in html
    assert "not advice" in html
    assert "margins compress" in html


def test_text_has_emoji_call_line():
    lines = _Research().render_text(_vm(), Detail.GLANCE)
    joined = "\n".join(lines)
    assert stance_emoji("BUY") in joined
    assert "Buy" in joined


def test_text_full_with_call_does_not_crash():
    lines = _Research().render_text(_vm(), Detail.FULL)
    joined = "\n".join(lines)
    assert "Buy" in joined          # call line present
    assert "   t" in joined         # takeaway still appended under FULL (no UnboundLocalError)
