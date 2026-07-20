"""fmp.fetch_insider config knob: skip the paid insider-trading/search request.

config.yaml ships `fetch_insider: false` because the /stable/ insider endpoint is
paid (402 on free plans) and EDGAR is the free authoritative source — the request
is guaranteed to fail yet still burns one of the ~13 FMP calls/ticker against the
250/day quota. These pin that the knob actually controls the fetch (it was a
documented-but-dead key) and that an absent key keeps the old fetch-everything
behavior (back-compat).
"""
import asyncio

import httpx

from shortlist.cache import NoOpCache
from shortlist.data.sources import FMPSource


def _run_fetch(config):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=[])

    src = FMPSource(api_key="k", cache=NoOpCache(), config=config)
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(src.fetch("AAPL"))
    return paths


def test_fetch_insider_false_skips_the_insider_request():
    paths = _run_fetch({"fmp": {"fetch_insider": False}})
    assert paths, "fetch made no requests at all"
    assert not any("insider-trading" in p for p in paths)


def test_fetch_insider_absent_keeps_the_request_backcompat():
    paths = _run_fetch({"fmp": {}})
    assert any("insider-trading" in p for p in paths)


def test_fetch_insider_true_keeps_the_request():
    paths = _run_fetch({"fmp": {"fetch_insider": True}})
    assert any("insider-trading" in p for p in paths)
