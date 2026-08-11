from datetime import date
from shortlist.models import ScoreCard, StockMetrics
import shortlist.bot.report as R
from shortlist.bot.report import build_report, ReportArtifacts


def _card(t, c, **kw):
    base = dict(ticker=t, composite=c, quality=70, moat=60, growth=50, momentum=80,
                value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _session():
    return date(2026, 6, 4)


def test_build_report_returns_html_and_text(monkeypatch):
    monkeypatch.setattr(R, "_render_png", lambda vm: None)   # force png-less path
    art = build_report([_card("AAPL", 80.0, metrics=StockMetrics(ticker="AAPL", price=100.0))],
                       _session(), assessments={})
    assert isinstance(art, ReportArtifacts)
    assert art.png is None
    assert art.html.startswith("<!DOCTYPE html>") and "AAPL" in art.html
    assert "AAPL" in art.text
