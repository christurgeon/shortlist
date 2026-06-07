from shortlist.data.models import (
    SocialSentiment, TickerSnapshot, SourceResult, merge_snapshots,
)


def test_social_defaults_all_none():
    s = SocialSentiment()
    assert s.mentions is None and s.as_of is None and s.rank is None


def test_empty_social_not_merged():
    r = SourceResult(source="wsb",
                     partial=TickerSnapshot(ticker="AAA", social=SocialSentiment()))
    snap = merge_snapshots("AAA", [r], priority=["wsb"])
    assert snap.social is None
    assert "social" not in snap.provenance


def test_social_not_in_coverage_denominator():
    base = TickerSnapshot(ticker="AAA")
    withsoc = TickerSnapshot(ticker="AAA",
                             social=SocialSentiment(mentions=300, as_of="2026-06-07"))
    assert withsoc.coverage() == base.coverage()
    assert withsoc.missing() == base.missing()


def test_social_merges_and_round_trips():
    soc = SocialSentiment(as_of="2026-06-07", mentions=300, mentions_24h_ago=100,
                          upvotes=900, rank=1, rank_24h_ago=5)
    r = SourceResult(source="wsb", partial=TickerSnapshot(ticker="AAA", social=soc))
    snap = merge_snapshots("AAA", [r], priority=["wsb"])
    assert snap.social is not None and snap.social.mentions == 300
    assert snap.provenance["social"] == ["wsb"]
    back = TickerSnapshot.from_dict(snap.to_dict())
    assert back.social is not None
    assert back.social.as_of == "2026-06-07" and back.social.rank == 1
