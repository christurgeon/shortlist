"""How the "Peer comparison" section reaches the brief: after the model call, never in the
prompt, recorded in the brief JSON, and hashed with the brief's wording."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from shortlist import research
from shortlist.config import load_config
from shortlist.research import assess as assess_mod
from shortlist.research import cachekey, capital_allocation, peers, report
from shortlist.research.models import (FilingBundle, FilingText, Moat, PeerRow, PeerTable,
                                       QualitativeAssessment, Thesis)

TABLE = PeerTable(status=peers.SHOWN, as_of="2026-11-02 14:00 UTC", ticker="ACME",
                  basis="Peers from Finnhub's sub-industry list.", peers=["AAA", "BBB", "CCC"],
                  rows=[PeerRow("P/E (TTM)", "15.0x", "20.0x", 3, "lowest of 4")],
                  relative="52-week price return: ACME +1.0%.", growth="Not shown: x.",
                  values={"ACME": {"peTTM": 15.0}})


def _assessment():
    return QualitativeAssessment(ticker="ACME", as_of="t", filing_accession="acc-1",
                                 filing_date="2026-02-19", model="m", moat=Moat(),
                                 thesis=Thesis(takeaway="read."), cache_key="acc-1")


def _bundle():
    tenk = FilingText("ACME", "acc-1", "2026-02-19", business="b")
    return FilingBundle(tenk=tenk, primary_accession="acc-1", cache_key="acc-1",
                        filing_date="2026-02-19")


def _enrich(tmp_path, monkeypatch, *, enabled):
    calls = []

    def fake_assess(card, bundle, config, **kw):
        calls.append("assess")
        return _assessment()

    def fake_section(card, config):
        calls.append("peers")
        return TABLE

    monkeypatch.setattr(peers, "fetch_section", fake_section)
    card = SimpleNamespace(ticker="ACME", composite=90.0, confidence=1.0, scored=True, gates=[],
                           passed=True, sic_bucket=None,
                           metrics=SimpleNamespace(name="Acme Inc.", sic="3674"))
    config = {"research": {"output_root": str(tmp_path), "peers": {"enabled": enabled}}}
    (result,) = research.enrich([card], config, top_n=1, fetch=lambda t, **k: _bundle(),
                                assess_fn=fake_assess)
    return result, calls


def test_the_section_is_off_unless_enabled_and_the_brief_is_then_unchanged(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=False)
    assert calls == ["assess"]
    brief = Path(result.brief_path).read_text()
    no_data = (capital_allocation.NOT_SHOWN_NO_DATA, capital_allocation.NOT_SHOWN)
    assert brief == report.to_markdown(_assessment(), capital_allocation=no_data)
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert record["peer_table"] is None


def test_the_section_is_fetched_after_the_model_call_and_recorded(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True)
    assert calls == ["assess", "peers"]
    brief = Path(result.brief_path).read_text()
    assert f"## {peers.HEADING}" in brief and "| P/E (TTM) | 15.0x | 20.0x (3) | lowest of 4 |" in brief
    assert brief.index("## Reconciliation") < brief.index(peers.HEADING) < brief.index("## Moat\n")
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert record["peer_table"]["status"] == peers.SHOWN
    assert record["peer_table"]["values"] == {"ACME": {"peTTM": 15.0}}
    assert peers.from_record(record["peer_table"]).rows == TABLE.rows


def test_the_model_never_sees_the_section():
    source = inspect.getsource(assess_mod)
    for name in ("peer_table", "peers", "stock/peers", "research.peers"):
        assert name not in source, name
    assert "peer" not in " ".join(inspect.signature(assess_mod._build_user_prompt).parameters)


def test_the_section_wording_is_in_the_cache_fingerprint():
    assert "peers" in cachekey._PROMPT_MODULES


def test_the_shipped_config_has_the_section_on():
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(str(root / "config.yaml"))["research"]["peers"]
    assert cfg["enabled"] is True and "min_market_cap" not in cfg
    assert (cfg["max_candidates"], cfg["max_peers"], cfg["min_peers"]) == (12, 6, 3)
    assert cfg["timeout"] <= peers.MAX_DEADLINE_S


def test_the_demo_path_never_fetches_peers(monkeypatch):
    from shortlist.screen import main

    def _no(*a, **k):
        raise AssertionError("the demo must not fetch peers")
    monkeypatch.setattr(peers, "collect", _no)
    assert main(["--demo", "--json"]) == 0
