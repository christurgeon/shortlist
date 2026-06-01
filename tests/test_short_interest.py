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


import asyncio
from shortlist.data.sources import (
    FinraSource, _finra_latest_partition, _finra_norm_symbol,
    _finra_row_to_si, _finra_index,
)


def test_finra_latest_partition_picks_max():
    payload = {"availablePartitions": [
        {"partitions": ["2026-04-30"]}, {"partitions": ["2026-05-15"]},
        {"partitions": ["2026-04-15"]}]}
    assert _finra_latest_partition(payload) == "2026-05-15"
    assert _finra_latest_partition({"availablePartitions": []}) is None


def test_finra_norm_symbol_collapses_separators():
    assert _finra_norm_symbol("brk.b") == "BRKB"
    assert _finra_norm_symbol("BRK-B") == "BRKB"


def test_finra_row_to_si_and_index():
    row = {"symbolCode": "AAPL", "settlementDate": "2026-05-15",
           "currentShortPositionQuantity": "138782718",
           "previousShortPositionQuantity": "134675274",
           "averageDailyVolumeQuantity": "50565316",
           "daysToCoverQuantity": "2.74", "stockSplitFlag": "", "revisionFlag": ""}
    si = _finra_row_to_si(row)
    assert si.short_shares == 138782718.0 and si.days_to_cover == 2.74
    assert si.split_flag is False
    idx = _finra_index([row])
    assert idx["AAPL"]["symbolCode"] == "AAPL"


def _finra_mock(monkeypatch, src, pages):
    """pages: list of row-lists returned per offset call (simulates pagination)."""
    async def fake_parts():
        return {"availablePartitions": [{"partitions": ["2026-05-15"]}]}
    async def fake_page(settlement, offset):
        i = offset // src.PAGE
        return pages[i] if i < len(pages) else []
    monkeypatch.setattr(src, "_fetch_partitions", fake_parts)
    monkeypatch.setattr(src, "_fetch_page", fake_page)


def test_finra_source_builds_short_interest(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    full = [{"symbolCode": f"S{i}", "settlementDate": "2026-05-15",
             "currentShortPositionQuantity": str(i)} for i in range(src.PAGE)]
    tail = [{"symbolCode": "AAPL", "settlementDate": "2026-05-15",
             "currentShortPositionQuantity": "138782718", "daysToCoverQuantity": "2.74"}]
    _finra_mock(monkeypatch, src, [full, tail])     # two pages: 5000 then 1 (short page ends loop)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.source == "finra"
    assert res.partial.short_interest is not None
    assert res.partial.short_interest.days_to_cover == 2.74
    asyncio.run(src.aclose())


def test_finra_absent_symbol_is_none_not_error(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    _finra_mock(monkeypatch, src, [[{"symbolCode": "AAPL", "settlementDate": "2026-05-15"}]])
    res = asyncio.run(src.fetch("ZZZZ"))
    assert res.partial.short_interest is None
    assert res.errors == []
    asyncio.run(src.aclose())


def test_finra_loads_cycle_once_per_run(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    parts_calls = {"n": 0}
    page_calls = {"n": 0}

    async def fake_parts():
        parts_calls["n"] += 1
        return {"availablePartitions": [{"partitions": ["2026-05-15"]}]}

    async def fake_page(settlement, offset):
        page_calls["n"] += 1
        return [{"symbolCode": "AAPL", "settlementDate": "2026-05-15",
                 "currentShortPositionQuantity": "100", "daysToCoverQuantity": "2.0"},
                {"symbolCode": "MSFT", "settlementDate": "2026-05-15",
                 "currentShortPositionQuantity": "200", "daysToCoverQuantity": "3.0"}]

    monkeypatch.setattr(src, "_fetch_partitions", fake_parts)
    monkeypatch.setattr(src, "_fetch_page", fake_page)

    a = asyncio.run(src.fetch("AAPL"))
    b = asyncio.run(src.fetch("MSFT"))     # second ticker must reuse the loaded index
    assert a.partial.short_interest is not None and b.partial.short_interest is not None
    # The cycle is discovered + paged exactly once, then reused across tickers.
    assert parts_calls["n"] == 1
    assert page_calls["n"] == 1            # single page (2 rows < PAGE), fetched once total
    asyncio.run(src.aclose())


def test_finra_load_error_is_non_fatal(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    async def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(src, "_fetch_partitions", boom)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.partial.short_interest is None
    assert res.errors and "finra" in res.errors[0]
    asyncio.run(src.aclose())
