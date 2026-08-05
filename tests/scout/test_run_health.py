"""Run-health classification (docs/audits/2026-08-05-discovery-funnel-audit.md §5d).

On 2026-08-04 the digest reported `0 raw` — but two originators had FAILED (a transient SEC
429), which reads identically to a genuinely quiet filing day unless the report says so.
"""
from shortlist.scout.models import SignalStatus, run_health


def _ok(name, discovery=True):
    return SignalStatus(name, True, "4 hits", discovery)


def _failed(name, detail="CIK→ticker resolver empty; skipped", discovery=True):
    return SignalStatus(name, False, detail, discovery)


def _disabled(name):
    return SignalStatus(name, False, "disabled", True)


def test_a_failed_originator_makes_the_run_degraded_and_is_named():
    status, failed = run_health(
        [_ok("edgar_form4"), _failed("edgar_activist_13d"), _disabled("edgar_8k")], raw=0)
    assert status == "degraded"
    assert failed == ["edgar_activist_13d"]


def test_zero_candidates_with_every_originator_healthy_is_a_quiet_day():
    status, failed = run_health([_ok("edgar_form4"), _ok("edgar_13f"), _disabled("edgar_8k")],
                                raw=0)
    assert status == "quiet"
    assert failed == []


def test_a_disabled_signal_is_never_a_failure():
    """Six signals ship disabled on evidence; they must not read as breakage."""
    status, failed = run_health([_ok("edgar_form4"), _disabled("wsb_hype"),
                                 _disabled("edgar_buyback")], raw=3)
    assert status == "healthy"
    assert failed == []


def test_an_enrichment_signal_with_nothing_to_enrich_is_not_a_failure():
    """finnhub_news/wikipedia report ran=False `checked 0 tickers` when the funnel handed
    them nothing — a CONSEQUENCE of an empty funnel, never its cause."""
    status, failed = run_health(
        [_ok("edgar_form4"),
         _failed("finnhub_news", "checked 0 tickers", discovery=False),
         _failed("wikipedia", "checked 0 mapped tickers", discovery=False)], raw=2)
    assert status == "healthy"
    assert failed == []


def test_a_partial_failure_is_still_degraded_even_when_candidates_were_found():
    """raw > 0 must not mask a broken originator — that is how 13D died unnoticed on 08-03."""
    status, failed = run_health([_ok("edgar_form4"), _failed("edgar_activist_13d")], raw=3)
    assert status == "degraded"
    assert failed == ["edgar_activist_13d"]


def test_signal_status_defaults_to_discovery_for_back_compat():
    """Every existing 3-arg construction must keep working (models.py's last-field rule)."""
    assert SignalStatus("edgar_form4", True, "4 hits").discovery is True


def test_boosters_are_recorded_as_non_discovery():
    """`_run_boosters` must stamp discovery=False, or `checked 0 tickers` — a downstream
    CONSEQUENCE of an empty funnel — gets misreported as a broken originator."""
    from datetime import date

    from shortlist.scout.daily import _run_boosters

    class _Booster:
        name = "finnhub_news"
        is_discovery = False

        def scan_for(self, tickers, session):
            return []

        def available(self):
            return (False, "checked 0 tickers")

    statuses: list[SignalStatus] = []
    _run_boosters([_Booster()], [], session=date(2026, 8, 4), sig_cfg={}, statuses=statuses)
    assert [s.discovery for s in statuses] == [False]
    assert run_health(statuses, raw=0)[0] == "quiet"     # not "degraded"


# --- report surfacing ---------------------------------------------------------------
# These use SignalStatusVM, NOT SignalStatus: the report never sees the manifest dataclass,
# and asserting against the wrong type hid a missing field on the real path once already.

def _vm_ok(name, discovery=True):
    from shortlist.scout.report.viewmodel import SignalStatusVM
    return SignalStatusVM(name, True, "4 hits", discovery)


def _vm_failed(name, detail="CIK→ticker resolver empty; skipped"):
    from shortlist.scout.report.viewmodel import SignalStatusVM
    return SignalStatusVM(name, False, detail, True)


def _vm_disabled(name):
    from shortlist.scout.report.viewmodel import SignalStatusVM
    return SignalStatusVM(name, False, "disabled", True)


def test_the_viewmodel_carries_discovery_through_from_the_manifest():
    """The field must survive manifest -> viewmodel, or the report silently classifies every
    booster as a failed originator."""
    from datetime import date

    from shortlist.scout.models import RunManifest
    from shortlist.scout.report.viewmodel import build_view_model

    manifest = RunManifest(session=date(2026, 8, 4),
                           signals=[SignalStatus("edgar_form4", True, "4 hits", True),
                                    SignalStatus("finnhub_news", False, "checked 0 tickers", False)],
                           raw=0, after_dedup=0, after_prefilter=0, screened=0,
                           dropped_for_budget=0)
    vm = build_view_model([], manifest, assessments={})
    assert [s.discovery for s in vm.signals] == [True, False]


def test_report_names_the_failed_originators_on_a_degraded_run():
    from datetime import date

    from shortlist.scout.report.sections import Detail, _Footer
    from shortlist.scout.report.viewmodel import FunnelVM, ReportVM

    vm = ReportVM(session=date(2026, 8, 4), leaders=[],
                  signals=[_vm_ok("edgar_form4"), _vm_failed("edgar_activist_13d")],
                  funnel=FunnelVM(0, 0, 0, 0, 0), notes=[], deep_block=[], prior_picks=[])
    txt = "\n".join(_Footer().render_text(vm, Detail.FULL))
    assert "DEGRADED" in txt
    assert "edgar_activist_13d" in txt


def test_html_report_also_flags_a_degraded_run():
    """The HTML file is the delivered artifact — the warning cannot be text-only."""
    from datetime import date

    from shortlist.scout.report.sections import _Footer
    from shortlist.scout.report.html import HtmlBuilder
    from shortlist.scout.report.viewmodel import FunnelVM, ReportVM

    vm = ReportVM(session=date(2026, 8, 4), leaders=[],
                  signals=[_vm_ok("edgar_form4"), _vm_failed("edgar_activist_13d")],
                  funnel=FunnelVM(0, 0, 0, 0, 0), notes=[], deep_block=[], prior_picks=[])
    html = _Footer().render_html(vm, HtmlBuilder())
    assert "DEGRADED" in html
    assert "edgar_activist_13d" in html


def test_report_calls_a_genuinely_empty_day_quiet_not_degraded():
    from datetime import date

    from shortlist.scout.report.sections import Detail, _Footer
    from shortlist.scout.report.viewmodel import FunnelVM, ReportVM

    vm = ReportVM(session=date(2026, 8, 4), leaders=[],
                  signals=[_vm_ok("edgar_form4"), _vm_disabled("edgar_8k")],
                  funnel=FunnelVM(0, 0, 0, 0, 0), notes=[], deep_block=[], prior_picks=[])
    txt = "\n".join(_Footer().render_text(vm, Detail.FULL))
    assert "DEGRADED" not in txt
    assert "quiet" in txt.lower()
