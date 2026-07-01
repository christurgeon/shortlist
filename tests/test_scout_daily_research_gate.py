"""daily.run() with scout.daily_push.research=false: the Claude research phase is skipped,
a coverage note says so, and the session's picks are recorded to the ledger."""
from __future__ import annotations

import json
from datetime import date

from shortlist.models import ScoreCard
from shortlist.scout.models import Emission


class _FakeNotifier:
    def configured(self): return False
    def send_photo(self, *a): return True
    def send_document(self, *a): return True
    def send_message(self, *a): return True


class _ConfiguredNotifier(_FakeNotifier):
    def configured(self): return True


class _StubDiscovery:
    name = "edgar_activist_13d"
    is_discovery = True

    def scan(self, session):
        return [Emission("AAPL", "edgar:activist_13d", 0.9, "Activist 13D: Elliott → AAPL",
                         is_discovery=True),
                Emission("MSFT", "edgar:activist_13d", 0.8, "Activist 13D: Starboard → MSFT",
                         is_discovery=True)]

    def available(self):
        return (True, "2 activist 13D")


def _card(t):
    return ScoreCard(ticker=t, composite=75.0, quality=70.0, moat=65.0, growth=80.0,
                     momentum=60.0, value=55.0, opportunity=60.0, insider=50.0, gates=[])


def test_research_disabled_skips_phase_notes_and_records_picks(tmp_path, monkeypatch, capsys):
    import shortlist.scout.daily as daily_mod
    import shortlist.screen as screen_mod
    import shortlist.scout.notify as notify_mod
    import shortlist.data.macro as macro_mod

    called = {"research": False}

    def spy_research(*a, **k):
        called["research"] = True
        return {}, {}, [], None, {}

    monkeypatch.setattr(daily_mod, "_research_phase", spy_research)
    monkeypatch.setattr(daily_mod, "build_signals",
                        lambda names, kwargs_by_name=None: [_StubDiscovery()])
    monkeypatch.setattr(screen_mod, "run_harness",
                        lambda tickers, sources, config, macro=None: [_card(t) for t in tickers])
    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier())
    monkeypatch.setattr(macro_mod, "fetch_macro", lambda config: None)

    state_file = tmp_path / "scout_state.json"
    config = {
        "scout": {
            "state_path": str(state_file),
            "artifact_dir": str(tmp_path / "scout"),
            "daily_x": 15, "cooldown_days": 7,
            "deep_screen_sources": ["mock"],
            "daily_push": {"enabled": True, "research": False},
            "picks": {"enabled": True},
            "signals": {"edgar_activist_13d": {"enabled": True, "weight": 1.5}},
        },
        "scoring": {}, "gates": {},
    }

    rc = daily_mod.run(config, demo=False, today=date(2026, 5, 29))
    assert rc == 0
    assert called["research"] is False, "research phase must be skipped when disabled"

    out = capsys.readouterr().out
    assert "research disabled by config" in out

    data = json.loads(state_file.read_text())
    recorded = {t for sess in data.get("picks", {}).values() for t in sess}
    assert recorded == {"AAPL", "MSFT"}


def test_successful_delivery_logs_confirmation_line(tmp_path, monkeypatch, capsys):
    """A configured, all-ok delivery emits a positive 'delivered' line to stderr —
    so the systemd journal positively confirms the push landed (not just silence)."""
    import shortlist.scout.daily as daily_mod
    import shortlist.screen as screen_mod
    import shortlist.scout.notify as notify_mod
    import shortlist.data.macro as macro_mod

    monkeypatch.setattr(daily_mod, "_research_phase",
                        lambda *a, **k: ({}, {}, [], None, {}))
    monkeypatch.setattr(daily_mod, "build_signals",
                        lambda names, kwargs_by_name=None: [_StubDiscovery()])
    monkeypatch.setattr(screen_mod, "run_harness",
                        lambda tickers, sources, config, macro=None: [_card(t) for t in tickers])
    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _ConfiguredNotifier())
    monkeypatch.setattr(macro_mod, "fetch_macro", lambda config: None)

    config = {
        "scout": {
            "state_path": str(tmp_path / "scout_state.json"),
            "artifact_dir": str(tmp_path / "scout"),
            "daily_x": 15, "cooldown_days": 7,
            "deep_screen_sources": ["mock"],
            "daily_push": {"enabled": True, "research": False},
            "picks": {"enabled": True},
            "signals": {"edgar_activist_13d": {"enabled": True, "weight": 1.5}},
        },
        "scoring": {}, "gates": {},
    }

    rc = daily_mod.run(config, demo=False, today=date(2026, 5, 29))
    assert rc == 0
    err = capsys.readouterr().err
    assert "delivered 2026-05-29 report to telegram" in err
