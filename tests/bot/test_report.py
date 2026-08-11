from datetime import date
from shortlist.models import ScoreCard, Coverage
from shortlist.bot.report import render_message


def _card(ticker, comp, gates=None, coverage=None):
    return ScoreCard(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                     momentum=80, value=40, opportunity=80, insider=55, gates=gates or [],
                     coverage=coverage)


def test_message_surfaces_thin_marker():
    thin_card = ScoreCard(ticker="THN", composite=80.0, quality=None, moat=None,
                          growth=None, momentum=None, value=None, opportunity=80.0,
                          insider=None, confidence=0.30, thin=True)
    session = date(2026, 5, 29)
    msg = render_message([thin_card], session, briefs={})
    assert "(thin)" in msg


def test_message_lists_ranked_names():
    cards = [_card("AAPL", 78.4), _card("MSFT", 71.0, gates=["negative_fcf"])]
    session = date(2026, 5, 29)
    msg = render_message(cards, session, briefs={"AAPL": "Strong moat, fair price."})
    assert "AAPL" in msg and "78" in msg
    assert "negative_fcf" in msg                      # gates surfaced
    assert "Strong moat" in msg                       # brief included


def test_message_surfaces_per_ticker_coverage_note():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"],
                   note="FMP gated this symbol; value axis has no inputs.")
    cards = [_card("GEV", 64.0, coverage=cov)]
    session = date(2026, 5, 29)
    msg = render_message(cards, session)
    assert "FMP gated this symbol" in msg              # data-layer coverage surfaced
    assert "⊘" in msg


def test_message_omits_coverage_line_when_clean():
    cards = [_card("AAPL", 78.4, coverage=None)]
    session = date(2026, 5, 29)
    msg = render_message(cards, session)
    assert "⊘" not in msg                              # no coverage note => no line
