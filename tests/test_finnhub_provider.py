from __future__ import annotations

from shortlist.providers.finnhub import FinnhubProvider


def _make_provider() -> FinnhubProvider:
    """Build a FinnhubProvider instance that skips the __init__ key check."""
    p = FinnhubProvider.__new__(FinnhubProvider)
    p.key = "test"
    p.timeout = 15
    return p


def _mock_get_with_metric(metric: dict):
    """Return a _get stub that serves `metric` for stock/metric and empties else."""

    def mock_get(path, **params):
        if path == "stock/metric":
            return {"metric": metric}
        if path == "quote":
            return {}
        if path == "stock/insider-sentiment":
            return {}
        if path == "stock/recommendation":
            return []
        return {}

    return mock_get


def test_finnhub_fetch_maps_market_cap_from_millions(monkeypatch):
    provider = _make_provider()
    # Finnhub reports marketCapitalization in millions of USD.
    monkeypatch.setattr(
        provider, "_get", _mock_get_with_metric({"marketCapitalization": 150391.7})
    )
    m = provider.fetch("TEST")
    assert m.market_cap == 150391.7e6
    assert m.sources.get("market_cap") == "finnhub"


def test_finnhub_fetch_market_cap_none_when_absent(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(provider, "_get", _mock_get_with_metric({"roeTTM": 20.0}))
    m = provider.fetch("TEST")
    assert m.market_cap is None
    assert "market_cap" not in m.sources
