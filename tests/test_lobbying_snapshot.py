from shortlist.data.models import TickerSnapshot, Lobbying


def test_lobbying_roundtrips_through_dict():
    snap = TickerSnapshot(ticker="LMT")
    snap.lobbying = Lobbying(
        as_of="2026-06-15", latest_filing="2026-04-01", ttm_spend=1.3e7,
        prior_ttm_spend=1.1e7, filing_count_ttm=60, matched_client="LOCKHEED MARTIN CORPORATION",
        match_confidence=0.99, registrant_count=22, truncated=True, total_filings=66)
    back = TickerSnapshot.from_dict(snap.to_dict())
    assert back.lobbying.ttm_spend == 1.3e7
    assert back.lobbying.matched_client == "LOCKHEED MARTIN CORPORATION"
    assert back.lobbying.truncated is True


def test_lobbying_absent_is_none():
    snap = TickerSnapshot(ticker="KO")
    assert snap.lobbying is None
    assert TickerSnapshot.from_dict(snap.to_dict()).lobbying is None
