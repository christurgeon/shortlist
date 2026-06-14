"""FMPSource._get: Retry-After-aware 429/5xx backoff (config: fmp.max_retries).

The downstream rate_limited_429 coverage machinery and the fmp.max_retries config
knob predated the actual retry loop; these pin the loop's behavior.
"""
import asyncio

import httpx
import pytest

from shortlist.cache import NoOpCache
from shortlist.data.sources import FMPSource


def _src(handler, *, max_retries=2):
    src = FMPSource(api_key="k", cache=NoOpCache(),
                    config={"fmp": {"max_retries": max_retries}})
    # Swap the real AsyncClient for one wired to a deterministic mock transport.
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return src


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast(_):  # don't actually wait during the backoff
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)


def test_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="rate limited")
        return httpx.Response(200, json=[{"ok": True}])

    src = _src(handler)
    out = asyncio.run(src._get("quote", symbol="AAPL"))
    assert out == [{"ok": True}]
    assert calls["n"] == 2  # one retry after the 429


def test_retries_transient_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"ok": True})

    src = _src(handler)
    out = asyncio.run(src._get("quote", symbol="AAPL"))
    assert out == {"ok": True}
    assert calls["n"] == 2


def test_gives_up_after_max_retries_and_raises_5xx():
    # A persistent 5xx exhausts retries and raises a 503-bearing error. (coverage_adapt
    # maps that to the generic "error" status, NOT rate_limited_429 — only 429 does.)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    src = _src(handler, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError) as ei:
        asyncio.run(src._get("quote", symbol="AAPL"))
    assert "503" in str(ei.value)
    assert calls["n"] == 3  # initial + max_retries


def test_retry_after_http_date_header_does_not_abort_retry():
    # RFC-7231 allows an HTTP-date Retry-After (an intermediary 5xx can emit it). A bare
    # float() would raise ValueError BEFORE retrying; the shared parser must tolerate it
    # so the retry still happens and a recovered call returns 200.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"},
                                  text="unavailable")
        return httpx.Response(200, json={"ok": True})

    src = _src(handler)
    out = asyncio.run(src._get("quote", symbol="AAPL"))
    assert out == {"ok": True}
    assert calls["n"] == 2  # date-form header did not defeat the retry


def test_gives_up_after_max_retries_and_raises_429():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    src = _src(handler, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError) as ei:
        asyncio.run(src._get("quote", symbol="AAPL"))
    assert "429" in str(ei.value)
    assert calls["n"] == 3  # initial + max_retries


def test_does_not_retry_402_gating():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(402, text="Special Endpoint")

    src = _src(handler, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError) as ei:
        asyncio.run(src._get("quote", symbol="AAPL"))
    assert "402" in str(ei.value)
    assert calls["n"] == 1  # gating is not retriable — it won't clear


def test_default_max_retries_when_config_absent():
    # No config -> default of 2 retries (3 attempts total).
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    src = FMPSource(api_key="k", cache=NoOpCache())
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(src._get("quote", symbol="AAPL"))
    assert calls["n"] == 3
