"""The "Recent news" section on the bot's report path: the HTML attachment and the chat text."""
from datetime import date

from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.sections import Detail, _Research
from shortlist.bot.report.viewmodel import (AssessmentVM, LeaderVM, MetricsVM, NewsVM, ReportVM,
                                            _assessment_vm)
from shortlist.research import news


def _rec(**over) -> dict:
    rec = {"business_model_summary": "Sells widgets.", "moat": {"summary": "Scale."},
           "thesis": {"bull_case": "b", "bear_case": "x", "what_would_change_my_mind": [],
                      "takeaway": "t"},
           "reconciliation": [], "risks": [], "red_flags": [], "management_capital_allocation": ""}
    rec.update(over)
    return rec


def _vm(a: AssessmentVM) -> ReportVM:
    ld = LeaderVM(ticker="X", name=None, composite=70, subscores={}, masked=set(), gates=[],
                  flags=[], confidence=0.8, thin=False, scored=True, coverage_note=None,
                  metrics=MetricsVM(), assessment=a)
    return ReportVM(session=date(2026, 10, 16), leaders=[ld], notes=[])


def _html(a):
    return _Research().render_html(_vm(a), HtmlBuilder())


def _text(a):
    return _Research().render_text(_vm(a), Detail.FULL)


_ITEM = {"date": "2026-10-15", "source": "Benzinga", "headline": "Acme <sued> by FTC",
         "summary": "The FTC & partners allege.", "url": "https://x.test/1?a=1&b=2", "reports": 3}


def test_the_section_reaches_the_viewmodel_and_old_briefs_read_as_off():
    vm = _assessment_vm(_rec(news_items=[_ITEM], news_status="shown", news_as_of="2026-10-16 14:02 UTC",
                             news_from="2026-09-16", news_truncated=True))
    assert vm.news == [NewsVM(**_ITEM)] and vm.news_status == "shown"
    assert (vm.news_from, vm.news_truncated) == ("2026-09-16", True)
    old = _assessment_vm(_rec())
    assert (old.news, old.news_status, old.news_as_of, old.news_from, old.news_truncated) == (
        [], "", "", "", False)


def test_a_non_http_link_never_reaches_the_report():
    vm = _assessment_vm(_rec(news_items=[{**_ITEM, "url": "javascript:alert(1)"}, "junk"],
                             news_status="shown"))
    assert [n.url for n in vm.news] == [""]


def test_the_html_section_is_escaped_and_linked():
    html = _html(AssessmentVM(news=[NewsVM(**_ITEM)], news_status="shown", news_as_of="t",
                              news_from="2026-09-16", news_truncated=True))
    assert news.HEADING in html and "from 2026-09-16 to t" in html and news.TRUNCATED_TEXT in html
    assert '<a href="https://x.test/1?a=1&amp;b=2">Acme &lt;sued&gt; by FTC</a>' in html
    assert "2026-10-15 · Benzinga · 3 reports — " in html and "<sued>" not in html
    assert news.HEADING not in _html(AssessmentVM())


def test_news_headlines_are_underlined_so_they_read_as_links():
    from shortlist.bot.report.html import _CSS
    html = _html(AssessmentVM(news=[NewsVM(**_ITEM)], news_status="shown"))
    assert 'class="block news"' in html
    assert ".block.news a{text-decoration:underline" in _CSS


def test_unavailable_and_none_render_their_own_sentences():
    assert news.UNAVAILABLE_TEXT in _html(AssessmentVM(news_status="unavailable", news_as_of="t"))
    assert news.NONE_TEXT in _html(AssessmentVM(news_status="none", news_as_of="t"))


def test_the_chat_text_lists_items_at_full_detail_only():
    a = AssessmentVM(news=[NewsVM(**_ITEM)], news_status="shown", call_stance="HOLD",
                     call_label="Hold", call_conviction="LOW")
    lines = _text(a)
    i = lines.index(f"   📰 {news.HEADING}:")
    assert lines[i + 1] == "      2026-10-15 · Acme <sued> by FTC (3 reports)"
    assert not any(news.HEADING in ln for ln in _text(AssessmentVM(news_status="none")))
    assert not any("📰" in ln for ln in _Research().render_text(_vm(a), Detail.GLANCE))
    assert _text(AssessmentVM(news_status="unavailable"))[-1] == \
        "   📰 News: the feed was unavailable for this brief."
