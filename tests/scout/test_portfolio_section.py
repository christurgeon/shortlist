from datetime import date

from shortlist.scout.report.viewmodel import ReportVM, build_view_model
from shortlist.scout.report import build_report
from shortlist.scout.models import RunManifest


def _manifest():
    return RunManifest(session=date(2026, 6, 12), signals=[], raw=0, after_dedup=0,
                       after_prefilter=0, screened=0, dropped_for_budget=0,
                       researched=[], notes=[])


def test_reportvm_defaults_portfolio_none():
    vm = ReportVM(session=date(2026, 6, 12), leaders=[], signals=[],
                  funnel=None, notes=[])
    assert vm.portfolio is None


def test_build_view_model_threads_portfolio():
    sentinel = object()
    vm = build_view_model([], _manifest(), assessments={}, portfolio=sentinel)
    assert vm.portfolio is sentinel


def test_build_report_threads_portfolio():
    sentinel = object()
    art = build_report([], _manifest(), assessments={}, portfolio=sentinel)
    art2 = build_report([], _manifest(), assessments={})
    assert art.html is not None and art2.html is not None


from types import SimpleNamespace

from shortlist.scout.report.sections import _Portfolio, render_html_body, render_text, Detail
from shortlist.scout.report.viewmodel import FunnelVM


def _vm(portfolio):
    return ReportVM(session=date(2026, 6, 12), leaders=[], signals=[],
                    funnel=FunnelVM(0, 0, 0, 0, 0), notes=[], portfolio=portfolio)


def _pos(ticker, *, weight=None, composite=50.0, gates=(), flags=(), scored=True,
         no_data=False, sic="unknown"):
    card = None if no_data else SimpleNamespace(
        composite=composite, gates=list(gates), flags=list(flags), scored=scored,
        sic_bucket=sic)
    return SimpleNamespace(ticker=ticker, shares=1, price=None if weight is None else 1.0,
                           value=None, weight=weight, card=card, no_data=no_data)


def _summary(positions, **kw):
    alerts = [p for p in positions if p.no_data or (p.card and (p.card.gates or p.card.flags
              or not p.card.scored))]
    return SimpleNamespace(positions=positions, alerts=alerts,
                           sector_weights=kw.get("sectors", []),
                           total_value=kw.get("total_value"),
                           weighted_composite=kw.get("wcomp"),
                           unpriced=[], no_data_tickers=[p.ticker for p in positions if p.no_data],
                           priced_count=sum(1 for p in positions if p.weight is not None))


def test_section_absent_when_portfolio_none():
    """LOAD-BEARING: adding _Portfolio to SECTIONS must not alter normal reports."""
    vm = _vm(None)
    assert _Portfolio().applies(vm) is False
    assert "Portfolio" not in render_html_body(vm)       # no leaked <section> header
    assert "Portfolio" not in render_text(vm, Detail.FULL)


def test_section_renders_alerts_table_sectors_totals():
    positions = [_pos("LMT", weight=0.75, composite=60, gates=["negative_fcf"]),
                 _pos("AAPL", weight=0.25, composite=70)]
    s = _summary(positions, sectors=[("financials", 0.75), ("unknown", 0.25)],
                 total_value=4000.0, wcomp=62.5)
    vm = _vm(s)
    assert _Portfolio().applies(vm) is True
    html = render_html_body(vm)
    assert "Portfolio" in html and "LMT" in html and "negative_fcf" in html
    assert "financials" in html
    text = render_text(vm, Detail.FULL)
    assert "LMT" in text and "negative_fcf" in text
