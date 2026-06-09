"""Integration test: daily.run() with stubbed signals and harness (FIX 5).

Drives the real orchestrator path (non-demo) to verify that:
  - The booster's scan_for() is actually called with the discovered tickers.
  - The run returns 0.
  - No real network calls are made (everything is monkeypatched).
"""
from __future__ import annotations

from datetime import date


from shortlist.models import ScoreCard
from shortlist.scout.models import Emission


# ---------------------------------------------------------------------------
# Stub signal classes
# ---------------------------------------------------------------------------

class _FakeNotifier:
    def __init__(self, configured=True, ok=True):
        self._c, self._ok = configured, ok
    def configured(self): return self._c
    def send_photo(self, *a): return self._ok
    def send_document(self, *a): return self._ok
    def send_message(self, *a): return self._ok


class StubDiscoverySignal:
    """Minimal discovery signal that emits a fixed set of tickers."""
    name = "stub_discovery"
    is_discovery = True

    def __init__(self):
        self._called = False

    def scan(self, session: date) -> list[Emission]:
        self._called = True
        return [
            Emission("AAPL", "stub:discovery", 0.8, "test emission", is_discovery=True),
            Emission("MSFT", "stub:discovery", 0.7, "test emission", is_discovery=True),
        ]

    def available(self) -> tuple[bool, str]:
        return (True, "2 hits")


class StubBoosterSignal:
    """Booster that records the tickers it was called with."""
    name = "stub_booster"
    is_discovery = False

    def __init__(self):
        self.scan_for_calls: list[list[str]] = []

    def scan(self, session: date) -> list[Emission]:
        return []

    def scan_for(self, tickers: list[str], session: date) -> list[Emission]:
        self.scan_for_calls.append(list(tickers))
        # Boost AAPL to verify the fold actually happens
        return [
            Emission("AAPL", "stub:boost", 0.9, "boosted", is_discovery=False),
        ]

    def available(self) -> tuple[bool, str]:
        return (True, "scanned")


# ---------------------------------------------------------------------------
# Helper to build canned ScoreCards
# ---------------------------------------------------------------------------

def _make_card(ticker: str) -> ScoreCard:
    return ScoreCard(
        ticker=ticker,
        composite=75.0,
        quality=70.0,
        moat=65.0,
        growth=80.0,
        momentum=60.0,
        value=55.0,
        opportunity=60.0,
        insider=50.0,
        gates=[],
    )


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

def test_run_calls_booster_scan_for_with_discovered_tickers(tmp_path, monkeypatch):
    """daily.run() non-demo: booster.scan_for() receives the discovered tickers."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    stub_discovery = StubDiscoverySignal()
    stub_booster = StubBoosterSignal()

    # Monkeypatch build_signals to return our stubs
    import shortlist.scout.daily as daily_mod

    def fake_build_signals(names, kwargs_by_name=None):
        return [stub_discovery, stub_booster]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)

    # Monkeypatch run_harness to return canned ScoreCards without network calls.
    # The import is lazy (inside run()), so patch the source module directly.
    import shortlist.screen as screen_mod

    def fake_run_harness(tickers, sources, config, macro=None):
        return [_make_card(t) for t in tickers]

    monkeypatch.setattr(screen_mod, "run_harness", fake_run_harness)

    # Monkeypatch TelegramNotifier so no real Telegram call is made
    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    # Build minimal config with state_path under tmp_path
    state_file = tmp_path / "scout_state.json"
    config = {
        "scout": {
            "state_path": str(state_file),
            "artifact_dir": str(tmp_path / "scout"),
            "daily_x": 15,
            "cooldown_days": 7,
            "deep_screen_sources": ["mock"],
            "research_top_n": 0,
            "research_phase_budget_s": 1,
            "daily_push": {"enabled": True},
            "signals": {
                "stub_discovery": {"enabled": True, "weight": 1.0},
                "stub_booster":   {"enabled": True, "weight": 0.5},
            },
        },
        "scoring": {},
        "gates": {},
    }

    # Use a real past trading day so last_session() gives a stable result
    today = date(2026, 5, 29)  # a Friday (trading day)

    rc = daily_mod.run(config, demo=False, today=today)

    assert rc == 0, "run() should return 0 on success"

    # The booster must have been called with the discovered tickers
    assert stub_booster.scan_for_calls, "booster.scan_for() was never called"
    discovered_in_call = set(stub_booster.scan_for_calls[0])
    assert "AAPL" in discovered_in_call, f"AAPL missing from booster call: {discovered_in_call}"
    assert "MSFT" in discovered_in_call, f"MSFT missing from booster call: {discovered_in_call}"
