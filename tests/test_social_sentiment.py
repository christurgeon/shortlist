from shortlist.data.models import (
    SocialSentiment, TickerSnapshot, SourceResult, merge_snapshots,
)
from shortlist.models import StockMetrics


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


def test_stockmetrics_social_fields_default_none():
    m = StockMetrics(ticker="X")
    assert m.social_mentions is None
    assert m.social_mentions_rising is None
    assert m.social_mention_delta_pct is None
    assert m.social_rank is None
    assert m.social_data_age_days is None


from shortlist.data.bridge import snapshot_to_metrics


def _snap_with_social(**kw):
    return TickerSnapshot(ticker="AAA", as_of="2026-06-09T00:00:00+00:00",
                          social=SocialSentiment(**kw))


def test_bridge_derives_rising_and_delta():
    snap = _snap_with_social(as_of="2026-06-09", mentions=300, mentions_24h_ago=100, rank=1)
    m = snapshot_to_metrics(snap)
    assert m.social_mentions == 300
    assert m.social_mentions_rising is True
    assert m.social_mention_delta_pct == 2.0
    assert m.social_rank == 1
    assert m.social_data_age_days == 0


def test_bridge_falling_is_not_rising():
    snap = _snap_with_social(as_of="2026-06-09", mentions=100, mentions_24h_ago=117)
    m = snapshot_to_metrics(snap)
    assert m.social_mentions_rising is False


def test_bridge_staleness_from_old_as_of():
    snap = _snap_with_social(as_of="2026-06-02", mentions=300, mentions_24h_ago=100)
    m = snapshot_to_metrics(snap)
    assert m.social_data_age_days == 7        # 2026-06-09 - 2026-06-02


def test_bridge_no_social_leaves_none():
    m = snapshot_to_metrics(TickerSnapshot(ticker="AAA"))
    assert m.social_mentions is None and m.social_data_age_days is None


def test_bridge_zero_prev_yields_none_delta_but_rising():
    snap = _snap_with_social(as_of="2026-06-09", mentions=50, mentions_24h_ago=0)
    m = snapshot_to_metrics(snap)
    assert m.social_mention_delta_pct is None    # truthy-prev guard avoids ZeroDivisionError
    assert m.social_mentions_rising is True       # 50 > 0
