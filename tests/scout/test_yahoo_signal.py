import json
from datetime import date
from pathlib import Path

import httpx
from shortlist.scout.signals import YahooScreenerSignal

FIX = Path(__file__).parent / "fixtures" / "yahoo_day_gainers.json"


def _client(payload, status=200):
    def handler(request):
        assert "Mozilla" in request.headers.get("user-agent", ""), "must send browser UA"
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_gainers_into_emissions():
    payload = json.loads(FIX.read_text())
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client(payload))
    ems = sig.scan(date(2026, 5, 29))
    syms = {e.ticker for e in ems}
    assert syms == {"ABC", "XYZ"}
    assert all(e.is_discovery for e in ems)
    assert all(0.0 <= e.strength <= 1.0 for e in ems)
    assert sig.available()[0] is True


def test_429_degrades_gracefully():
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client({}, status=429))
    assert sig.scan(date(2026, 5, 29)) == []
    ran, detail = sig.available()
    assert ran is False and "429" in detail or "rate" in detail.lower()
