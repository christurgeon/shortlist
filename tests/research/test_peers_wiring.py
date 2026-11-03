"""How the "Peer comparison" section reaches the brief: after the model call by default, or
before it as a prompt line with `in_prompt`; recorded in the brief JSON and hashed with the
brief's wording."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from shortlist import research
from shortlist.config import load_config
from shortlist.research import assess as assess_mod
from shortlist.research import cachekey, capital_allocation, peers, report
from shortlist.research.claude_cli import CliResult
from shortlist.research.models import (FilingBundle, FilingText, Moat, PeerRow, PeerTable,
                                       QualitativeAssessment, Thesis)

TABLE = PeerTable(status=peers.SHOWN, as_of="2026-11-02 14:00 UTC", ticker="ACME",
                  basis="Peer source: Finnhub sub-industry.", peers=["AAA", "BBB", "CCC"],
                  rows=[PeerRow("P/E (TTM)", "15.0x", "20.0x", 3, "lowest of 4")],
                  relative="52-week price return: ACME +1.0%.", growth="Not shown: x.",
                  values={"ACME": {"peTTM": 15.0}})

CONFIG = {"research": {"model": "m", "timeout_s": 30}}
GOOD = {"business_model_summary": "x", "moat": {"summary": "m"}, "risks": [], "red_flags": [],
        "management_capital_allocation": "y", "reconciliation": [],
        "thesis": {"takeaway": "t"}}


def _assessment():
    return QualitativeAssessment(ticker="ACME", as_of="t", filing_accession="acc-1",
                                 filing_date="2026-02-19", model="m", moat=Moat(),
                                 thesis=Thesis(takeaway="read."), cache_key="acc-1")


def _bundle():
    tenk = FilingText("ACME", "acc-1", "2026-02-19", business="b")
    return FilingBundle(tenk=tenk, primary_accession="acc-1", cache_key="acc-1",
                        filing_date="2026-02-19")


def _enrich(tmp_path, monkeypatch, *, enabled, in_prompt=False, table=TABLE, seen=None):
    calls = []

    def fake_assess(card, bundle, config, **kw):
        calls.append("assess")
        if seen is not None:
            seen.update(kw)
        return _assessment()

    def fake_section(card, config):
        calls.append("peers")
        return table

    monkeypatch.setattr(peers, "fetch_section", fake_section)
    card = SimpleNamespace(ticker="ACME", composite=90.0, confidence=1.0, scored=True, gates=[],
                           passed=True, sic_bucket=None,
                           metrics=SimpleNamespace(name="Acme Inc.", sic="3674"))
    config = {"research": {"output_root": str(tmp_path),
                           "peers": {"enabled": enabled, "in_prompt": in_prompt}}}
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


def test_by_default_the_model_never_sees_the_section(tmp_path, monkeypatch):
    seen = {}
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True, seen=seen)
    assert calls == ["assess", "peers"] and "peer_table" not in seen
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert record["peer_table"]["in_prompt"] is False


def test_in_prompt_fetches_once_before_the_model_call_and_says_so(tmp_path, monkeypatch):
    seen = {}
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True, in_prompt=True, seen=seen)
    assert calls == ["peers", "assess"]
    assert seen["peer_table"].rows == TABLE.rows and seen["peer_table"].in_prompt is True
    assert TABLE.in_prompt is False
    brief = Path(result.brief_path).read_text()
    assert f"## {peers.HEADING_IN_PROMPT}" in brief and peers.HEADING not in brief
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert peers.from_record(record["peer_table"]).in_prompt is True


def test_in_prompt_with_no_table_shown_does_not_claim_the_model_saw_it(tmp_path, monkeypatch):
    seen = {}
    down = PeerTable(peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT, "2026-11-02 14:00 UTC", "ACME")
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True, in_prompt=True, table=down,
                            seen=seen)
    assert calls == ["peers", "assess"] and seen["peer_table"].in_prompt is False
    assert f"## {peers.HEADING}" in Path(result.brief_path).read_text()


def test_the_prompt_line_and_its_destination_clause_appear_only_with_a_shown_table():
    prompts, systems = [], []

    def runner(prompt, system, model, timeout_s, **kw):
        prompts.append(prompt)
        systems.append(system)
        return CliResult(text=json.dumps(GOOD), model=model)

    down = PeerTable(peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT, "t", "ACME")
    for table in (None, down, TABLE):
        assess_mod.assess(None, _bundle(), CONFIG, runner=runner, peer_table=table)
    assert prompts[0] == prompts[1] and systems[0] == systems[1]
    assert prompts[2] == prompts[0] + "\n\n" + peers.context_line(TABLE)
    assert systems[2] == systems[0] + assess_mod.PEERS_SYSTEM_ADDENDUM
    line = peers.context_line(TABLE)
    assert "NOT filing text" in line and "P/E (TTM) 15.0x vs peer median 20.0x (n=3, lowest of 4)" in line
    assert "'peers'" in assess_mod.PEERS_SYSTEM_ADDENDUM


def test_the_peers_entry_is_kept_on_top_of_the_reconciliation_ceiling():
    rec = [{"signal": s, "tension": "t", "filing_says": "", "verdict": "silent"}
           for s in ("value", "growth", "moat", "peers")]

    def runner(prompt, system, model, timeout_s, **kw):
        return CliResult(text=json.dumps({**GOOD, "reconciliation": rec}), model=model)

    cfg = {"research": {**CONFIG["research"], "max_conflicts": 3}}
    without = assess_mod.assess(None, _bundle(), cfg, runner=runner)
    with_line = assess_mod.assess(None, _bundle(), cfg, runner=runner, peer_table=TABLE)
    assert [c.signal for c in without.reconciliation] == ["value", "growth", "moat"]
    assert [c.signal for c in with_line.reconciliation] == ["value", "growth", "moat", "peers"]


def test_a_vendor_figure_from_the_prompt_line_never_verifies_as_a_quote():
    quote = "P/E (TTM) 15.0x vs peer median 20.0x (n=3, lowest of 4)"
    payload = {**GOOD, "risks": [{"claim": "Dear vs peers", "evidence": quote}]}

    def runner(prompt, system, model, timeout_s, **kw):
        assert quote in prompt
        return CliResult(text=json.dumps(payload), model=model)

    a = assess_mod.assess(None, _bundle(), CONFIG, runner=runner, peer_table=TABLE)
    assert [r.verified for r in a.risks] == [False]


def test_the_section_wording_is_in_the_cache_fingerprint():
    assert "peers" in cachekey._PROMPT_MODULES


def test_the_shipped_config_has_the_section_on():
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(str(root / "config.yaml"))["research"]["peers"]
    assert cfg["enabled"] is True and "min_market_cap" not in cfg
    assert cfg["in_prompt"] is True
    assert (cfg["max_candidates"], cfg["max_peers"], cfg["min_peers"]) == (12, 6, 3)
    assert cfg["size_mismatch_ratio"] == 0.1
    assert cfg["timeout"] <= peers.MAX_DEADLINE_S
    # Three /deep briefs that start together must all get their calls inside the deadline.
    assert 3 * cfg["max_calls"] <= cfg["bucket_capacity"] + cfg["refill_per_s"] * cfg["timeout"]
    assert cfg["refill_per_s"] * 60 < 60
