import json
from pathlib import Path
import pytest
from shortlist.scout.bot import TelegramBot, parse_command


class _Notifier:
    def __init__(self): self.msgs = []
    def send_message(self, m): self.msgs.append(m)
    def send_chat_action(self, *a, **k): pass


def _bot(tmp_path, screen_fn=None):
    cfg = {"scout": {}, "portfolio": {"store": str(tmp_path / "positions.json"),
                                      "decisions": str(tmp_path / "decisions.jsonl"),
                                      "max_holdings": 50}}
    b = TelegramBot(_Notifier(), cfg, screen_fn=screen_fn or (lambda *a, **k: []),
                    report_fn=lambda *a, **k: None, deliver_fn=lambda *a, **k: None)
    return b


def test_add_writes_position_and_confirms(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA 12"))
    store = json.loads((tmp_path / "positions.json").read_text())
    assert store["positions"]["NVDA"]["shares"] == 12
    assert any("NVDA" in m for m in b.notifier.msgs)


def test_add_bulk(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA, MSFT, LMT"))
    store = json.loads((tmp_path / "positions.json").read_text())
    assert set(store["positions"]) == {"NVDA", "MSFT", "LMT"}


def test_add_invalid_replies_usage_no_write(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA years of runway"))
    assert not (tmp_path / "positions.json").exists() or \
        json.loads((tmp_path / "positions.json").read_text())["positions"] == {}
    assert any("Usage" in m or "thesis" in m for m in b.notifier.msgs)


def test_thesis_on_unknown_ticker_replies_not_tracked(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/thesis NVDA some reason"))
    assert any("not tracked" in m.lower() for m in b.notifier.msgs)


def test_thesis_sets_on_existing(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA"))
    b._handle(parse_command("/thesis NVDA capex cycle"))
    store = json.loads((tmp_path / "positions.json").read_text())
    assert store["positions"]["NVDA"]["thesis"] == "capex cycle"


def test_remove_is_nondestructive_writes_ledger(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA 12"))
    b._handle(parse_command("/thesis NVDA capex cycle"))
    b._handle(parse_command("/remove NVDA thesis broke"))
    store = json.loads((tmp_path / "positions.json").read_text())
    assert "NVDA" not in store["positions"]
    ledger = (tmp_path / "decisions.jsonl").read_text().splitlines()
    rec = json.loads(ledger[-1])
    assert rec["action"] == "remove" and rec["ticker"] == "NVDA"
    assert rec["position"]["thesis"] == "capex cycle"   # full record embedded (recoverable)


def test_hold_writes_ledger(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/add NVDA"))
    b._handle(parse_command("/hold NVDA looks fine"))
    rec = json.loads((tmp_path / "decisions.jsonl").read_text().splitlines()[-1])
    assert rec["action"] == "hold" and rec["note"] == "looks fine"


def test_portfolio_empty_state_mentions_add_not_csv(tmp_path):
    b = _bot(tmp_path)
    b._handle(parse_command("/portfolio"))
    joined = " ".join(b.notifier.msgs)
    assert "/add" in joined and "portfolio.csv" not in joined
