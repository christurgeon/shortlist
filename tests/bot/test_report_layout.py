"""The /deep HTML layout: the calls first, the decision open, supporting detail one tap away."""
import re
from datetime import date

from shortlist.bot.report.sections import Detail, render_html_body, render_text
from shortlist.bot.report.viewmodel import AssessmentVM, FindingVM, LeaderVM, MetricsVM, ReportVM


def _leader(ticker, comp, assessment=None, flags=None, metrics=None):
    return LeaderVM(ticker=ticker, name=None, composite=comp, subscores={"quality": 70},
                    masked=set(), gates=[], flags=flags or [], confidence=0.8, thin=False,
                    scored=True, coverage_note=None, metrics=metrics or MetricsVM(pe_ttm=30.0),
                    assessment=assessment)


def _vm(*leaders):
    return ReportVM(session=date(2026, 10, 18), leaders=list(leaders), notes=[])


def _call(**over):
    base = dict(call_stance="HOLD", call_label="Hold", call_conviction="MEDIUM",
                call_rationale="heavy_insider_selling overrode the model's Buy.",
                call_model_stance="BUY")
    return AssessmentVM(**{**base, **over})


def _folds(html):
    return re.findall(r'<details class="block fold"><summary>([^<]*)</summary>', html)


def test_the_calls_strip_comes_first_with_each_call_its_comp_and_why():
    body = render_html_body(_vm(_leader("NVDA", 77.4, _call()), _leader("DECK", 78.9,
                                _call(call_stance="BUY", call_label="Buy", call_model_stance="",
                                      call_rationale="Cheap for its growth."))))
    strip = body[:body.index("Shortlist")]
    assert body.index('class="calls"') < body.index('class="board-wrap"')
    assert "NVDA" in strip and "comp 77" in strip and "gate override" in strip
    assert "DECK" in strip and "comp 79" in strip and "Cheap for its growth." in strip
    assert "Screen only — not advice." in strip


def test_no_calls_strip_without_a_call_and_none_in_the_chat_text():
    assert 'class="calls"' not in render_html_body(_vm(_leader("AAPL", 80)))
    assert "Comp ranks names" not in render_text(_vm(_leader("NVDA", 77, _call())), Detail.FULL)


def test_the_decision_stays_open_and_supporting_detail_is_folded_not_removed():
    risks = [FindingVM(claim=f"risk {i}") for i in range(6)]
    a = _call(takeaway="Take.", bull_case="Bull.", bear_case="Bear.", risks=risks,
              red_flags=[FindingVM(claim="going concern")], change_my_mind=["HOKA slows"],
              moat="Brand.", moat_sources=[FindingVM(claim="scale")], business_model="Shoes.",
              capital_allocation="Buybacks.", management_findings=[FindingVM(claim="no debt")],
              reconciliation=[])
    html = render_html_body(_vm(_leader("DECK", 79, a)))
    card = html[html.index("DECK</span> analysis"):]
    risks_fold = card.index('<details class="block fold"><summary>2 more')
    first_card_fold = card.index('<details class="block fold"><summary>Moat')
    for open_text in ("Take.", "Bull.", "Bear.", "going concern", "risk 3"):
        assert card.index(open_text) < risks_fold
    assert risks_fold < card.index("HOKA slows") < first_card_fold
    assert _folds(card) == ["2 more", "Moat", "Business model", "Capital allocation"]
    for folded in ("risk 4", "risk 5", "Brand.", "scale", "Shoes.", "Buybacks.", "no debt"):
        assert folded in card


def test_empty_fundamentals_cells_become_one_no_data_line():
    html = render_html_body(_vm(_leader("FIN", 50, metrics=MetricsVM(pe_ttm=12.0))))
    assert ">12.0<" in html and "No data: Price, Mkt cap, PE 5y med" in html
    assert 'class="v na"' not in html


def test_the_glossary_folds_under_the_flag_chips():
    html = render_html_body(_vm(_leader("GME", 60, flags=["value_trap"])))
    assert html.index("flags-strip") < html.index("Flags &amp; gates in this report")
    assert "Flags &amp; gates in this report" in _folds(html)
    assert html.count('class="sec-label">Flags') == 0


def test_the_stylesheet_carries_no_control_characters():
    # _CSS is a plain Python string: a CSS escape like "\201C" is read as an octal escape and
    # reaches the browser as a control character, which rendered "“” Evidence" as "C D".
    from shortlist.bot.report.html import _CSS
    assert not [c for c in _CSS if ord(c) < 32 and c not in "\n\t"]
