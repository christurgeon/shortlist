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


from shortlist.models import StockMetrics, ScoreCard


def test_stockmetrics_short_fields_default_none():
    m = StockMetrics(ticker="X")
    assert m.short_pct_outstanding is None
    assert m.days_to_cover is None
    assert m.short_interest_rising is None
    assert m.short_data_age_days is None


def test_scorecard_flags_default_empty_and_do_not_affect_passed():
    c = ScoreCard(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                  momentum=None, value=None, opportunity=None, insider=None)
    assert c.flags == []
    c.flags = ["crowded_short"]
    assert c.passed is True            # flags are advisory: passed depends only on gates


def test_config_yaml_has_flags_and_finra():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cs = cfg["flags"]["crowded_short"]
    assert cs["min_short_pct_outstanding"] == 0.10
    assert cs["min_days_to_cover"] == 5.0
    assert cs["require_rising"] is True
    assert cs["max_staleness_days"] == 35
    assert "finra" in cfg["harness_sources"]
    assert "finra" in cfg["scout"]["deep_screen_sources"]
