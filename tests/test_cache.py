import asyncio

import pytest

from shortlist.cache import cache_key, _is_cacheable, ttl_for, _BUCKET_BY_KEY


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
