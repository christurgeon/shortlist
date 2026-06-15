from datetime import date

from shortlist.models import StockMetrics
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, FunnelVM, SignalStatusVM, _metrics_vm)
from shortlist.scout.report.sections import (
    render_html_body, render_text, Detail, _piotroski_text, _FUND_ROWS)


def _leader(ticker, mvm):
    return LeaderVM(ticker=ticker, name=None, composite=70.0,
                    subscores={"quality": 70}, masked=set(), gates=[], flags=[],
                    confidence=0.8, thin=False, scored=True, coverage_note=None,
                    metrics=mvm, assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("edgar", True, "")],
                    funnel=FunnelVM(10, 8, 5, len(leaders), 1), notes=[])


# --- projection ---

def test_metrics_vm_projects_piotroski_and_floored_leverage():
    m = StockMetrics(ticker="X", piotroski_f=5, piotroski_f_legs=6, net_debt_to_ebitda=2.53)
    vm = _metrics_vm(m)
    assert vm.piotroski_f == 5 and vm.piotroski_f_legs == 6
    assert vm.net_debt_to_ebitda == 2.53


def test_net_debt_floors_net_cash_to_zero():
    vm = _metrics_vm(StockMetrics(ticker="X", net_debt_to_ebitda=-1.5))   # net cash
    assert vm.net_debt_to_ebitda == 0.0
    assert _metrics_vm(StockMetrics(ticker="X")).net_debt_to_ebitda is None


# --- piotroski formatter ---

def test_piotroski_text():
    assert _piotroski_text(MetricsVM(piotroski_f=5, piotroski_f_legs=6)) == "5/6"
    assert _piotroski_text(MetricsVM(piotroski_f=1, piotroski_f_legs=2)) == "1/2"   # partial coverage
    assert _piotroski_text(MetricsVM(piotroski_f=0, piotroski_f_legs=4)) == "0/4"   # zero won still renders
    assert _piotroski_text(MetricsVM(piotroski_f=4)) == "4/6"   # legs default to 6
    assert _piotroski_text(MetricsVM()) is None


def test_zero_won_piotroski_still_renders_in_html():
    body = render_html_body(_vm([_leader("X", MetricsVM(piotroski_f=0, piotroski_f_legs=5))]))
    assert "Piotroski" in body and ">0/5<" in body   # the `if pio:` guard must not drop "0/5"


# --- rendering ---

def test_net_debt_is_a_fund_row():
    assert any(attr == "net_debt_to_ebitda" for _, attr, _ in _FUND_ROWS)


def test_html_renders_leverage_and_conditional_piotroski():
    full = MetricsVM(piotroski_f=5, piotroski_f_legs=6, net_debt_to_ebitda=2.5)
    body = render_html_body(_vm([_leader("LMT", full)]))
    assert "Net debt/EBITDA" in body and ">2.5<" in body
    assert "Piotroski" in body and ">5/6<" in body

    # Piotroski is conditional; leverage row always present (shows "·" when absent)
    bare = render_html_body(_vm([_leader("FIN", MetricsVM(pe_ttm=12.0))]))
    assert "Piotroski" not in bare
    assert "Net debt/EBITDA" in bare           # uniform row, value "·"


def test_text_full_renders_both():
    full = MetricsVM(piotroski_f=6, piotroski_f_legs=6, net_debt_to_ebitda=0.0)
    txt = render_text(_vm([_leader("KO", full)]), Detail.FULL)
    assert "Net debt/EBITDA: 0.0" in txt
    assert "Piotroski: 6/6" in txt
