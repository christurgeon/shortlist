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
