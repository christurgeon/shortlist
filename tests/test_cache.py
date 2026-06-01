import asyncio

import pytest

from shortlist.cache import (cache_key, _is_cacheable, ttl_for, _BUCKET_BY_KEY,
                             HttpCache, NoOpCache, configure_default_cache,
                             get_default_cache, reset_default_cache)


def test_cache_key_order_independent():
    a = cache_key("fmp", "ratios", {"symbol": "AAPL", "period": "annual", "limit": 5})
    b = cache_key("fmp", "ratios", {"limit": 5, "period": "annual", "symbol": "AAPL"})
    assert a == b


def test_cache_key_strips_secrets():
    with_key = cache_key("fmp", "quote", {"symbol": "AAPL", "apikey": "SECRET1"})
    rotated = cache_key("fmp", "quote", {"symbol": "AAPL", "apikey": "SECRET2"})
    no_key = cache_key("fmp", "quote", {"symbol": "AAPL"})
    assert with_key == rotated == no_key
    assert "SECRET1" not in with_key


def test_cache_key_distinguishes_provider_endpoint_symbol():
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("finnhub", "quote", {"symbol": "AAPL"})
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("fmp", "profile", {"symbol": "AAPL"})
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("fmp", "quote", {"symbol": "MSFT"})


def test_cache_key_raises_on_non_json_param():
    with pytest.raises(TypeError):
        cache_key("fmp", "quote", {"symbol": object()})


@pytest.mark.parametrize("payload", [None, [], {}, "", {"error": "Not found"},
                                     {"Error": "x"}])
def test_not_cacheable(payload):
    assert _is_cacheable(payload) is False


@pytest.mark.parametrize("payload", [[{"c": 123}], {"c": 123}, {"data": []},
                                     {"errors": 0}])  # 'errors' != 'error' key
def test_cacheable(payload):
    assert _is_cacheable(payload) is True


def test_ttl_for_buckets():
    cfg = {}  # no overrides -> hardcoded defaults
    assert ttl_for("fmp", "quote", cfg) == 21600
    assert ttl_for("finnhub", "quote", cfg) == 21600
    assert ttl_for("fmp", "income-statement", cfg) == 604800
    assert ttl_for("fmp", "ratios-ttm", cfg) == 86400
    assert ttl_for("fmp", "unknown-endpoint", cfg) == 86400  # default bucket


def test_ttl_for_respects_config_override():
    cfg = {"quote": 60, "statements": 120}
    assert ttl_for("fmp", "quote", cfg) == 60
    assert ttl_for("fmp", "income-statement", cfg) == 120


def test_bucket_map_covers_all_live_endpoints():
    """Every (provider, path) emitted by the four wrapped _get call sites must be in
    the bucket map, or it silently demotes to the 1d default."""
    harness = {
        ("fmp", "profile"), ("fmp", "quote"), ("fmp", "ratios-ttm"),
        ("fmp", "ratios"), ("fmp", "key-metrics-ttm"), ("fmp", "key-metrics"),
        ("fmp", "income-statement"), ("fmp", "balance-sheet-statement"),
        ("fmp", "cash-flow-statement"), ("fmp", "price-target-consensus"),
        ("fmp", "grades-consensus"), ("fmp", "insider-trading/search"),
        ("fmp", "stock-price-change"),
        ("finnhub", "stock/profile2"), ("finnhub", "quote"),
        ("finnhub", "stock/metric"), ("finnhub", "stock/recommendation"),
        ("finnhub", "stock/insider-sentiment"),
    }
    screener = {
        ("fmp", "quote"), ("fmp", "ratios-ttm"), ("fmp", "key-metrics-ttm"),
        ("fmp", "price-target-consensus"), ("fmp", "grades-consensus"),
        ("fmp", "stock-price-change"),
        ("finnhub", "quote"), ("finnhub", "stock/metric"),
        ("finnhub", "stock/recommendation"), ("finnhub", "stock/insider-sentiment"),
    }
    for pk in harness | screener:
        assert pk in _BUCKET_BY_KEY, f"{pk} missing from bucket map"


# --- Task 3: HttpCache sync store ------------------------------------------------

def test_caches_within_ttl(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))  # default quote TTL = 6h
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": 1}]

    r1 = cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    r2 = cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert r1 == r2 == [{"c": 1}]
    assert calls["n"] == 1
    cache.close()


