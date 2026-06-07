"""Unit tests for correctness/robustness fixes applied to daily.run().

Covers:
  FIX 1 — disabled-signal coverage honesty (quiver ✗ (disabled))
  FIX 2 — discovery loop guard (scan() raises → run still completes, rc 0)
  FIX 3 — Telegram failure visibility:
           configured + delivery fails → rc 2, state completed, manifest note
           unconfigured + delivery fails → rc 0 (stdout fallback expected)
"""
from __future__ import annotations

import json
from datetime import date


from shortlist.models import ScoreCard
from shortlist.scout.models import Emission


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeNotifier:
    def __init__(self, configured=True, ok=True):
        self._c, self._ok = configured, ok
    def configured(self): return self._c
    def send_photo(self, *a): return self._ok
    def send_document(self, *a): return self._ok
    def send_message(self, *a): return self._ok


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


def _base_config(tmp_path, extra_signals: dict | None = None) -> dict:
    """Minimal config with state + artifact dirs under tmp_path."""
    signals = {
        "stub_discovery": {"enabled": True, "weight": 1.0},
        "quiver":         {"enabled": False, "weight": 0.5},
    }
    if extra_signals:
        signals.update(extra_signals)
    return {
        "scout": {
            "state_path": str(tmp_path / "scout_state.json"),
            "artifact_dir": str(tmp_path / "scout"),
            "daily_x": 15,
            "cooldown_days": 7,
            "deep_screen_sources": ["mock"],
            "research_top_n": 0,
            "research_phase_budget_s": 1,
            "daily_push": {"enabled": True},
            "signals": signals,
        },
        "scoring": {},
        "gates": {},
    }


class _OkDiscoverySignal:
    """Emits two tickers; never raises."""
    name = "stub_discovery"
    is_discovery = True

    def scan(self, session: date) -> list[Emission]:
        return [
            Emission("AAPL", "stub:discovery", 0.8, "test", is_discovery=True),
            Emission("MSFT", "stub:discovery", 0.7, "test", is_discovery=True),
        ]

    def available(self) -> tuple[bool, str]:
        return (True, "2 hits")


class _RaisingDiscoverySignal:
    """Scan always raises — used for FIX 2."""
    name = "bad_discovery"
    is_discovery = True

    def scan(self, session: date) -> list[Emission]:
        raise RuntimeError("simulated scan failure")

    def available(self) -> tuple[bool, str]:  # should not be reached
        return (True, "unreachable")


def _patch_harness(monkeypatch):
    import shortlist.screen as screen_mod
    monkeypatch.setattr(screen_mod, "run_harness",
                        lambda tickers, sources, config: [_make_card(t) for t in tickers])


# ---------------------------------------------------------------------------
# FIX 1 — disabled signal shows up in report / manifest
# ---------------------------------------------------------------------------

def test_disabled_signal_appears_in_manifest(tmp_path, monkeypatch):
    """quiver is disabled → manifest.signals must contain quiver with ran=False/disabled."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    import shortlist.scout.daily as daily_mod

    # build_signals returns only the enabled stub; quiver never gets instantiated
    def fake_build_signals(names, kwargs_by_name=None):
        return [_OkDiscoverySignal()]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)
    _patch_harness(monkeypatch)

    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    config = _base_config(tmp_path)
    today = date(2026, 5, 29)

    rc = daily_mod.run(config, demo=False, today=today)
    assert rc == 0

    # Check the written manifest JSON
    manifest_file = list((tmp_path / "scout").glob("*/manifest.json"))[0]
    manifest = json.loads(manifest_file.read_text())
    signal_names = {s["name"]: s for s in manifest["signals"]}

    assert "quiver" in signal_names, f"quiver missing from manifest signals: {signal_names}"
    quiver_status = signal_names["quiver"]
    assert quiver_status["ran"] is False
    assert quiver_status["detail"] == "disabled"


def test_disabled_signal_appears_in_rendered_report(tmp_path, monkeypatch):
    """The rendered .txt report must include 'quiver ✗ (disabled)'."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    import shortlist.scout.daily as daily_mod

    def fake_build_signals(names, kwargs_by_name=None):
        return [_OkDiscoverySignal()]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)
    _patch_harness(monkeypatch)

    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    config = _base_config(tmp_path)
    today = date(2026, 5, 29)

    daily_mod.run(config, demo=False, today=today)

    report_file = list((tmp_path / "scout").glob("*/report.txt"))[0]
    report_text = report_file.read_text()
    assert "quiver" in report_text
    assert "disabled" in report_text


