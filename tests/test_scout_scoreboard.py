from datetime import date

from shortlist.scout.picks import bucket_label, pick_performance


def test_excess_over_spy_and_bucket():
    pick = {"ticker": "XYZ", "session": "2026-01-02", "as_of_price": 10.0}
    stock = [(date(2026, 1, 2), 10.0), (date(2026, 4, 2), 13.0)]   # +30%
    spy = [(date(2026, 1, 2), 100.0), (date(2026, 4, 2), 110.0)]   # +10%
    perf = pick_performance(pick, stock, spy)
    assert round(perf["ret"], 3) == 0.30
    assert round(perf["spy_ret"], 3) == 0.10
    assert round(perf["excess"], 3) == 0.20
    assert perf["horizon_bucket"] == "3m"
    assert perf["ticker"] == "XYZ"


def test_split_safe_uses_series_not_scalar():
    # as_of_price scalar is pre-split (20.0). The SERIES is split-adjusted (10 -> 13).
    # Return must come from the series (+30%), NOT current/scalar (13/20-1 = -35%).
    pick = {"ticker": "XYZ", "session": "2026-01-02", "as_of_price": 20.0}
    stock = [(date(2026, 1, 2), 10.0), (date(2026, 4, 2), 13.0)]
    spy = [(date(2026, 1, 2), 100.0), (date(2026, 4, 2), 110.0)]
    assert round(pick_performance(pick, stock, spy)["ret"], 3) == 0.30


def test_selection_close_is_on_or_after_session():
    # If no bar exactly on the session, use the first bar on/after it.
    pick = {"ticker": "XYZ", "session": "2026-01-01", "as_of_price": None}
    stock = [(date(2026, 1, 2), 10.0), (date(2026, 2, 2), 11.0)]
    spy = [(date(2026, 1, 2), 100.0), (date(2026, 2, 2), 100.0)]
    perf = pick_performance(pick, stock, spy)
    assert round(perf["ret"], 3) == 0.10
    assert round(perf["excess"], 3) == 0.10


def test_missing_data_safe():
    perf = pick_performance({"ticker": "X", "session": "2026-01-02", "as_of_price": None}, [], [])
    assert perf["ret"] is None and perf["excess"] is None
    # spy missing -> ret computed, excess None
    p2 = pick_performance({"ticker": "X", "session": "2026-01-02"},
                          [(date(2026, 1, 2), 10.0), (date(2026, 3, 2), 11.0)], [])
    assert round(p2["ret"], 3) == 0.10 and p2["excess"] is None


def test_bucket_label():
    assert bucket_label(20) == "1m"
    assert bucket_label(60) == "3m"
    assert bucket_label(150) == "6m"
    assert bucket_label(300) == "12m"
    assert bucket_label(500) == ">12m"
