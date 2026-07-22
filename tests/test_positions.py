import json
from datetime import date
from pathlib import Path
import pytest
from shortlist import positions as pos
from shortlist.portfolio import Holding


def _store():
    return {"version": 1, "positions": {}}


def test_add_bare_ticker_sets_added_and_null_shares(monkeypatch):
    monkeypatch.setattr(pos, "_today", lambda: date(2026, 7, 22))
    s = _store()
    pos.add_or_update(s, "nvda")            # lowercase in, upper stored
    assert s["positions"]["NVDA"] == {"added": "2026-07-22", "shares": None,
                                      "thesis": None, "entry_card": None}


def test_add_with_shares_and_entry_card():
    s = _store()
    card = {"composite": 71.2, "sources": ["yahoo", "finnhub", "edgar"], "as_of": "2026-07-22"}
    pos.add_or_update(s, "NVDA", shares=12.5, entry_card=card)
    p = s["positions"]["NVDA"]
    assert p["shares"] == 12.5 and p["entry_card"] == card


def test_update_preserves_added_thesis_and_entry_card():
    s = _store()
    pos.add_or_update(s, "NVDA", shares=None,
                      entry_card={"composite": 70, "sources": ["yahoo"], "as_of": "2026-01-01"})
    pos.set_thesis(s, "NVDA", "capex cycle")
    orig_added = s["positions"]["NVDA"]["added"]
    pos.add_or_update(s, "NVDA", shares=12,
                      entry_card={"composite": 99, "sources": ["yahoo"], "as_of": "2026-07-22"})
    p = s["positions"]["NVDA"]
    assert p["shares"] == 12               # updated
    assert p["added"] == orig_added        # preserved
    assert p["thesis"] == "capex cycle"    # preserved
    assert p["entry_card"]["composite"] == 70   # original entry_card preserved, NOT overwritten


def test_set_thesis_absent_returns_false():
    assert pos.set_thesis(_store(), "NVDA", "x") is False


def test_remove_returns_full_record_and_pops():
    s = _store()
    pos.add_or_update(s, "NVDA", shares=12)
    rec = pos.remove(s, "NVDA")
    assert rec["shares"] == 12 and "NVDA" not in s["positions"]
    assert pos.remove(s, "NVDA") is None


def test_holdings_view_carries_optional_shares():
    s = _store()
    pos.add_or_update(s, "NVDA", shares=12)
    pos.add_or_update(s, "MSFT")           # no shares
    hs = {h.ticker: h.shares for h in pos.holdings_view(s)}
    assert hs == {"NVDA": 12, "MSFT": None}
    assert all(isinstance(h, Holding) for h in pos.holdings_view(s))


def test_no_thesis_tickers():
    s = _store()
    pos.add_or_update(s, "NVDA")
    pos.set_thesis(s, "NVDA", "why")
    pos.add_or_update(s, "MSFT")
    assert pos.no_thesis_tickers(s) == ["MSFT"]


def test_load_missing_file_is_empty(tmp_path):
    assert pos.load_store(tmp_path / "nope.json") == {"version": 1, "positions": {}}


def test_load_corrupt_file_is_empty(tmp_path):
    p = tmp_path / "positions.json"
    p.write_text("{ not json")
    assert pos.load_store(p) == {"version": 1, "positions": {}}


def test_save_then_load_roundtrip_atomic(tmp_path):
    p = tmp_path / "positions.json"
    s = _store()
    pos.add_or_update(s, "NVDA", shares=12)
    pos.save_store(p, s)
    assert pos.load_store(p)["positions"]["NVDA"]["shares"] == 12
    assert not list(tmp_path.glob("*.tmp"))   # temp cleaned up


def test_unknown_keys_preserved_on_roundtrip(tmp_path):
    p = tmp_path / "positions.json"
    p.write_text(json.dumps({"version": 1, "positions": {},
                             "future_key": {"x": 1}}))
    s = pos.load_store(p)
    pos.save_store(p, s)
    assert json.loads(p.read_text())["future_key"] == {"x": 1}


def test_append_decision_writes_one_json_line(tmp_path):
    p = tmp_path / "decisions.jsonl"
    pos.append_decision(p, {"ts": "2026-07-22", "ticker": "NVDA", "action": "hold"})
    pos.append_decision(p, {"ts": "2026-07-23", "ticker": "MSFT", "action": "remove"})
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ticker"] == "NVDA"