# ---------------------------------------------------------------------------
# FIX 2 — discovery loop guard: raising signal → run still completes (rc 0)
# ---------------------------------------------------------------------------

def test_discovery_signal_raising_does_not_abort_run(tmp_path, monkeypatch):
    """A discovery signal whose scan() raises must not abort the whole run."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    import shortlist.scout.daily as daily_mod

    def fake_build_signals(names, kwargs_by_name=None):
        # Both signals: one raises, one is fine
        return [_RaisingDiscoverySignal(), _OkDiscoverySignal()]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)
    _patch_harness(monkeypatch)

    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    config = _base_config(tmp_path, extra_signals={
        "bad_discovery": {"enabled": True, "weight": 1.0},
    })
    today = date(2026, 5, 29)

    rc = daily_mod.run(config, demo=False, today=today)
    assert rc == 0, "run() must complete successfully even when a discovery signal raises"

    # The failing signal should appear in the manifest as ran=False
    manifest_file = list((tmp_path / "scout").glob("*/manifest.json"))[0]
    manifest = json.loads(manifest_file.read_text())
    signal_names = {s["name"]: s for s in manifest["signals"]}

    assert "bad_discovery" in signal_names
    bad = signal_names["bad_discovery"]
    assert bad["ran"] is False
    assert "simulated scan failure" in bad["detail"]


# ---------------------------------------------------------------------------
# FIX 3 — Telegram failure: configured + fails → rc 2 + state complete + note
# ---------------------------------------------------------------------------

def test_telegram_configured_fails_returns_2_and_marks_complete(tmp_path, monkeypatch):
    """Configured Telegram that fails to deliver → rc 2, state marked complete, manifest note."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    import shortlist.scout.daily as daily_mod

    def fake_build_signals(names, kwargs_by_name=None):
        return [_OkDiscoverySignal()]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)
    _patch_harness(monkeypatch)

    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=True, ok=False))

    config = _base_config(tmp_path)
    today = date(2026, 5, 29)

    rc = daily_mod.run(config, demo=False, today=today)
    assert rc == 2, f"expected rc=2 for configured-but-failed Telegram, got {rc}"

    # State must be marked completed (idempotency anchor)
    from shortlist.scout.state import ScoutState
    state = ScoutState(tmp_path / "scout_state.json")
    from shortlist.scout.calendar import last_session
    session = last_session(today)
    assert state.run_completed(session), "state.mark_run_completed() must still be called"

    # Manifest must contain the delivery failure note
    manifest_file = list((tmp_path / "scout").glob("*/manifest.json"))[0]
    manifest = json.loads(manifest_file.read_text())
    notes_text = " ".join(manifest.get("notes", []))
    assert "telegram delivery failed" in notes_text, f"note missing; notes={manifest.get('notes')}"
    assert "configured" in notes_text


