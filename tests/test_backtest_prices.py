from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory, parse_chart


def _chart(ts_closes):
    ts = [t for t, _ in ts_closes]
    adj = [c for _, c in ts_closes]
    return {"chart": {"result": [{"timestamp": ts,
            "indicators": {"adjclose": [{"adjclose": adj}]}}]}}


def test_parse_chart_pairs_and_drops_only_null_close():
    raw = _chart([(86400, 100.0), (172800, None), (259200, 102.0)])
    dates, closes, _ = parse_chart(raw)
    assert closes == [100.0, 102.0]
    assert dates == [date(1970, 1, 2), date(1970, 1, 4)]   # dates stay ALIGNED
    assert len(dates) == len(closes)


def test_parse_chart_drops_nan_and_inf_keeps_alignment():
    raw = _chart([(86400, 100.0), (172800, float("nan")),
                  (259200, float("inf")), (345600, 105.0)])
    dates, closes, _ = parse_chart(raw)
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


def _raw(ts, adj, nominal):
    return {"chart": {"result": [{
        "timestamp": ts,
        "indicators": {
            "adjclose": [{"adjclose": adj}],
            "quote": [{"close": nominal}],
        },
    }]}}


def test_parse_chart_returns_aligned_nominal_closes():
    # 2021-01-04, 2021-01-05 (UTC midnight epochs)
    ts = [1609722000, 1609808400]
    dates, closes, nominal = parse_chart(_raw(ts, adj=[90.0, 91.0], nominal=[100.0, 101.0]))
    assert len(dates) == len(closes) == len(nominal) == 2
    assert closes == [90.0, 91.0]          # adjusted, unchanged
    assert nominal == [100.0, 101.0]        # unadjusted, new


def test_parse_chart_nominal_none_when_quote_absent():
    ts = [1609722000]
    raw = {"chart": {"result": [{"timestamp": ts,
            "indicators": {"adjclose": [{"adjclose": [90.0]}]}}]}}  # no quote array
    dates, closes, nominal = parse_chart(raw)
    assert closes == [90.0]
    assert nominal == [None]                # aligned, but unknown


def test_nominal_close_asof_uses_unadjusted_series():
    h = PriceHistory("X", [date(2021, 1, 4), date(2021, 1, 5)],
                      [90.0, 91.0], nominal_closes=[100.0, 101.0])
    assert h.nominal_close_asof(date(2021, 1, 6)) == 101.0
    assert h.close_asof(date(2021, 1, 6)) == 91.0   # adjusted path unchanged
    assert h.nominal_price_on(date(2021, 1, 5)) == 101.0


# --- fetch_history cache discipline: never cache soft failures -----------------

import asyncio

from shortlist.backtest.prices import fetch_history


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeClient:
    def __init__(self, payload): self.payload = payload; self.calls = 0
    async def get(self, url, *a, **k):
        self.calls += 1
        return _FakeResp(self.payload)


def test_fetch_history_day_caches_definitive_empty_payload(tmp_path, capsys):
    """A well-formed chart envelope with no rows is Yahoo's DEFINITIVE answer for a
    dead/unknown symbol: it IS day-cached (or every re-run re-fetches dead tickers
    and baits the WAF), with a stderr warning naming the ticker."""
    client = _FakeClient({"chart": {"result": None, "error": {"code": "Not Found"}}})
    h = asyncio.run(fetch_history("GONE", client,
                                  cache_dir=str(tmp_path), today="2026-07-10"))
    assert h.dates == [] and h.closes == []
    assert len(list(tmp_path.iterdir())) == 1        # definitive miss cached for the day
    assert "GONE" in capsys.readouterr().err          # stderr warning names the ticker
    asyncio.run(fetch_history("GONE", client,
                              cache_dir=str(tmp_path), today="2026-07-10"))
    assert client.calls == 1                          # re-run served from the day-cache


def test_fetch_history_never_caches_malformed_payload(tmp_path, capsys):
    """A 200 whose body is NOT a chart envelope is a soft failure: never cached
    (caching it would silently drop the ticker for the rest of the day)."""
    client = _FakeClient({"unexpected": "shape"})
    h = asyncio.run(fetch_history("GONE", client,
                                  cache_dir=str(tmp_path), today="2026-07-10"))
    assert h.dates == [] and h.closes == []
    assert list(tmp_path.iterdir()) == []            # no cache file written
    assert "GONE" in capsys.readouterr().err
    asyncio.run(fetch_history("GONE", client,
                              cache_dir=str(tmp_path), today="2026-07-10"))
    assert client.calls == 2                          # the soft failure was not cached


def test_fetch_history_caches_good_payload(tmp_path):
    raw = _chart([(86400, 100.0), (172800, 101.0)])
    client = _FakeClient(raw)
    h1 = asyncio.run(fetch_history("AAA", client,
                                   cache_dir=str(tmp_path), today="2026-07-10"))
    h2 = asyncio.run(fetch_history("AAA", client,
                                   cache_dir=str(tmp_path), today="2026-07-10"))
    assert h1.closes == [100.0, 101.0] == h2.closes
    assert client.calls == 1                          # second run served from disk
    assert (tmp_path / "AAA-fullhist-2026-07-10.json").exists()
