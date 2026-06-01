import asyncio

from shortlist.data.sources import (
    YahooSource, _closes_from_chart, _normalize_yahoo,
    _yh_annualized_vol, _yh_max_drawdown, _yh_ret_over, _yh_sma,
)


# --- pure math ---------------------------------------------------------------

def test_sma():
    assert _yh_sma([1, 2, 3, 4], 2) == 3.5
    assert _yh_sma([1, 2], 5) is None  # not enough points


def test_ret_over():
    assert abs(_yh_ret_over([100, 110], 1) - 0.10) < 1e-9
    assert _yh_ret_over([100], 1) is None


def test_annualized_vol_constant_series_is_zero():
    assert _yh_annualized_vol([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_annualized_vol_positive_for_moving_series():
    v = _yh_annualized_vol([100, 101, 99, 102, 98, 103])
    assert v is not None and v > 0


def test_max_drawdown_simple():
    # peak 100 -> trough 80 => -0.20
    md = _yh_max_drawdown([90, 100, 80, 95])
    assert abs(md - (-0.20)) < 1e-9


def test_max_drawdown_monotonic_up_is_zero():
    assert _yh_max_drawdown([100, 110, 120]) == 0.0


def test_normalize_builds_price_with_rel_strength():
    # stock +20% over the 126-day window, SPY +10% => rel strength +10%
    closes = [100.0] * 126 + [120.0]          # len 127, index -127 == 100
    spy = [100.0] * 126 + [110.0]
    snap = _normalize_yahoo("AAA", closes, spy)
    assert snap.price is not None
    assert abs(snap.price.ret_6m - 0.20) < 1e-9
    assert abs(snap.price.rel_strength_6m - 0.10) < 1e-9
    assert snap.price.price == 120.0


def test_normalize_computes_ma200():
    # >=200 points so the 200d SMA is actually computed (the core momentum input).
    closes = [float(i) for i in range(1, 251)]   # 1..250 ascending
    snap = _normalize_yahoo("AAA", closes, [])
    assert snap.price.ma200 == sum(range(51, 251)) / 200   # last 200 = 51..250
    assert snap.price.rel_strength_6m is None              # no SPY series given


def test_normalize_empty_closes_returns_bare_snapshot():
    snap = _normalize_yahoo("AAA", [], [])
    assert snap.ticker == "AAA" and snap.price is None


# --- source (network isolated via _get_chart monkeypatch) --------------------

def _chart_payload(closes):
    return {"chart": {"result": [{
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


def test_closes_from_chart_filters_nulls():
    raw = _chart_payload([100.0, None, 102.0])
    assert _closes_from_chart(raw) == [100.0, 102.0]


def test_closes_from_chart_handles_garbage():
    assert _closes_from_chart({}) == []
    assert _closes_from_chart({"chart": {"result": []}}) == []


def test_yahoo_source_uses_disk_cache(tmp_path, monkeypatch):
    src = YahooSource(cache_dir=str(tmp_path))
    calls = []

    async def fake_get(symbol):
        calls.append(symbol)
        n = 130
        base = [100.0] * n
        return _chart_payload(base + [120.0] if symbol != "SPY" else base + [110.0])

    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAA"))
    assert res.source == "yahoo"
    assert res.partial.price is not None
    assert res.partial.price.rel_strength_6m is not None
    # AAA + SPY fetched once each
    assert sorted(calls) == ["AAA", "SPY"]

    # second ticker reuses the cached SPY (no second SPY network call)
    asyncio.run(src.fetch("BBB"))
    assert calls.count("SPY") == 1
    asyncio.run(src.aclose())


def test_yahoo_source_error_is_non_fatal(tmp_path, monkeypatch):
    src = YahooSource(cache_dir=str(tmp_path))

    async def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(src, "_get_chart", boom)
    res = asyncio.run(src.fetch("AAA"))
    assert res.partial.price is None
    assert res.errors and "yahoo" in res.errors[0]
    asyncio.run(src.aclose())
