from shortlist.data.models import (
    ShortInterest, TickerSnapshot, SourceResult, merge_snapshots,
)


def test_short_interest_defaults():
    si = ShortInterest()
    assert si.settlement_date is None and si.short_shares is None
    assert si.split_flag is None and si.revised is None


def test_empty_short_interest_not_merged():
    from shortlist.data.models import ShortInterest, SourceResult, TickerSnapshot, merge_snapshots
    r = SourceResult(source="finra",
                     partial=TickerSnapshot(ticker="AAA", short_interest=ShortInterest()))
    snap = merge_snapshots("AAA", [r], priority=["finra"])
    assert snap.short_interest is None
    assert "short_interest" not in snap.provenance


def test_short_interest_not_in_coverage_denominator():
    # A snapshot with NO short_interest and one WITH it must report identical coverage.
    base = TickerSnapshot(ticker="AAA")
    withsi = TickerSnapshot(ticker="AAA",
                            short_interest=ShortInterest(short_shares=1.0, settlement_date="2026-05-15"))
    assert withsi.coverage() == base.coverage()
    assert withsi.missing() == base.missing()


def test_short_interest_merges_and_round_trips():
    si = ShortInterest(settlement_date="2026-05-15", short_shares=100.0,
                       prev_short_shares=90.0, days_to_cover=4.2)
    r = SourceResult(source="finra", partial=TickerSnapshot(ticker="AAA", short_interest=si))
    snap = merge_snapshots("AAA", [r], priority=["finra"])
    assert snap.short_interest is not None and snap.short_interest.short_shares == 100.0
    assert snap.provenance["short_interest"] == ["finra"]
    # to_dict -> from_dict preserves the section (else persisted snapshots drop it)
    back = TickerSnapshot.from_dict(snap.to_dict())
    assert back.short_interest is not None
    assert back.short_interest.settlement_date == "2026-05-15"
    assert back.short_interest.days_to_cover == 4.2
