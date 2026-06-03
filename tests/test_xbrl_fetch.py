from shortlist.backtest.xbrl import build_cik_index, cik_for

_RAW = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

def test_build_cik_index_and_lookup_is_case_insensitive():
    idx = build_cik_index(_RAW)
    assert cik_for("aapl", idx) == "0000320193"   # zero-padded to 10
    assert cik_for("MSFT", idx) == "0000789019"

def test_cik_for_unknown_ticker_returns_none():
    assert cik_for("NOPE", build_cik_index(_RAW)) is None
