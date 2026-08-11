from datetime import date

from shortlist.bot.models import RunManifest
from shortlist.bot.report.sections import Detail, render_html_body, render_text
from shortlist.bot.report.viewmodel import build_view_model


def _manifest():
    return RunManifest(session=date(2026, 7, 22), signals=[], raw=0, after_dedup=0,
                       after_prefilter=0, screened=0, dropped_for_budget=0,
                       researched=[], notes=[])


def _pm(alerts):
    return {"alerts": alerts, "heartbeat": {"count": 3, "as_of": "2026-07-22"}}


def test_section_absent_when_payload_none():
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=None)
    assert "Monitoring" not in render_text(vm, Detail.FULL)


def test_heartbeat_renders_on_quiet_day():
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=_pm([]))
    txt = render_text(vm, Detail.FULL)
    assert "Monitoring 3 holding" in txt


def test_alert_renders_plain_english_and_ticker():
    alert = {"ticker": "NVDA", "kind": "8k_negative", "key": "8k:AAA", "adsh": "AAA",
             "items": ["4.02"], "date": "2026-07-19",
             "meaning": "its past financial statements can no longer be relied on — a restatement is coming",
             "thesis": "capex cycle"}
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=_pm([alert]))
    txt = render_text(vm, Detail.FULL)
    assert "NVDA" in txt and "relied on" in txt and "4.02" in txt
    assert "browse-edgar" in txt and "CIK=NVDA" in txt and "type=8-K" in txt
    html = render_html_body(vm)
    assert "NVDA" in html
    assert "browse-edgar" in html and "CIK=NVDA" in html and "type=8-K" in html


def test_other_sections_byte_identical_when_payload_absent_vs_none():
    a = render_html_body(build_view_model([], _manifest(), assessments={}))
    b = render_html_body(build_view_model([], _manifest(), assessments={}, positions_monitor=None))
    assert a == b
