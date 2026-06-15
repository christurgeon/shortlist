from shortlist.backtest.xbrl import build_name_index


def test_build_name_index_maps_ticker_to_title():
    raw = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
        "2": {"cik_str": 0, "ticker": "", "title": "Bad Row"},  # skipped
    }
    idx = build_name_index(raw)
    assert idx["AAPL"] == "Apple Inc."
    assert idx["LMT"] == "LOCKHEED MARTIN CORP"
    assert "" not in idx
