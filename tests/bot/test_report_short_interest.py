from datetime import date

from shortlist.models import StockMetrics
from shortlist.bot.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, FunnelVM, SignalStatusVM, _metrics_vm)
from shortlist.bot.report.sections import (
    render_html_body, render_text, Detail, _short_interest_text)


def _leader(ticker, mvm):
    return LeaderVM(ticker=ticker, name=None, composite=70.0,
                    subscores={"quality": 70}, masked=set(), gates=[], flags=[],
                    confidence=0.8, thin=False, scored=True, coverage_note=None,
                    metrics=mvm, assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("finra", True, "")],
                    funnel=FunnelVM(10, 8, 5, len(leaders), 1), notes=[])


# --- _metrics_vm projection ---

def test_metrics_vm_projects_short_interest():
    m = StockMetrics(ticker="GME", short_pct_outstanding=0.224, days_to_cover=8.1,
                     short_interest_rising=True)
    vm = _metrics_vm(m)
    assert vm.short_pct_outstanding == 0.224
    assert vm.days_to_cover == 8.1
    assert vm.short_interest_rising is True


def test_metrics_vm_default_is_none():
    vm = MetricsVM()
    assert vm.short_pct_outstanding is None and vm.days_to_cover is None


# --- _short_interest_text formatting ---

def test_text_formats_pct_days_and_rising_arrow():
    vm = MetricsVM(short_pct_outstanding=0.224, days_to_cover=8.1, short_interest_rising=True)
    assert _short_interest_text(vm) == "22.4% / 8.1d ↑"


def test_text_omits_days_when_none_and_arrow_when_not_rising():
    assert _short_interest_text(MetricsVM(short_pct_outstanding=0.06)) == "6.0%"
    assert _short_interest_text(
        MetricsVM(short_pct_outstanding=0.06, days_to_cover=2.0,
                  short_interest_rising=False)) == "6.0% / 2.0d"


def test_text_none_when_no_short_pct():
    assert _short_interest_text(MetricsVM()) is None
    assert _short_interest_text(MetricsVM(days_to_cover=5.0)) is None   # need the % to render


def test_immaterial_short_interest_is_hidden():
    # FINRA covers most names at ~1% — below the 5% materiality floor it's noise.
    assert _short_interest_text(MetricsVM(short_pct_outstanding=0.01, days_to_cover=3.0)) is None
    assert _short_interest_text(MetricsVM(short_pct_outstanding=0.05)) == "5.0%"   # at the floor, shows


# --- rendering (conditional) ---

def test_html_shows_short_interest_only_when_present():
    crowded = MetricsVM(short_pct_outstanding=0.224, days_to_cover=8.1, short_interest_rising=True)
    body = render_html_body(_vm([_leader("GME", crowded)]))
    assert "Short interest" in body and "22.4% / 8.1d ↑" in body   # full string incl. arrow survives escape

    plain = render_html_body(_vm([_leader("AAPL", MetricsVM(pe_ttm=30.0))]))
    assert "Short interest" not in plain
    # immaterial (1%) -> hidden in the rendered body too
    low = render_html_body(_vm([_leader("AAPL", MetricsVM(short_pct_outstanding=0.01))]))
    assert "Short interest" not in low


def test_end_to_end_stockmetrics_to_rendered_html():
    # Guards the _metrics_vm field-name mapping all the way to the rendered body.
    m = StockMetrics(ticker="BYND", short_pct_outstanding=0.274, days_to_cover=4.5,
                     short_interest_rising=True)
    body = render_html_body(_vm([_leader("BYND", _metrics_vm(m))]))
    assert "27.4% / 4.5d ↑" in body


def test_text_full_shows_short_interest_only_when_present():
    crowded = MetricsVM(short_pct_outstanding=0.18, days_to_cover=6.0)
    txt = render_text(_vm([_leader("GME", crowded)]), Detail.FULL)
    assert "Short interest: 18.0% / 6.0d" in txt          # render_text returns a string

    plain = render_text(_vm([_leader("AAPL", MetricsVM(pe_ttm=30.0))]), Detail.FULL)
    assert "Short interest" not in plain
