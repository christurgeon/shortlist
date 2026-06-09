import json
from datetime import date
from pathlib import Path
import yaml
import shortlist.scout.daily as daily

_CONFIG = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text())


def _cfg(tmp_path):
    cfg = dict(_CONFIG)                       # real thresholds/scoring/gates from config.yaml
    cfg["scout"] = dict(cfg.get("scout", {}))
    cfg["scout"].update(artifact_dir="scout", state_path="state/s.json")
    return cfg


def test_demo_run_prints_text_and_no_pillow(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = daily.run(_cfg(tmp_path), demo=True, today=date(2026, 6, 4))
    assert rc == 0
    assert "Scout shortlist" in capsys.readouterr().out


def test_assessment_record_loader_reads_json(tmp_path):
    rec = {"business_model_summary": "Chips.", "thesis": {"bull_case": "AI"}}
    p = tmp_path / "AAPL" / "abc.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(rec))
    md = str(p).replace(".json", ".md")
    assert daily._assessment_record_from_file(md)["thesis"]["bull_case"] == "AI"


def test_live_run_configured_delivers_photo_and_document(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("PIL")  # the "photo" assertion needs the Pillow glance renderer
    import shortlist.scout.notify as notify_mod
    import shortlist.screen as screen_mod
    from shortlist.models import ScoreCard
    from shortlist.scout.models import Emission

    calls = []

    class _Rec:
        def configured(self): return True
        def send_photo(self, png, cap): calls.append("photo"); return True
        def send_document(self, data, fn, cap): calls.append("doc"); return True
        def send_message(self, text): calls.append("msg"); return True

    class _OkSignal:
        name = "stub_discovery"
        is_discovery = True
        def scan(self, session): return [
            Emission("AAPL", "stub:discovery", 0.8, "test", is_discovery=True),
            Emission("MSFT", "stub:discovery", 0.7, "test", is_discovery=True),
        ]
        def available(self): return (True, "2 hits")

    def fake_build_signals(names, kwargs_by_name=None):
        return [_OkSignal()]

    def fake_run_harness(tickers, sources, config, macro=None):
        return [ScoreCard(ticker=t, composite=75.0, quality=70.0, moat=65.0,
                          growth=80.0, momentum=60.0, value=55.0, opportunity=60.0,
                          insider=50.0, gates=[]) for t in tickers]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _Rec())
    monkeypatch.setattr(daily, "build_signals", fake_build_signals)
    monkeypatch.setattr(screen_mod, "run_harness", fake_run_harness)
    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")

    cfg = _cfg(tmp_path)
    cfg["scout"] = dict(cfg["scout"])
    cfg["scout"]["signals"] = {"stub_discovery": {"enabled": True, "weight": 1.0}}
    cfg["scout"]["deep_screen_sources"] = ["mock"]
    cfg["scout"]["daily_push"] = {"enabled": True}   # opt into the live delivery path (Task 1 flag)

    rc = daily.run(cfg, demo=False, today=date(2026, 6, 4))
    assert rc == 0
    assert "photo" in calls and "doc" in calls            # both artifacts delivered
    assert (tmp_path / "scout" / "2026-06-04" / "report.html").exists()
