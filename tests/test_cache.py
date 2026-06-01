import asyncio

import pytest

from shortlist.cache import cache_key, _is_cacheable


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
