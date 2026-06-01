from __future__ import annotations

import pytest
import requests

from shortlist.providers.fmp import FMPProvider


def _make_provider(*, fetch_insider: bool = False, max_retries: int = 2) -> FMPProvider:
    """Build an FMPProvider instance that skips the __init__ key check."""
    p = FMPProvider.__new__(FMPProvider)
    p.key = "test"
    p.timeout = 15
    p.fetch_insider = fetch_insider
    p.max_retries = max_retries
    p._session = None  # tests that touch HTTP set this explicitly
    p._spy_6m = None
    return p


def _http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    if headers:
        resp.headers.update(headers)
    return requests.HTTPError(response=resp)


def test_fmp_fetch_populates_pe_median_5y_from_annual_ratios(monkeypatch):
    provider = _make_provider()

    def mock_get(path, **params):
        if path == "ratios" and params.get("period") == "annual":
            return [
                {"priceToEarningsRatio": 35.0},
                {"priceToEarningsRatio": 25.0},
                {"priceToEarningsRatio": 20.0},
                {"priceToEarningsRatio": 30.0},
                {"priceToEarningsRatio": 28.0},
            ]
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    m = provider.fetch("TEST")
    # sorted: [20, 25, 28, 30, 35] → median (middle of 5) = 28
    assert m.pe_median_5y == 28.0


def test_fmp_fetch_skips_pe_median_when_fewer_than_two_pe_rows(monkeypatch):
    provider = _make_provider()

    def mock_get(path, **params):
        if path == "ratios" and params.get("period") == "annual":
            return [{"priceToEarningsRatio": 30.0}]
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    m = provider.fetch("TEST")
    assert m.pe_median_5y is None


def test_fmp_skips_insider_call_by_default(monkeypatch):
    """The insider-trading endpoint is paid (402 on free plans); EDGAR is the
    free insider source, so we must not waste a request on it by default."""
    provider = _make_provider(fetch_insider=False)
    calls = []

    def mock_get(path, **params):
        calls.append(path)
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    provider.fetch("TEST")
    assert "insider-trading/search" not in calls


def test_fmp_fetches_insider_when_enabled(monkeypatch):
    provider = _make_provider(fetch_insider=True)
    calls = []

    def mock_get(path, **params):
        calls.append(path)
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    provider.fetch("TEST")
    assert "insider-trading/search" in calls


def test_fmp_partial_data_survives_one_leg_429(monkeypatch):
    """A 429 on one endpoint must not discard the legs that already succeeded."""
    provider = _make_provider()

    def mock_get(path, **params):
        if path == "quote":
            return [{"name": "Test Co", "price": 100.0, "marketCap": 5e9}]
        if path == "grades-consensus":
            raise _http_error(429)
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    m = provider.fetch("TEST")
    assert m.market_cap == 5e9  # the quote leg survived the later 429


def test_fmp_raises_when_all_legs_fail(monkeypatch):
    """If every endpoint 429s (e.g. daily quota exhausted), fetch re-raises so
    the coverage layer can classify it rather than returning an empty card."""
    provider = _make_provider()

    def mock_get(path, **params):
        raise _http_error(429)

    monkeypatch.setattr(provider, "_get", mock_get)
    with pytest.raises(requests.HTTPError) as exc:
        provider.fetch("TEST")
    assert exc.value.response.status_code == 429


def test_fmp_get_retries_on_429_then_succeeds(monkeypatch):
    """_get backs off and retries a transient 429 instead of failing on first sight."""
    provider = _make_provider(max_retries=2)
    sleeps = []
    monkeypatch.setattr("shortlist.providers.fmp.time.sleep", lambda s: sleeps.append(s))

    class FakeResp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self.headers = {}
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise _http_error(self.status_code)

        def json(self):
            return self._payload

    responses = [FakeResp(429), FakeResp(200, [{"ok": True}])]

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            return responses.pop(0)

    provider._session = FakeSession()
    out = provider._get("quote", symbol="TEST")
    assert out == [{"ok": True}]
    assert len(sleeps) == 1  # one backoff between the 429 and the success


def test_fmp_rel_strength_caches_benchmark_failure(monkeypatch):
    """A rate-limited SPY benchmark must be fetched once per run, not re-fired (with
    full retries) on every subsequent ticker in the batch."""
    from shortlist.providers.fmp import _rel_strength

    provider = _make_provider()
    spy_calls = []

    def fake_change_6m(ticker):
        if ticker == "SPY":
            spy_calls.append(ticker)
            raise _http_error(429)
        return 0.10  # the stock's own 6m change

    monkeypatch.setattr(provider, "_change_6m", fake_change_6m)
    first = _rel_strength(provider, "AAA")
    second = _rel_strength(provider, "BBB")
    assert len(spy_calls) == 1  # benchmark failure cached, not retried per ticker
    # benchmark pinned to 0.0 -> rel-strength falls back to the stock's own change
    assert first == pytest.approx(0.10)
    assert second == pytest.approx(0.10)


def test_fmp_get_honors_retry_after_header(monkeypatch):
    provider = _make_provider(max_retries=1)
    sleeps = []
    monkeypatch.setattr("shortlist.providers.fmp.time.sleep", lambda s: sleeps.append(s))

    class FakeResp:
        def __init__(self, status, headers=None, payload=None):
            self.status_code = status
            self.headers = headers or {}
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise _http_error(self.status_code)

        def json(self):
            return self._payload

    responses = [FakeResp(429, headers={"Retry-After": "3"}), FakeResp(200, payload=[])]

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            return responses.pop(0)

    provider._session = FakeSession()
    provider._get("quote", symbol="TEST")
    assert sleeps == [3.0]


def test_fmp_fetch_skips_pe_median_when_rows_missing_pe_field(monkeypatch):
    provider = _make_provider()

    def mock_get(path, **params):
        if path == "ratios" and params.get("period") == "annual":
            return [{"priceToEarningsRatio": None}, {"grossProfitMargin": 0.5}]
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    m = provider.fetch("TEST")
    assert m.pe_median_5y is None