def test_telegram_unconfigured_fails_returns_0(tmp_path, monkeypatch):
    """Unconfigured Telegram (no env vars) + delivery False → rc 0 (stdout fallback expected)."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    import shortlist.scout.daily as daily_mod

    def fake_build_signals(names, kwargs_by_name=None):
        return [_OkDiscoverySignal()]

    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)
    _patch_harness(monkeypatch)

    import shortlist.scout.notify as notify_mod
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    config = _base_config(tmp_path)
    today = date(2026, 5, 29)

    rc = daily_mod.run(config, demo=False, today=today)
    assert rc == 0, f"unconfigured Telegram should keep rc=0, got {rc}"

    # No "telegram delivery failed" note in manifest
    manifest_file = list((tmp_path / "scout").glob("*/manifest.json"))[0]
    manifest = json.loads(manifest_file.read_text())
    notes_text = " ".join(manifest.get("notes", []))
    assert "telegram delivery failed" not in notes_text


# ---------------------------------------------------------------------------
# Yahoo WAF cross-run cooldown — the ban-safety wiring in daily.run()
# ---------------------------------------------------------------------------

class _YahooWafBlockedSignal:
    """Stands in for YahooScreenerSignal hitting a WAF block: sets waf_blocked
    and reports a degraded status, exactly as the real signal does on an HTML 429."""
    name = "yahoo_screener"
    is_discovery = True

    def __init__(self):
        self.waf_blocked = False

    def scan(self, session: date) -> list[Emission]:
        self.waf_blocked = True
        return []

    def available(self) -> tuple[bool, str]:
        return (False, "HTTP 429 WAF-blocked (HTML); bailed after 0/3 screens")


class _YahooMustNotScanSignal:
    """Fails loudly if scan() is called — proves the cooldown skips it with zero requests."""
    name = "yahoo_screener"
    is_discovery = True
    waf_blocked = False

    def scan(self, session: date) -> list[Emission]:
        raise AssertionError("yahoo scan() must not be called while WAF cooldown is active")

    def available(self) -> tuple[bool, str]:  # pragma: no cover - should not be reached
        return (True, "unreachable")


def _yahoo_config(tmp_path):
    return _base_config(tmp_path, extra_signals={
        "yahoo_screener": {"enabled": True, "weight": 1.0},
    })


def test_yahoo_waf_block_persists_rest_of_day_cooldown(tmp_path, monkeypatch):
    """A WAF-blocked yahoo scan must persist a rest-of-day cooldown in ScoutState."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")
    import shortlist.scout.daily as daily_mod
    import shortlist.scout.notify as notify_mod
    from shortlist.scout.state import ScoutState

    monkeypatch.setattr(daily_mod, "build_signals",
                        lambda names, kwargs_by_name=None: [_YahooWafBlockedSignal(), _OkDiscoverySignal()])
    _patch_harness(monkeypatch)
    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier(configured=False))

    config = _yahoo_config(tmp_path)
    session = date(2026, 5, 29)
    rc = daily_mod.run(config, demo=False, today=session)
    assert rc == 0

    state = ScoutState(tmp_path / "scout_state.json")     # fresh read from disk
    assert state.yahoo_blocked_on(session) is True
    assert state.yahoo_blocked_on(date(2026, 5, 30)) is False  # next day resumes


def test_yahoo_cooldown_skips_scan_with_zero_requests(tmp_path, monkeypatch):
    """With an active cooldown, the yahoo signal is skipped (scan never called) and the
    coverage line records the skip."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")
    import shortlist.scout.daily as daily_mod
    import shortlist.scout.notify as notify_mod
    from shortlist.scout.state import ScoutState

    state_path = tmp_path / "scout_state.json"
    session = date(2026, 5, 29)
    ScoutState(state_path).mark_yahoo_blocked(session)     # pre-seed the cooldown

    monkeypatch.setattr(daily_mod, "build_signals",
                        lambda names, kwargs_by_name=None: [_YahooMustNotScanSignal(), _OkDiscoverySignal()])
    _patch_harness(monkeypatch)
    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier(configured=False))

    config = _yahoo_config(tmp_path)
    rc = daily_mod.run(config, demo=False, today=session)  # _YahooMustNotScanSignal raises if scanned
    assert rc == 0

    manifest = json.loads(list((tmp_path / "scout").glob("*/manifest.json"))[0].read_text())
    yahoo = {s["name"]: s for s in manifest["signals"]}["yahoo_screener"]
    assert yahoo["ran"] is False
    assert "cooldown" in yahoo["detail"]
