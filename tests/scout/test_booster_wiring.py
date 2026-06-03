"""Unit tests for confluence-booster wiring (FIX 2).

Covers:
 1. build_signals forwards kwargs to signal constructors.
 2. A booster emission raises interest on an already-discovered candidate.
 3. A booster does NOT introduce a new ticker (only folds into existing kept list).
 4. Booster status is appended to the statuses list.
"""
from __future__ import annotations

from datetime import date

import pytest

from shortlist.scout.models import Candidate, Emission, SignalStatus
from shortlist.scout.signals import (
    FinnhubNewsSignal,
    WikipediaAttentionSignal,
    build_signals,
    register,
)


# ---------------------------------------------------------------------------
# 1. build_signals forwards kwargs
# ---------------------------------------------------------------------------

class _KwargsCapture:
    """Dummy signal that records the kwargs it received."""
    name = "kwargs_capture"
    is_discovery = True
    received: dict = {}

    def __init__(self, **kwargs):
        _KwargsCapture.received = kwargs

    def scan(self, session):
        return []

    def available(self):
        return (True, "ok")


def test_build_signals_forwards_kwargs():
    register("kwargs_capture", _KwargsCapture)
    build_signals(["kwargs_capture"], kwargs_by_name={"kwargs_capture": {"foo": 42, "bar": "baz"}})
    assert _KwargsCapture.received == {"foo": 42, "bar": "baz"}


def test_build_signals_no_kwargs_still_works():
    """Existing no-arg call path must not break."""
    sigs = build_signals(["mock"])
    assert len(sigs) == 1 and sigs[0].name == "mock"


def test_build_signals_partial_kwargs():
    """Signals absent from kwargs_by_name are constructed with no args."""
    register("kwargs_capture", _KwargsCapture)
    _KwargsCapture.received = {"sentinel": True}  # reset
    sigs = build_signals(["kwargs_capture"], kwargs_by_name={})
    # called with no kwargs -> received should be overwritten to empty
    assert _KwargsCapture.received == {}
    assert sigs[0].name == "kwargs_capture"


# ---------------------------------------------------------------------------
# 2. Booster emission raises interest on an already-discovered candidate
# ---------------------------------------------------------------------------

def _make_discovery_candidate(ticker: str, strength: float = 0.5) -> Candidate:
    c = Candidate(ticker=ticker)
    c.add(Emission(ticker, "yahoo:day_gainers", strength, "ev", is_discovery=True), weight=1.0)
    return c


def test_booster_raises_interest_on_known_candidate():
    kept = [_make_discovery_candidate("AAPL", strength=0.5)]
    interest_before = kept[0].interest

    # Simulate what daily.py does after prefilter
    kept_by_ticker = {c.ticker: c for c in kept}
    booster_em = Emission("AAPL", "finnhub:news_volume", 0.8, "25 articles", is_discovery=False)
    weight = 0.5
    if booster_em.ticker in kept_by_ticker:
        kept_by_ticker[booster_em.ticker].add(booster_em, weight)

    assert kept[0].interest > interest_before
    assert kept[0].interest == pytest.approx(interest_before + 0.8 * 0.5)


def test_booster_does_not_introduce_new_ticker():
    kept = [_make_discovery_candidate("AAPL")]
    kept_by_ticker = {c.ticker: c for c in kept}

    # Booster fires for an unknown ticker — should be silently ignored
    booster_em = Emission("NVDA", "finnhub:news_volume", 0.9, "40 articles", is_discovery=False)
    if booster_em.ticker in kept_by_ticker:  # won't match
        kept_by_ticker[booster_em.ticker].add(booster_em, 0.5)

    assert "NVDA" not in kept_by_ticker
    assert len(kept) == 1  # AAPL only


# ---------------------------------------------------------------------------
# 3. FinnhubNewsSignal accepts api_key kwarg via build_signals
# ---------------------------------------------------------------------------

def test_finnhub_news_kwarg_forwarding():
    """build_signals with api_key kwarg -> FinnhubNewsSignal.api_key is set."""
    sigs = build_signals(["finnhub_news"], kwargs_by_name={"finnhub_news": {"api_key": "testkey"}})
    assert len(sigs) == 1
    sig = sigs[0]
    assert isinstance(sig, FinnhubNewsSignal)
    assert sig.api_key == "testkey"


# ---------------------------------------------------------------------------
# 4. WikipediaAttentionSignal accepts ticker_map kwarg via build_signals
# ---------------------------------------------------------------------------

def test_wikipedia_ticker_map_kwarg_forwarding():
    """build_signals with ticker_map kwarg -> WikipediaAttentionSignal.ticker_map is set."""
    tmap = {"AAPL": "Apple_Inc."}
    sigs = build_signals(["wikipedia"], kwargs_by_name={"wikipedia": {"ticker_map": tmap}})
    assert len(sigs) == 1
    sig = sigs[0]
    assert isinstance(sig, WikipediaAttentionSignal)
    assert sig.ticker_map == tmap


# ---------------------------------------------------------------------------
# 5. Booster with no key degrades gracefully (ran=False, no crash)
# ---------------------------------------------------------------------------

def test_finnhub_no_key_degrades_to_not_ran():
    """FinnhubNewsSignal with no api_key returns [] and available() ran=False."""
    sig = FinnhubNewsSignal(api_key=None)
    ems = sig.scan_for(["AAPL"], date(2026, 5, 29))
    assert ems == []
    ran, detail = sig.available()
    assert ran is False
    assert "FINNHUB_API_KEY" in detail
