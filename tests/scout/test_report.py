from datetime import date
from shortlist.models import ScoreCard
from shortlist.scout.models import SignalStatus, RunManifest
from shortlist.scout.report import render_message


def _card(ticker, comp, gates=None):
    return ScoreCard(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                     momentum=80, value=40, opportunity=80, insider=55, gates=gates or [])


def test_message_lists_ranked_names_and_signal_coverage():
    cards = [_card("AAPL", 78.4), _card("MSFT", 71.0, gates=["negative_fcf"])]
    manifest = RunManifest(
        session=date(2026, 5, 29),
        signals=[SignalStatus("yahoo_screener", True, "42 hits"),
                 SignalStatus("wikipedia", False, "rate-limited")],
        raw=42, after_dedup=30, after_prefilter=18, screened=15, dropped_for_budget=3,
        researched=["AAPL"])
    msg = render_message(cards, manifest, briefs={"AAPL": "Strong moat, fair price."})
    assert "AAPL" in msg and "78" in msg
    assert "negative_fcf" in msg                      # gates surfaced
    assert "yahoo_screener" in msg and "rate-limited" in msg  # signal coverage line
    assert "15 screened" in msg                       # funnel line
    assert "Strong moat" in msg                       # brief included
