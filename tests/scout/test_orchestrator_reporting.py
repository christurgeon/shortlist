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
