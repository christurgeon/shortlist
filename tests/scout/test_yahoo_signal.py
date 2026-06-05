import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from shortlist.scout import signals as sig_mod
from shortlist.scout.signals import (
    YahooScreenerSignal,
    _is_waf_block,
    _should_retry,
    _yahoo_retry_after_seconds,
    _YAHOO_RETRY_BASE_S,
    _YAHOO_RETRY_MAX_S,
)

FIX = Path(__file__).parent / "fixtures" / "yahoo_day_gainers.json"
WHEN = date(2026, 5, 29)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Single sleep seam: patch the module's ``time.sleep`` to a recorder so no test
    ever actually sleeps. Tests that care read ``recorder``; others ignore it. Requires
    the impl to ``import time`` and call ``time.sleep`` (NOT ``from time import sleep``)."""
    recorder: list[float] = []
    monkeypatch.setattr(sig_mod.time, "sleep", lambda s: recorder.append(s))
    return recorder


# --- response/client helpers -------------------------------------------------

def _screen_payload(symbols):
    quotes = [{"symbol": s, "regularMarketChangePercent": 4.0,
               "regularMarketVolume": 2_000_000, "averageDailyVolume3Month": 1_000_000}
              for s in symbols]
    return {"finance": {"result": [{"quotes": quotes}], "error": None}}


def _client(payload, status=200):
    def handler(request):
        assert "Mozilla" in request.headers.get("user-agent", ""), "must send browser UA"
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _counting_client(responder):
    """responder(request) -> httpx.Response. Records the scrIds param of each call."""
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.params.get("scrIds"))
        return responder(request)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _html_429(text="Too Many Requests"):
    return httpx.Response(429, headers={"content-type": "text/html"}, text=text)


def _json_429(retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(429, json={"finance": {"result": None}}, headers=headers)


# --- pure helper unit tests --------------------------------------------------

def test_is_waf_block_classification():
    assert _is_waf_block(_html_429()) is True
    assert _is_waf_block(httpx.Response(429, content=b"")) is True          # no content-type
    assert _is_waf_block(_json_429()) is False


def test_yahoo_retry_after_seconds():
    assert _yahoo_retry_after_seconds(_json_429(retry_after="2"), 0, _YAHOO_RETRY_BASE_S) == 2.0
    assert _yahoo_retry_after_seconds(_json_429(retry_after="999"), 0, _YAHOO_RETRY_BASE_S) == _YAHOO_RETRY_MAX_S
    assert _yahoo_retry_after_seconds(_json_429(), 0, _YAHOO_RETRY_BASE_S) == _YAHOO_RETRY_BASE_S
    assert _yahoo_retry_after_seconds(_json_429(), 10, _YAHOO_RETRY_BASE_S) == _YAHOO_RETRY_MAX_S


def test_should_retry_biases_ambiguous_to_no_retry():
    assert _should_retry(_html_429()) is False                  # WAF / HTML -> no retry
    assert _should_retry(_json_429()) is False                  # JSON but no Retry-After -> no retry
    assert _should_retry(_json_429(retry_after="1")) is True     # genuine throttle -> retry
    assert _should_retry(httpx.Response(503)) is True            # transient server error
    assert _should_retry(httpx.Response(404)) is False


# --- behavioural tests -------------------------------------------------------

def test_parses_gainers_into_emissions():
    payload = json.loads(FIX.read_text())
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client(payload))
    ems = sig.scan(WHEN)
    syms = {e.ticker for e in ems}
    assert syms == {"ABC", "XYZ"}
    assert all(e.is_discovery for e in ems)
    assert all(0.0 <= e.strength <= 1.0 for e in ems)
    assert sig.available()[0] is True


def test_full_browser_headers_are_sent():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json=_screen_payload(["ABC"]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sig = YahooScreenerSignal(screens=["day_gainers"], client=client)
    sig.scan(WHEN)
    assert "application/json" in seen.get("accept", "")
    assert seen.get("accept-language", "").startswith("en-US")
    assert seen.get("accept-encoding") == "gzip, deflate"
    assert "br" not in seen.get("accept-encoding", "")
    assert seen.get("origin") == "https://finance.yahoo.com"
    assert seen.get("referer") == "https://finance.yahoo.com/"
    assert seen.get("sec-fetch-site") == "same-site"
    assert seen.get("sec-fetch-mode") == "cors"
    assert seen.get("sec-fetch-dest") == "empty"


def test_html_waf_429_is_not_retried():
    client, calls = _counting_client(lambda r: _html_429())
    sig = YahooScreenerSignal(screens=["day_gainers"], client=client)
    assert sig.scan(WHEN) == []
    ran, detail = sig.available()
    assert ran is False
    assert "WAF" in detail
    assert calls == ["day_gainers"]          # exactly one request, no retry
    assert sig.waf_blocked is True


def test_missing_content_type_429_treated_as_waf():
    client, calls = _counting_client(lambda r: httpx.Response(429, content=b""))
    sig = YahooScreenerSignal(screens=["day_gainers"], client=client)
    assert sig.scan(WHEN) == []
    assert sig.available()[0] is False
    assert len(calls) == 1                    # no retry on an ambiguous/headerless 429
    assert sig.waf_blocked is True


def test_json_429_with_retry_after_retries_once_then_bails(no_sleep):
    client, calls = _counting_client(lambda r: _json_429(retry_after="0"))
    sig = YahooScreenerSignal(screens=["day_gainers"], client=client,
                              max_retries=1, retry_base_s=0)
    assert sig.scan(WHEN) == []
    ran, detail = sig.available()
    assert ran is False
    assert len(calls) == 2                    # 1 initial + 1 retry
    assert no_sleep == [0.0]                   # backoff honored the Retry-After
    assert "429" in detail and "JSON" in detail
    assert sig.waf_blocked is False


def test_json_429_without_retry_after_is_not_retried():
    client, calls = _counting_client(lambda r: _json_429())
    sig = YahooScreenerSignal(screens=["day_gainers"], client=client, max_retries=1)
    assert sig.scan(WHEN) == []
    assert len(calls) == 1                    # ambiguous (no Retry-After) -> no retry


def test_blocked_first_screen_skips_remaining_screens():
    client, calls = _counting_client(lambda r: _html_429())
    sig = YahooScreenerSignal(
        screens=["day_gainers", "most_actives", "undervalued_growth_stocks"],
        client=client, inter_screen_delay=0)
    assert sig.scan(WHEN) == []
    assert sig.available()[0] is False
    assert calls == ["day_gainers"]           # by value: bailed on the FIRST, never fired 2/3


def test_partial_success_keeps_earlier_emissions():
    def responder(request):
        if request.url.params.get("scrIds") == "day_gainers":
            return httpx.Response(200, json=_screen_payload(["ABC", "XYZ"]))
        return _html_429()

    client, calls = _counting_client(responder)
    sig = YahooScreenerSignal(screens=["day_gainers", "most_actives"],
                              client=client, inter_screen_delay=0)
    ems = sig.scan(WHEN)
    assert {e.ticker for e in ems} == {"ABC", "XYZ"}      # screen-1 emissions kept
    ran, detail = sig.available()
    assert ran is False                                    # any bail => not clean-complete
    assert "2 hits then" in detail and "bailed after 1/2 screens" in detail
    assert calls == ["day_gainers", "most_actives"]


def test_all_screens_succeed_ran_true_even_with_zero_emissions():
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client(_screen_payload([])))
    assert sig.scan(WHEN) == []
    assert sig.available()[0] is True          # ran reflects fetch success, not emission count


def test_inter_screen_delay_between_not_before_first(no_sleep):
    client, _ = _counting_client(lambda r: httpx.Response(200, json=_screen_payload(["ABC"])))
    sig = YahooScreenerSignal(
        screens=["day_gainers", "most_actives", "undervalued_growth_stocks"],
        client=client, inter_screen_delay=0.5)
    sig.scan(WHEN)
    assert len(no_sleep) == 2                   # n-1 delays for 3 screens, none before the first


def test_blocked_first_screen_has_no_inter_screen_delay(no_sleep):
    client, _ = _counting_client(lambda r: _html_429())
    sig = YahooScreenerSignal(screens=["day_gainers", "most_actives"],
                              client=client, inter_screen_delay=0.5)
    sig.scan(WHEN)
    assert no_sleep == []                        # bailed before any between-screen delay


def test_no_rate_limited_label():
    # The misleading "(rate-limited?)" label is gone on BOTH the JSON-throttle and the
    # HTML-WAF paths.
    json_sig = YahooScreenerSignal(screens=["day_gainers"], client=_client({}, status=429))
    json_sig.scan(WHEN)
    assert "rate-limited?" not in json_sig.available()[1]

    client, _ = _counting_client(lambda r: _html_429())
    waf_sig = YahooScreenerSignal(screens=["day_gainers"], client=client)
    waf_sig.scan(WHEN)
    assert "rate-limited?" not in waf_sig.available()[1]
