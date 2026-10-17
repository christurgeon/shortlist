"""Fetching the "Recent news" section: one request, the key in a header, and a feed failure that
reads as "unavailable", never as "no news"."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from shortlist.research import news

NOW = datetime(2026, 10, 16, 14, 2, tzinfo=timezone.utc)
_TS = int(datetime(2026, 10, 15, 12, tzinfo=timezone.utc).timestamp())
CFG = {"research": {"news": {"enabled": True, "lookback_days": 30, "max_items": 10, "timeout": 5}}}
ARTICLES = [{"id": 1, "datetime": _TS, "source": "Benzinga", "url": "https://x.test/1",
             "headline": "Acme sued by FTC over subscription billing", "summary": "The FTC alleges."}]


class _Net:
    def __init__(self, response):
        self.response, self.requests = response, []

    def __call__(self, request):
        self.requests.append(request)
        r = self.response
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def transport(self):
        return httpx.MockTransport(self)


def _card(name="Acme Inc."):
    return SimpleNamespace(ticker="ACME", metrics=SimpleNamespace(name=name))


def _section(net, monkeypatch, card=None, key="k-123"):
    if key:
        monkeypatch.setenv("FINNHUB_API_KEY", key)
    else:
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    return news.fetch_section(card or _card(), CFG, now=NOW, transport=net.transport)


def test_one_request_with_the_key_in_a_header_never_the_url(monkeypatch):
    net = _Net(httpx.Response(200, json=ARTICLES))
    s = _section(net, monkeypatch)
    assert s.status == news.SHOWN and s.as_of == "2026-10-16 14:02 UTC"
    assert [i.headline for i in s.items] == ["Acme sued by FTC over subscription billing"]
    assert (s.covers_from, s.truncated) == ("2026-09-16", False)
    (req,) = net.requests
    assert req.headers["x-finnhub-token"] == "k-123" and "k-123" not in str(req.url)
    assert req.url.params["symbol"] == "ACME"
    assert (req.url.params["from"], req.url.params["to"]) == ("2026-09-16", "2026-10-16")


def test_a_feed_failure_is_unavailable_and_never_raises(monkeypatch, capsys):
    for response in (httpx.Response(429, json={}), httpx.Response(200, json={"error": "x"}),
                     httpx.ConnectError("boom k-123")):
        s = _section(_Net(response), monkeypatch)
        assert (s.items, s.status) == ([], news.UNAVAILABLE)
    assert "k-123" not in capsys.readouterr().err


def test_no_key_is_unavailable_without_a_request(monkeypatch):
    net = _Net(httpx.Response(200, json=ARTICLES))
    assert _section(net, monkeypatch, key=None).status == news.UNAVAILABLE
    assert net.requests == []


def test_no_events_is_none_not_unavailable(monkeypatch):
    quiet = [{**ARTICLES[0], "headline": "Acme Brand Strength to Bolster Growth"}]
    assert _section(_Net(httpx.Response(200, json=quiet)), monkeypatch).status == news.NONE


def test_a_card_without_metrics_falls_back_to_the_ticker(monkeypatch):
    by_ticker = [{**ARTICLES[0], "headline": "ACME recalls widgets after FDA warning"}]
    s = _section(_Net(httpx.Response(200, json=by_ticker)), monkeypatch,
                 card=SimpleNamespace(ticker="ACME"))
    assert s.status == news.SHOWN and len(s.items) == 1


def test_a_short_ticker_with_no_name_needs_a_context_form(monkeypatch):
    bare = [{**ARTICLES[0], "headline": "ON strikes deal with FTC over billing"}]
    framed = [{**ARTICLES[0], "headline": "ON Semiconductor (ON) settles FTC billing probe"}]
    card = SimpleNamespace(ticker="ON", metrics=SimpleNamespace(name=None))
    assert _section(_Net(httpx.Response(200, json=bare)), monkeypatch, card=card).status == news.NONE
    assert _section(_Net(httpx.Response(200, json=framed)), monkeypatch, card=card).status == news.SHOWN


def test_a_capped_feed_says_how_far_back_it_really_covers(monkeypatch):
    day = 86400
    capped = [{**ARTICLES[0], "id": n, "datetime": _TS - (n % 5) * day} for n in range(news.TRUNCATE_AT)]
    s = _section(_Net(httpx.Response(200, json=capped)), monkeypatch)
    assert (s.covers_from, s.truncated) == ("2026-10-11", True)


def test_a_stalled_request_is_abandoned_at_the_deadline(monkeypatch):
    import time

    def slow(request):
        time.sleep(3)
        return httpx.Response(200, json=ARTICLES)
    monkeypatch.setenv("FINNHUB_API_KEY", "k-123")
    cfg = {"research": {"news": {**CFG["research"]["news"], "timeout": 0.3}}}
    t0 = time.monotonic()
    s = news.fetch_section(_card(), cfg, now=NOW, transport=httpx.MockTransport(slow))
    assert s.status == news.UNAVAILABLE and time.monotonic() - t0 < 2