def test_ttl_expiry_refetches(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"), ttls={"quote": -1})  # already expired
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": calls["n"]}]

    cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert calls["n"] == 2
    cache.close()


def test_empty_payload_not_stored(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return []  # soft failure

    cache.get_or_fetch("fmp", "grades-consensus", {"symbol": "X"}, fetch)
    cache.get_or_fetch("fmp", "grades-consensus", {"symbol": "X"}, fetch)
    assert calls["n"] == 2  # empty never cached -> re-fetched
    cache.close()


def test_errors_never_cached(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cache.get_or_fetch("fmp", "quote", {"symbol": "X"}, fetch)
    assert calls["n"] == 2
    cache.close()


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "c.sqlite")
    c1 = HttpCache(path)
    c1.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, lambda: [{"c": 7}])
    c1.close()  # model an ad-hoc process exiting

    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": 999}]

    c2 = HttpCache(path)  # a fresh "process"
    r = c2.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert r == [{"c": 7}] and calls["n"] == 0  # served from persisted DB
    c2.close()


def test_refresh_mode_bypasses_read_but_writes(tmp_path):
    path = str(tmp_path / "c.sqlite")
    seed = HttpCache(path)
    seed.get_or_fetch("fmp", "quote", {"symbol": "A"}, lambda: [{"v": 1}])
    seed.close()

    cache = HttpCache(path, refresh=True)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"v": 2}]

    assert cache.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch) == [{"v": 2}]
    assert calls["n"] == 1  # bypassed the stale read
    cache.close()

    after = HttpCache(path)  # refresh wrote it through
    assert after.get_or_fetch("fmp", "quote", {"symbol": "A"},
                              lambda: [{"v": 3}]) == [{"v": 2}]
    after.close()


# --- Task 4: async --------------------------------------------------------------

def test_aget_or_fetch_caches_and_runs_concurrently(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0)
        return [{"c": 1}]

    async def scenario():
        r1 = await cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
        r2, r3 = await asyncio.gather(
            cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch),
            cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch),
        )
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(scenario())
    assert r1 == r2 == r3 == [{"c": 1}]
    assert calls["n"] == 1
    cache.close()


# --- Task 5: NoOpCache, singleton, fallback -------------------------------------

def test_noopcache_always_fetches():
    c = NoOpCache()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [1]

    c.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
    c.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
    assert calls["n"] == 2


def test_configure_disabled_returns_noop():
    configure_default_cache(enabled=False)
    assert isinstance(get_default_cache(), NoOpCache)
    reset_default_cache()


def test_configure_enabled_returns_httpcache(tmp_path):
    configure_default_cache(enabled=True, path=str(tmp_path / "c.sqlite"))
    assert isinstance(get_default_cache(), HttpCache)
    reset_default_cache()


def test_get_default_cache_unconfigured_is_enabled(tmp_path, monkeypatch):
    reset_default_cache()
    monkeypatch.chdir(tmp_path)  # default .cache/ lands in tmp
    assert isinstance(get_default_cache(), HttpCache)
    reset_default_cache()


def test_corrupt_db_falls_back_to_noop(tmp_path):
    bad = tmp_path / "c.sqlite"
    bad.write_text("not a sqlite database")
    configure_default_cache(enabled=True, path=str(bad))
    assert isinstance(get_default_cache(), NoOpCache)
    reset_default_cache()


def test_sweep_removes_expired(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"), ttls={"quote": -1, "profile": 1000})
    cache.get_or_fetch("fmp", "quote", {"symbol": "A"}, lambda: [1])     # expired
    cache.get_or_fetch("fmp", "profile", {"symbol": "B"}, lambda: [2])   # live
    cache.sweep()
    with cache._lock:
        n = cache._conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    assert n == 1
    cache.close()


# --- Task 7: wired screener providers -------------------------------------------

def test_fmp_provider_get_uses_cache(tmp_path):
    from shortlist.providers.fmp import FMPProvider

    real = HttpCache(str(tmp_path / "c.sqlite"))
    p = FMPProvider.__new__(FMPProvider)  # bypass __init__/key requirement
    p.key = "test"
    p.timeout = 15
    p.max_retries = 2
    p._cache = real

    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"price": 1}]

    class FakeSession:
        def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    p._session = FakeSession()

    assert p._get("quote", symbol="AAPL") == [{"price": 1}]
    assert p._get("quote", symbol="AAPL") == [{"price": 1}]
    assert calls["n"] == 1  # second call cached
    real.close()


# --- Task 8: wired harness sources (async) --------------------------------------

def test_fmp_source_get_uses_cache(tmp_path):
    from shortlist.data.sources import FMPSource

    real = HttpCache(str(tmp_path / "c.sqlite"))
    s = FMPSource.__new__(FMPSource)
    s.key = "test"
    s._cache = real
    s.BASE = "https://example.invalid/stable"

    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"x": 1}]

    class FakeClient:
        async def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    s._client = FakeClient()

    async def scenario():
        return await s._get("quote", symbol="AAPL"), await s._get("quote", symbol="AAPL")

    a, b = asyncio.run(scenario())
    assert a == b == [{"x": 1}]
    assert calls["n"] == 1
    real.close()
