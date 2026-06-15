from shortlist.data.models import TickerSnapshot, GovContracts


def test_gov_contracts_roundtrips_through_dict():
    snap = TickerSnapshot(ticker="LMT")
    snap.gov_contracts = GovContracts(
        as_of="2026-06-15", ttm_obligated=1.2e10, prior_ttm_obligated=1.0e10,
        award_count_ttm=42, matched_recipient="LOCKHEED MARTIN CORPORATION",
        match_confidence=0.98, truncated=True, total_txns=500)
    back = TickerSnapshot.from_dict(snap.to_dict())
    assert back.gov_contracts.ttm_obligated == 1.2e10
    assert back.gov_contracts.matched_recipient == "LOCKHEED MARTIN CORPORATION"
    assert back.gov_contracts.truncated is True


def test_gov_contracts_absent_is_none():
    snap = TickerSnapshot(ticker="KO")
    assert snap.gov_contracts is None
    assert TickerSnapshot.from_dict(snap.to_dict()).gov_contracts is None
