from __future__ import annotations

from shortlist.data import collect
from shortlist.data.models import (
    Fundamentals, Price, SourceResult, TickerSnapshot, merge_snapshots,
)
from shortlist.data.store import load, save


def test_mock_collect_is_assessment_ready():
    snaps = {s.ticker: s for s in collect(["GEV", "LMT"], ["mock"])}
    assert set(snaps) == {"GEV", "LMT"}
    assert snaps["GEV"].coverage() >= 0.8
    assert snaps["GEV"].fundamentals.roe == 0.44
    assert snaps["GEV"].analyst.target_median == 1217


def test_unknown_ticker_reports_thin_not_crash():
    (snap,) = collect(["NOPE"], ["mock"])
    assert snap.coverage() == 0.0
    assert snap.missing()  # everything missing
    assert any("no sample" in e for e in snap.errors)


def test_field_level_merge_fills_gaps_across_sources():
    # Primary has pe but no roe; secondary supplies the missing roe.
    primary = SourceResult(
        source="fmp",
        partial=TickerSnapshot(ticker="X", fundamentals=Fundamentals(pe_ttm=20.0, roe=None)),
    )
    secondary = SourceResult(
        source="finnhub",
        partial=TickerSnapshot(ticker="X", fundamentals=Fundamentals(pe_ttm=None, roe=0.30)),
    )
    merged = merge_snapshots("X", [secondary, primary], priority=["fmp", "finnhub"])
    assert merged.fundamentals.pe_ttm == 20.0   # from fmp (higher priority)
    assert merged.fundamentals.roe == 0.30       # filled from finnhub
    assert set(merged.provenance["fundamentals"]) == {"fmp", "finnhub"}


def test_higher_priority_wins_on_conflict():
    a = SourceResult(source="fmp", partial=TickerSnapshot(ticker="X", price=Price(price=100.0)))
    b = SourceResult(source="finnhub", partial=TickerSnapshot(ticker="X", price=Price(price=101.0)))
    merged = merge_snapshots("X", [b, a], priority=["fmp", "finnhub"])
    assert merged.price.price == 100.0


def test_snapshot_roundtrips_through_store(tmp_path):
    (snap,) = collect(["LMT"], ["mock"])
    path = save(snap, tmp_path)
    assert path.exists()
    loaded = load("LMT", tmp_path)
    assert loaded["ticker"] == "LMT"
    assert loaded["fundamentals"]["pe_ttm"] == 20.0


def test_yahoo_leads_default_priority():
    from shortlist.data.collector import DEFAULT_PRIORITY
    # Yahoo must outrank FMP so its auditable price fields win the price merge.
    assert DEFAULT_PRIORITY.index("yahoo") < DEFAULT_PRIORITY.index("fmp")
