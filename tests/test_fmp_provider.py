from __future__ import annotations

import pytest

from shortlist.providers.fmp import FMPProvider


def _make_provider() -> FMPProvider:
    """Build an FMPProvider instance that skips the __init__ key check."""
    p = FMPProvider.__new__(FMPProvider)
    p.key = "test"
    p.timeout = 15
    p._spy_6m = None
    return p


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


def test_fmp_fetch_skips_pe_median_when_rows_missing_pe_field(monkeypatch):
    provider = _make_provider()

    def mock_get(path, **params):
        if path == "ratios" and params.get("period") == "annual":
            return [{"priceToEarningsRatio": None}, {"grossProfitMargin": 0.5}]
        return []

    monkeypatch.setattr(provider, "_get", mock_get)
    m = provider.fetch("TEST")
    assert m.pe_median_5y is None
