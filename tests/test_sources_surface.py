"""Pins the public+private import surface of shortlist.data.sources so the
package split (2026-07-24) stays byte-identical. If you add or rename a name
exported from the sources package, update _CONTRACT deliberately."""
import shortlist.data.sources as s

# The 43 names defined in the pre-split monolith + the 4 _finra_* aliases.
_CONTRACT = {
    # Source classes + base
    "EdgarSource", "FMPSource", "FinnhubSource", "FinraSource",
    "GovContractsSource", "LobbyingSource", "MockSource", "Source",
    "WsbSource", "YahooSource", "_KeyedHttpSource",
    # fmp
    "_normalize_fmp", "_year", "_match",
    # finnhub
    "_normalize_finnhub", "_news_flow", "_earnings",
    # edgar
    "_edgar_semaphore", "build_events_section", "classify_event_form",
    # yahoo pure
    "_normalize_yahoo", "_closes_from_chart", "_chart_ts_and_series",
    "_dates_from_chart", "_monthly_closes_from_chart", "_yh_sma",
    "_yh_ret_over", "_yh_annualized_vol", "_yh_max_drawdown", "ret_between",
    "mom_6m", "mom_12_1", "pct_to_52w_high", "max_daily_return",
    "vol_scaled_momentum", "snapshot_from_closes", "snapshot_from_closes_dated",
    # common
    "_load_ticker_name_index", "_read_versioned_cache", "_write_versioned_cache",
    # base http helpers
    "_fetch_sections", "_retry_after_backoff",
    # registry
    "build_sources",
    # finra aliases (imported by tests/test_short_interest.py)
    "_finra_latest_partition", "_finra_norm_symbol", "_finra_row_to_si",
    "_finra_index",
}


def test_sources_surface_is_complete():
    missing = _CONTRACT - set(dir(s))
    assert not missing, f"sources package dropped names: {sorted(missing)}"


def test_contract_names_resolve_to_real_objects():
    # Catches an accidental `= None` / broken re-export.
    for name in _CONTRACT:
        assert getattr(s, name) is not None, name
