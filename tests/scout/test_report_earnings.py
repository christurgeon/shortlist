from datetime import date

from shortlist.models import StockMetrics
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, FunnelVM, SignalStatusVM, _metrics_vm)
from shortlist.scout.report.sections import (
    render_html_body, render_text, Detail, _earnings_text)


def _leader(ticker, mvm):
    return LeaderVM(ticker=ticker, name=None, composite=70.0,
                    subscores={"quality": 70}, masked=set(), gates=[], flags=[],
                    confidence=0.8, thin=False, scored=True, coverage_note=None,
                    metrics=mvm, assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("finnhub", True, "")],
                    funnel=FunnelVM(10, 8, 5, len(leaders), 1), notes=[])


def test_metrics_vm_projects_earnings():
    m = StockMetrics(ticker="AAPL", earnings_beats=4, earnings_quarters=4,
                     earnings_avg_surprise_pct=3.7, earnings_days_to_next=18)
    vm = _metrics_vm(m)
    assert vm.earnings_beats == 4 and vm.earnings_quarters == 4
    assert vm.earnings_avg_surprise_pct == 3.7 and vm.earnings_days_to_next == 18


def test_earnings_text_full_and_partial():
    full = MetricsVM(earnings_beats=4, earnings_quarters=4,
                     earnings_avg_surprise_pct=3.7, earnings_days_to_next=18)
    assert _earnings_text(full) == "4/4 beats · +3.7% · next 18d"
    # no avg, no next-date -> just beats
    assert _earnings_text(MetricsVM(earnings_beats=3, earnings_quarters=4)) == "3/4 beats"
    # missing beats -> 0
    assert _earnings_text(MetricsVM(earnings_quarters=2)).startswith("0/2 beats")


def test_earnings_text_none_without_quarters():
    assert _earnings_text(MetricsVM()) is None
    assert _earnings_text(MetricsVM(earnings_quarters=0)) is None   # 0 quarters -> absent


def test_html_and_text_conditional_render():
    full = MetricsVM(earnings_beats=4, earnings_quarters=4,
                     earnings_avg_surprise_pct=3.7, earnings_days_to_next=18)
    body = render_html_body(_vm([_leader("AAPL", full)]))
    assert "Earnings" in body and "4/4 beats · +3.7% · next 18d" in body

    bare = render_html_body(_vm([_leader("X", MetricsVM(pe_ttm=20.0))]))
    assert "Earnings" not in bare

    txt = render_text(_vm([_leader("AAPL", full)]), Detail.FULL)
    assert "Earnings: 4/4 beats · +3.7% · next 18d" in txt
