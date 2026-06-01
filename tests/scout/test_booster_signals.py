from datetime import date

import httpx
from shortlist.scout.signals import FinnhubNewsSignal, WikipediaAttentionSignal


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finnhub_boosts_only_known_tickers():
    # 12 articles for AAPL -> a confluence emission; booster, not discovery
    def handler(request):
        return httpx.Response(200, json=[{"headline": f"n{i}"} for i in range(12)])
    sig = FinnhubNewsSignal(api_key="k", client=_client(handler))
    ems = sig.scan_for(["AAPL"], date(2026, 5, 29))
    assert len(ems) == 1
    assert ems[0].is_discovery is False
    assert ems[0].ticker == "AAPL"


def test_finnhub_is_not_a_discovery_source():
    sig = FinnhubNewsSignal(api_key="k")
    assert sig.is_discovery is False
    # plain scan() returns nothing — it can't originate candidates
    assert sig.scan(date(2026, 5, 29)) == []


def test_wikipedia_pageview_spike_boosts_mapped_ticker():
    def handler(request):
        # recent window higher than prior -> spike
        items = [{"views": 100} for _ in range(7)] + [{"views": 300} for _ in range(7)]
        return httpx.Response(200, json={"items": items})
    sig = WikipediaAttentionSignal(ticker_map={"AAPL": "Apple_Inc."}, client=_client(handler))
    ems = sig.scan_for(["AAPL"], date(2026, 5, 29))
    assert len(ems) == 1 and ems[0].is_discovery is False
