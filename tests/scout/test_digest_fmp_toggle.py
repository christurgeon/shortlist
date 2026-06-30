from datetime import date
from shortlist.scout import daily as daily_mod
import shortlist.screen as screen_mod
import shortlist.scout.notify as notify_mod


class _StubSignal:
    # is_discovery=True is REQUIRED: daily.run() filters discovery signals via
    # getattr(s, "is_discovery", False) (daily.py:178); without it scan() is never called.
    name = "stub_discovery"
    is_discovery = True
    def available(self):
        return (True, "ok")
    def scan(self, session):
        from shortlist.scout.models import Emission
        # Real Emission fields: (ticker, signal, strength, evidence, is_discovery, cik=None)
        return [Emission("AAPL", "stub:discovery", 0.8, "x", is_discovery=True)]


class _FakeNotifier:
    # deliver() calls notifier.configured() as a METHOD (notify.py:127), not an attribute.
    def __init__(self, configured=False):
        self._c = configured
    def configured(self):
        return self._c
    def send_photo(self, *a, **k):
        return True
    def send_document(self, *a, **k):
        return True
    def send_message(self, *a, **k):
        return True


def _run_capturing_sources(monkeypatch, tmp_path, *, include_fmp, base):
    """Drive daily.run() with stubbed discovery + run_harness; return (config, seen)."""
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")
    monkeypatch.setattr(daily_mod, "build_signals",
                        lambda names, kwargs_by_name=None: [_StubSignal()])

    seen = {}
    def fake_run_harness(tickers, sources, config, macro=None):
        seen["sources"] = list(sources)
        return []  # empty cards: run() still completes + prints the journal fallback
    monkeypatch.setattr(screen_mod, "run_harness", fake_run_harness)
    monkeypatch.setattr(notify_mod, "TelegramNotifier",
                        lambda: _FakeNotifier(configured=False))

    config = {
        "scout": {
            "state_path": str(tmp_path / "state.json"),
            "artifact_dir": str(tmp_path / "scout"),
            "daily_x": 15,
            "deep_screen_sources": base,
            "research_top_n": 0,
            "daily_push": {"enabled": True, "research": False, "include_fmp": include_fmp},
            "signals": {"stub_discovery": {"enabled": True, "weight": 1.0}},
        },
        "scoring": {}, "gates": {},
    }
    return config, seen


def test_run_rations_fmp_when_disabled(tmp_path, monkeypatch, capsys):
    base = ["yahoo", "fmp", "finnhub", "edgar"]
    config, seen = _run_capturing_sources(monkeypatch, tmp_path, include_fmp=False, base=base)
    daily_mod.run(config, demo=False, today=date(2026, 5, 29))
    assert "fmp" not in seen["sources"], f"fmp should be rationed: {seen['sources']}"
    assert "Free-source screen" in capsys.readouterr().out


def test_run_keeps_fmp_when_enabled(tmp_path, monkeypatch, capsys):
    base = ["yahoo", "fmp", "finnhub", "edgar"]
    config, seen = _run_capturing_sources(monkeypatch, tmp_path, include_fmp=True, base=base)
    daily_mod.run(config, demo=False, today=date(2026, 5, 29))
    assert "fmp" in seen["sources"]
    assert "Free-source screen" not in capsys.readouterr().out


def test_run_defaults_to_fmp_when_flag_absent(tmp_path, monkeypatch, capsys):
    base = ["yahoo", "fmp", "finnhub", "edgar"]
    config, seen = _run_capturing_sources(monkeypatch, tmp_path, include_fmp=True, base=base)
    # remove the flag entirely -> default must keep fmp (back-compat)
    del config["scout"]["daily_push"]["include_fmp"]
    daily_mod.run(config, demo=False, today=date(2026, 5, 29))
    assert "fmp" in seen["sources"]
    assert "Free-source screen" not in capsys.readouterr().out
