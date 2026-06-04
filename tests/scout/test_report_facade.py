from datetime import date
from shortlist.models import ScoreCard, StockMetrics
from shortlist.scout.models import RunManifest, SignalStatus
import shortlist.scout.report as R
from shortlist.scout.report import build_report, ReportArtifacts


def _card(t, c, **kw):
    base = dict(ticker=t, composite=c, quality=70, moat=60, growth=50, momentum=80,
                value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _manifest():
    return RunManifest(session=date(2026, 6, 4),
                       signals=[SignalStatus("edgar_form4", True, "2 clusters")],
                       raw=5, after_dedup=4, after_prefilter=3, screened=1,
                       dropped_for_budget=0, researched=[])


def test_build_report_returns_html_and_text(monkeypatch):
    monkeypatch.setattr(R, "_render_png", lambda vm: None)   # force png-less path
    art = build_report([_card("AAPL", 80.0, metrics=StockMetrics(ticker="AAPL", price=100.0))],
                       _manifest(), assessments={})
    assert isinstance(art, ReportArtifacts)
    assert art.png is None
    assert art.html.startswith("<!DOCTYPE html>") and "AAPL" in art.html
    assert "AAPL" in art.text and "screened" in art.text
