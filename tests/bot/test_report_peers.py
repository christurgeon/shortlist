"""The "Peer comparison" section on the bot's report path: the HTML attachment and the chat text."""
import dataclasses
from datetime import date

from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.sections import Detail, _Research
from shortlist.bot.report.viewmodel import AssessmentVM, LeaderVM, MetricsVM, ReportVM, _assessment_vm
from shortlist.research import peers
from shortlist.research.models import PeerRow, PeerTable

TABLE = PeerTable(status=peers.SHOWN, as_of="2026-11-02 14:00 UTC", ticker="A&B",
                  basis="Peers <from> Finnhub.", peers=["AAA", "<BBB>"],
                  rows=[PeerRow("P/E (TTM)", "<15.0x>", "20.0x", 3, "lowest of 4"),
                        PeerRow("ROI (TTM)", "—", "1.0%", 2, "")],
                  masked=["EV/EBITDA (TTM)"], relative="rel <b>", growth="growth & co")


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
    return ReportVM(session=date(2026, 11, 2), leaders=[ld], notes=[])


def _html(a):
    return _Research().render_html(_vm(a), HtmlBuilder())


def test_the_section_reaches_the_viewmodel_and_old_briefs_read_as_off():
    vm = _assessment_vm(_rec(peer_table=dataclasses.asdict(TABLE)))
    assert vm.peers.rows == TABLE.rows and vm.peers.ticker == "A&B"
    assert _assessment_vm(_rec()).peers is None
    assert _assessment_vm(_rec(peer_table=None)).peers is None
    assert _assessment_vm(_rec(peer_table="junk")).peers is None


def test_an_old_brief_renders_without_the_section():
    old = _assessment_vm(_rec())
    assert peers.HEADING not in _html(old)
    assert not any("🧭" in ln for ln in _Research().render_text(_vm(old), Detail.FULL))


def test_the_html_table_is_escaped():
    html = _html(AssessmentVM(peers=TABLE))
    assert peers.HEADING in html and 'class="peers"' in html
    assert "<th>A&amp;B</th>" in html and "<td>&lt;15.0x&gt;</td>" in html
    assert "<td>20.0x (3)</td>" in html and "<td>—</td>" in html
    assert "Peers &lt;from&gt; Finnhub." in html and "Peers: AAA, &lt;BBB&gt;." in html
    assert "<b>Relative performance:</b> rel &lt;b&gt;" in html and "growth &amp; co" in html
    assert "Not meaningful for this sector, so not shown: EV/EBITDA (TTM)." in html
    for raw in ("<BBB>", "<15.0x>", "rel <b>", "<from>"):
        assert raw not in html


def test_not_shown_renders_its_sentence_and_no_table():
    html = _html(AssessmentVM(peers=PeerTable(peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT, "t")))
    assert HtmlBuilder().esc(peers.UNAVAILABLE_TEXT) in html and "<table" not in html


def test_the_chat_text_at_full_detail_only():
    a = AssessmentVM(peers=TABLE, call_stance="HOLD", call_label="Hold", call_conviction="LOW")
    lines = _Research().render_text(_vm(a), Detail.FULL)
    i = next(n for n, ln in enumerate(lines) if "🧭" in ln)
    assert lines[i + 1:i + 3] == ["      rel <b>", "      growth & co"]
    assert not any("🧭" in ln for ln in _Research().render_text(_vm(a), Detail.GLANCE))
