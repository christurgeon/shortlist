import asyncio
from datetime import date

from shortlist.data.sources import (
    YahooSource, _closes_from_chart, _normalize_yahoo,
    _yh_annualized_vol, _yh_max_drawdown, _yh_ret_over, _yh_sma,
    pct_to_52w_high, max_daily_return, vol_scaled_momentum,
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


def test_pct_to_52w_high_at_the_high_is_one():
    closes = [100.0 + i for i in range(200)]   # monotonic ramp, last == max
    assert pct_to_52w_high(closes) == 1.0


def test_pct_to_52w_high_below_high_ratio():
    closes = [100.0 + i for i in range(199)] + [150.0]  # 200 closes; max 298, last 150
    r = pct_to_52w_high(closes)
    assert r is not None and abs(r - 150.0 / 298.0) < 1e-9


def test_pct_to_52w_high_none_when_too_short():
    assert pct_to_52w_high([100.0 + i for i in range(199)]) is None   # < 200


def test_max_daily_return_picks_largest_spike():
    closes = [100.0] * 30
    closes[-1] = 130.0          # a single +30% day inside the trailing 21d window
    r = max_daily_return(closes)
    assert r is not None and abs(r - 0.30) < 1e-9


def test_max_daily_return_none_when_too_short():
    assert max_daily_return([100.0, 101.0]) is None   # < window+1 (default 21)


def test_vol_scaled_momentum_none_on_flat_window():
    # vol ~ 0 (<= floor) must abstain, never mom/~0 garbage.
    assert vol_scaled_momentum([100.0] * 300) is None


def test_vol_scaled_momentum_finite_and_positive_on_rising_wobble():
    closes = [100.0 * (1.0 + 0.0008 * i) + (2.0 if i % 2 else -2.0) for i in range(300)]
    v = vol_scaled_momentum(closes)
    assert v is not None and v > 0.0


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


class _StatusOnlyClient:
    """Async client whose response raises like httpx, carrying a real status code."""

    def __init__(self, status_code):
        self.status_code = status_code
        self.calls = 0

    async def get(self, url, **kwargs):
        import httpx
        self.calls += 1
        req = httpx.Request("GET", url)
        return httpx.Response(self.status_code, request=req)

    async def aclose(self):
        return None


def test_yahoo_source_caches_a_404_so_dead_symbols_are_not_refetched(tmp_path, monkeypatch):
    """Same bug class as backtest/prices.py: `raise_for_status()` ran BEFORE
    `write_json_cache`, so Yahoo's 404 for a delisted/unknown symbol was never cached and
    every run re-fetched it — baiting the WAF on the production path that feeds the scorer."""
    src = YahooSource(cache_dir=str(tmp_path))
    client = _StatusOnlyClient(404)
    monkeypatch.setattr(src, "_client", client)

    res = asyncio.run(src.fetch("DEADCO"))
    assert res.partial.price is None                  # degrades, never raises
    asyncio.run(src.fetch("DEADCO"))
    # DEADCO fetched once; the repeat is served from the day-cache. (SPY may add calls.)
    assert client.calls <= 3, f"dead symbol re-fetched: {client.calls} calls"
    assert (tmp_path / f"DEADCO-{date.today().isoformat()}.json").exists()
    asyncio.run(src.aclose())


def test_yahoo_source_never_caches_a_429_waf_block(tmp_path, monkeypatch):
    """LOAD-BEARING. A 429 is a WAF block, not an answer about the symbol. Caching it as
    empty would fabricate a day of 'no price data' for a live ticker — which would silently
    null out price/momentum/risk for the whole run instead of failing loudly."""
    src = YahooSource(cache_dir=str(tmp_path))
    monkeypatch.setattr(src, "_client", _StatusOnlyClient(429))

    res = asyncio.run(src.fetch("AAPL"))
    assert res.partial.price is None
    assert not (tmp_path / f"AAPL-{date.today().isoformat()}.json").exists()
    asyncio.run(src.aclose())


def _chart_payload_with_ts(closes, timestamps):
    return {"chart": {"result": [{
        "timestamp": timestamps,
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


def test_yahoo_emits_monthly_closes(tmp_path, monkeypatch):
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))
    ts = [i * 86400 for i in range(400)]            # 400 daily points, 1 day apart
    closes = [100.0 + i for i in range(400)]

    async def fake_get(symbol):
        return _chart_payload_with_ts(closes, ts)
    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAPL"))
    mc = res.partial.price.monthly_closes
    assert mc, "monthly_closes should be populated"
    assert 5 <= len(mc) <= 40                       # ~monthly sampling of ~13 months
    assert mc[0][0] < mc[-1][0]                     # ISO dates ascending
    assert all(isinstance(p[0], str) and isinstance(p[1], float) for p in mc)


def test_monthly_closes_empty_when_no_timestamps(tmp_path, monkeypatch):
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))
    async def fake_get(symbol):
        return {"chart": {"result": [{"indicators": {"adjclose": [{"adjclose": [1.0, 2.0]}]}}]}}
    monkeypatch.setattr(src, "_get_chart", fake_get)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.partial.price.monthly_closes == []   # no timestamps -> no dated history, no crash


# --- residual momentum plumbed into the LIVE source path ---------------------

def _closes_from_returns(rets, start=100.0):
    out = [start]
    for r in rets:
        out.append(out[-1] * (1.0 + r))
    return out


def _resid_mom_chart_pair():
    """A ~260-bar daily chart fixture (dated) for stock + SPY where the stock has real
    idiosyncratic wobble around its market beta, so residual_momentum is finite (not the
    flat-residual abstain case). Returns (stock_payload, spy_payload)."""
    n = 260
    ts = [i * 86400 for i in range(n)]                       # n daily bars, 1 day apart
    mkt_rets = [0.011 if i % 2 == 0 else -0.008 for i in range(n - 1)]
    # stock = beta*market + an idiosyncratic component with a positive recent drift
    idio = [0.004 if k % 5 else -0.003 for k in range(n - 1)]
    stock_rets = [1.3 * m + i for m, i in zip(idio, mkt_rets, strict=False)]
    spy_closes = _closes_from_returns(mkt_rets)
    stock_closes = _closes_from_returns(stock_rets)
    return (_chart_payload_with_ts(stock_closes, ts),
            _chart_payload_with_ts(spy_closes, ts))


def test_yahoo_live_path_populates_residual_momentum(tmp_path, monkeypatch):
    # END-TO-END: a DATED chart through YahooSource.fetch must populate
    # price.residual_momentum (it was None on the live path before the date plumbing).
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))
    stock_payload, spy_payload = _resid_mom_chart_pair()

    async def fake_get(symbol):
        return spy_payload if symbol == "SPY" else stock_payload
    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAA"))
    assert res.partial.price is not None
    rm = res.partial.price.residual_momentum
    assert rm is not None and isinstance(rm, float)          # leg computed live, not None
    asyncio.run(src.aclose())


def test_yahoo_live_path_residual_none_when_dateless(tmp_path, monkeypatch):
    # ROBUSTNESS: a date-less payload (no timestamp array) falls back to the prior
    # behavior — residual_momentum stays None, no crash — so the leg simply abstains.
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))
    n = 260
    base = [100.0 + i for i in range(n)]

    async def fake_get(symbol):
        return _chart_payload(base)                          # no "timestamp" key
    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAA"))
    assert res.partial.price is not None
    assert res.partial.price.residual_momentum is None
    asyncio.run(src.aclose())


def test_normalize_populates_price_refinement_axes():
    from shortlist.data.sources import _normalize_yahoo
    closes = [100.0 * (1.0 + 0.0008 * i) + (1.5 if i % 2 else -1.5) for i in range(300)]
    snap = _normalize_yahoo("AAA", closes, closes)   # spy == stock is fine for these single-series axes
    p = snap.price
    assert p.pct_to_52w_high is not None
    assert p.max_daily_return is not None
    assert p.vol_scaled_momentum is not None
