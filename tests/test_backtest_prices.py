from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory, parse_chart


def _chart(ts_closes):
    ts = [t for t, _ in ts_closes]
    adj = [c for _, c in ts_closes]
    return {"chart": {"result": [{"timestamp": ts,
            "indicators": {"adjclose": [{"adjclose": adj}]}}]}}


def test_parse_chart_pairs_and_drops_only_null_close():
    raw = _chart([(86400, 100.0), (172800, None), (259200, 102.0)])
    dates, closes = parse_chart(raw)
    assert closes == [100.0, 102.0]
    assert dates == [date(1970, 1, 2), date(1970, 1, 4)]   # dates stay ALIGNED
    assert len(dates) == len(closes)


def test_parse_chart_drops_nan_and_inf_keeps_alignment():
    raw = _chart([(86400, 100.0), (172800, float("nan")),
                  (259200, float("inf")), (345600, 105.0)])
    dates, closes = parse_chart(raw)
    assert closes == [100.0, 105.0]                        # NaN/Inf excluded
    assert dates == [date(1970, 1, 2), date(1970, 1, 5)]   # still aligned


def _hist():
    start = date(2020, 1, 1)
    dates, closes = [], []
    for i in range(240):
        dates.append(start + timedelta(days=i))
        closes.append(100.0 + i)
    return PriceHistory("AAA", dates, closes)


def test_close_asof_uses_latest_on_or_before():
    h = _hist()
    assert h.close_asof(date(2020, 1, 10)) == 100.0 + 9
    assert h.close_asof(date(2019, 12, 1)) is None


def test_closes_through_truncates_at_date():
    h = _hist()
    cs = h.closes_through(date(2020, 1, 10))
    assert cs == [100.0 + i for i in range(10)]


def test_price_on_nearest_within_tolerance():
    h = PriceHistory("AAA", [date(2020, 1, 1), date(2020, 1, 8)], [100.0, 107.0])
    assert h.price_on(date(2020, 1, 9), tol_days=5) == 107.0
    assert h.price_on(date(2020, 1, 20), tol_days=5) is None


def test_forward_return_calendar_month_excess_drop():
    h = _hist()
    r = h.forward_return(date(2020, 1, 1), horizon_months=3)
    assert r is not None and 0.8 < r < 1.0
    assert h.forward_return(date(2020, 8, 1), horizon_months=3) is None
