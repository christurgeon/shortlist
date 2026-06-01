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


def test_normalize_fmp_populates_annual_history_fields():
    from shortlist.data.sources import _normalize_fmp
    raw = {
        "ratios_ttm": [{"priceToEarningsRatioTTM": 20.0}],
        "key_metrics_ttm": [{"returnOnInvestedCapitalTTM": 0.25}],
        "ratios_annual": [
            {"priceToEarningsRatio": 35.0}, {"priceToEarningsRatio": 25.0},
            {"priceToEarningsRatio": 20.0}, {"priceToEarningsRatio": 30.0},
            {"priceToEarningsRatio": 28.0},
        ],
        "key_metrics_annual": [
            {"returnOnInvestedCapital": 0.10}, {"returnOnInvestedCapital": 0.20},
            {"returnOnInvestedCapital": 0.30},
        ],
    }
    snap = _normalize_fmp("TEST", raw)
    assert snap.fundamentals.pe_median_5y == 28.0   # median of the 5 annual PEs
    assert snap.fundamentals.roic_5y_avg == 0.20    # mean of the 3 annual ROICs


def test_normalize_fmp_history_fields_none_when_annual_absent():
    from shortlist.data.sources import _normalize_fmp
    # TTM present but the annual sections errored out (missing from raw).
    raw = {
        "ratios_ttm": [{"priceToEarningsRatioTTM": 20.0}],
        "key_metrics_ttm": [{"returnOnInvestedCapitalTTM": 0.25}],
    }
    snap = _normalize_fmp("TEST", raw)
    assert snap.fundamentals.pe_ttm == 20.0          # TTM still works
    assert snap.fundamentals.pe_median_5y is None     # no annual ratios -> None
    assert snap.fundamentals.roic_5y_avg is None      # no annual key-metrics -> None


def test_statements_and_price_carry_new_value_fields():
    from shortlist.data.models import Statements, Price
    s = Statements(diluted_eps=[7.46], fiscal_period_end=["2025-09-27"])
    assert s.diluted_eps == [7.46]
    assert s.fiscal_period_end == ["2025-09-27"]
    p = Price(monthly_closes=[["2025-09-30", 255.0]])
    assert p.monthly_closes == [["2025-09-30", 255.0]]


def test_new_plumbing_fields_excluded_from_coverage_denominator():
    # diluted_eps/fiscal_period_end/monthly_closes are internal derivation aids, not
    # assessment-ready signals -> they must NOT change coverage() vs a baseline snapshot.
    from shortlist.data.models import TickerSnapshot, Statements, Price, Fundamentals, Profile, Analyst, Insider
    base = TickerSnapshot(ticker="X", profile=Profile(name="x"), fundamentals=Fundamentals(roe=0.2),
                          statements=Statements(revenue=[1.0]), analyst=Analyst(buy=1),
                          insider=Insider(buy_count=1), price=Price(price=1.0))
    cov_before = base.coverage()
    base.statements.diluted_eps = [1.0]
    base.statements.fiscal_period_end = ["2025-01-01"]
    base.price.monthly_closes = [["2025-01-01", 1.0]]
    assert base.coverage() == cov_before     # excluded fields don't move the needle
