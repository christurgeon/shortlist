# Position Monitor v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user register/remove/view a stock portfolio over Telegram and get a once-daily digest alert when a clean-negative 8-K (item 1.03/2.04/4.02) is filed against a name they own.

**Architecture:** A new bot-owned `positions.json` store (pure leaf `positions.py`) drives four new Telegram commands and rewires `/portfolio`. The daily scout run gains a failure-isolated monitor step that intersects the holdings with the negative-8-K `veto_map` it *already* computes (zero extra fetches), renders the result as a new digest section, and dedups via a `ScoutState` ledger. No new schedule, no new service.

**Tech Stack:** Python 3.12, stdlib-only leaves (`json`, `os`, `dataclasses`), pytest. No new dependencies.

## Global Constraints

- **Spec:** `docs/POSITION_MONITOR.md` is the authority; every task maps to a spec section.
- **Two-store split ownership.** `positions.json` is written **only** by the bot. `ScoutState` is written **only** by the daily run. The daily monitor **reads** `positions.json` but must **never write it** (§3.1).
- **No stance, ever.** Alerts route to the SEC filing; they never say sell, score an exit, or render a verdict (§1, §11).
- **No price-derived alert, ever** (§2). No drawdown, trailing stop, 52-week-low, etc. may push a message.
- **Item subset is {1.03, 2.04, 4.02} only** — NOT the full veto set. 5.01/2.05/2.06/3.01 are matched by the sweep but filtered out (§5.1).
- **Atomic writes:** temp-in-same-dir + `os.replace` (the `state.py:_save` pattern), for both `positions.json` and `decisions.jsonl` semantics.
- **Byte-identical when disabled:** absent `portfolio.monitor` block → the daily discovery run is byte-identical (§9).
- **Plain-English alert copy leads; item code trails** (§5.3).
- **New user-facing terms require `scout/glossary.py` entries** (AST-scan enforced, §9).
- **Free-chain screening:** `/add` and the monitor screen on `digest_sources(base, include_fmp=False)` — never the bot's fmp-bearing `self.sources` (§4, §7).
- DRY, YAGNI, TDD, frequent commits. Run the full suite (`uv run pytest`) before each commit where practical; targeted tests during a task.

---

### Task 1: `positions.py` — the bot-owned store leaf

**Files:**
- Create: `src/shortlist/positions.py`
- Test: `tests/test_positions.py`

**Interfaces:**
- Consumes: `shortlist.portfolio.Holding` (widened in Task 2, but importable now).
- Produces:
  - `load_store(path) -> dict` — the `{"version":1,"positions":{...}}` dict; missing/corrupt → `{"version":1,"positions":{}}` + never raises.
  - `save_store(path, store: dict) -> None` — atomic.
  - `add_or_update(store, ticker, *, shares=None, entry_card=None) -> None` — creates (with `added`=today) or fills/updates shares+entry_card, preserving `added`/`thesis`/original `entry_card` on update.
  - `set_thesis(store, ticker, thesis) -> bool` — False if ticker absent.
  - `remove(store, ticker) -> dict | None` — pops and returns the full record (for the ledger), or None if absent.
  - `holdings_view(store) -> list[Holding]` — one `Holding(ticker, shares)` per position; `shares` may be `None`.
  - `no_thesis_tickers(store) -> list[str]`.
  - `append_decision(path, record: dict) -> None` — one JSON line appended to `decisions.jsonl`.
  - `KNOWN_ACTIONS = frozenset({"hold", "remove"})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_positions.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_positions.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.positions'`

- [ ] **Step 3: Implement `src/shortlist/positions.py`**

```python
"""Bot-owned position store (positions.json) — the register/remove/view foundation.

Pure + dependency-light (stdlib + Holding), the portfolio.py / _form4.py leaf pattern.
The ONLY writer of positions.json is the bot; the daily run reads it but never writes it
(see docs/POSITION_MONITOR.md §3.1). Atomic writes mirror ScoutState._save.
"""
from __future__ import annotations

import json
import os
from datetime import date, timezone, datetime
from pathlib import Path
from typing import Optional

from .portfolio import Holding

KNOWN_ACTIONS = frozenset({"hold", "remove"})


def _today() -> date:                       # seam for tests
    return datetime.now(timezone.utc).date()


def _empty() -> dict:
    return {"version": 1, "positions": {}}


def load_store(path) -> dict:
    """Parse positions.json leniently. Missing/unreadable/corrupt -> empty, never raises."""
    p = Path(path)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
        return _empty()
    data.setdefault("version", 1)
    return data


def save_store(path, store: dict) -> None:
    """Atomic write (PID-unique sibling temp + os.replace), the ScoutState._save pattern."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def add_or_update(store: dict, ticker: str, *, shares: Optional[float] = None,
                  entry_card: Optional[dict] = None) -> None:
    """Create a position (added=today) or fill/update shares+entry_card on an existing one,
    preserving `added`, `thesis`, and the ORIGINAL `entry_card` (never overwritten)."""
    t = ticker.strip().upper()
    positions = store.setdefault("positions", {})
    if t not in positions:
        positions[t] = {"added": _today().isoformat(), "shares": shares,
                        "thesis": None, "entry_card": entry_card}
        return
    rec = positions[t]
    if shares is not None:
        rec["shares"] = shares
    if entry_card is not None and not rec.get("entry_card"):
        rec["entry_card"] = entry_card      # only fill if empty — never clobber the baseline


def set_thesis(store: dict, ticker: str, thesis: str) -> bool:
    t = ticker.strip().upper()
    rec = store.get("positions", {}).get(t)
    if rec is None:
        return False
    rec["thesis"] = thesis
    return True


def remove(store: dict, ticker: str) -> Optional[dict]:
    """Pop and return the full record (for the decision ledger), or None if absent."""
    t = ticker.strip().upper()
    return store.get("positions", {}).pop(t, None)


def holdings_view(store: dict) -> list[Holding]:
    return [Holding(t, rec.get("shares")) for t, rec in store.get("positions", {}).items()]


def no_thesis_tickers(store: dict) -> list[str]:
    return [t for t, rec in store.get("positions", {}).items() if not rec.get("thesis")]


def append_decision(path, record: dict) -> None:
    """Append one JSON line to decisions.jsonl (append-only; parent dir created)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_positions.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/positions.py tests/test_positions.py
git commit -m "feat(positions): bot-owned positions.json store leaf"
```

---

### Task 2: `portfolio.py` — fix the `shares=None` crash

**Files:**
- Modify: `src/shortlist/portfolio.py:21` (Holding.shares type), `src/shortlist/portfolio.py:126` (summarize guard)
- Test: `tests/test_portfolio.py` (add cases)

**Interfaces:**
- Produces: `Holding.shares: Optional[float]`; `summarize()` tolerant of `shares=None` (holding → `unpriced`, no exposure).

**Context:** `summarize()` at `portfolio.py:126` is `value = h.shares * price if price else None` — it guards on `price`, not `shares`. `Holding.shares` is typed `float`. The CSV loader never produced null shares, so the Task-1 store is the first producer; `None * price` raises `TypeError` and crashes the whole `/portfolio` render.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio.py — add
from shortlist.portfolio import Holding, summarize
from shortlist.providers import MockProvider   # existing offline StockMetrics factory
# NOTE: match how test_portfolio.py already builds ScoreCards; if it uses a local
# _card(ticker, price=...) helper, reuse that instead of MockProvider.

def test_summarize_tolerates_none_shares(_card):   # _card: existing fixture/helper in this file
    # A holding with shares=None and a valid-price card must NOT crash; it is unpriced.
    cards = [_card("NVDA", price=100.0), _card("MSFT", price=50.0)]
    holdings = [Holding("NVDA", 10), Holding("MSFT", None)]
    summary = summarize(holdings, cards)
    assert "MSFT" in summary.unpriced          # excluded from exposure
    assert summary.total_value == 1000.0       # only NVDA counted
    msft = next(p for p in summary.positions if p.ticker == "MSFT")
    assert msft.value is None and msft.weight is None
```

Note to implementer: open `tests/test_portfolio.py` first and reuse its existing ScoreCard-building helper (there is one — the file already tests `summarize`). Do not introduce a new card factory if one exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_portfolio.py -k none_shares -q`
Expected: FAIL with `TypeError: unsupported operand type(s) for *: 'NoneType' and 'float'`

- [ ] **Step 3: Apply the two-line fix**

In `src/shortlist/portfolio.py`, change the `Holding` dataclass field:

```python
@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: Optional[float]
```

And the value computation in `summarize()` (the line at ~126):

```python
        value = h.shares * price if (price and h.shares is not None) else None
```

(`Optional` is already imported in this file.)

- [ ] **Step 4: Run tests to verify pass (and no regressions)**

Run: `uv run pytest tests/test_portfolio.py -q`
Expected: PASS (all existing + the new case)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/portfolio.py tests/test_portfolio.py
git commit -m "fix(portfolio): summarize tolerates shares=None (optional-shares holdings)"
```

---

### Task 3: Command grammar — pure parsers for `/add`, `/thesis`, `/hold`, `/remove`

**Files:**
- Modify: `src/shortlist/scout/bot.py` (add parsers + extend `_KNOWN`)
- Test: `tests/scout/test_bot_position_parse.py` (create)

**Interfaces:**
- Consumes: `shortlist.validation.valid_format`.
- Produces (module-level functions in `bot.py`):
  - `parse_add(raw: str) -> tuple[list[str], float | None, str | None]` — `(tickers, shares, error)`. A comma anywhere ⇒ bulk (bare tickers, `shares=None`). Else ticker + optional numeric shares. `error` is a usage/validation string when parsing fails (tickers empty then).
  - `parse_thesis(raw: str) -> tuple[str | None, str | None, str | None]` — `(ticker, thesis_text, error)`. Ticker upper-cased+validated; thesis is the remaining prose, **case preserved**.
  - `parse_ticker_note(raw: str) -> tuple[str | None, str | None, str | None]` — `(ticker, note, error)` for `/hold` and `/remove`.
  - `_KNOWN` extended with `add`, `thesis`, `hold`, `remove`, `sold`.

**Context:** `parse_command` (`bot.py:47`) uppercases ALL tokens via `_tickers` — unusable for a shares number or case-bearing thesis. These parsers take the raw command text (like `explain_term`, `bot.py:57`) and strip the leading `/cmd` word themselves.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_bot_position_parse.py
from shortlist.scout.bot import parse_add, parse_thesis, parse_ticker_note


# --- /add ---
def test_add_bare_ticker():
    assert parse_add("/add NVDA") == (["NVDA"], None, None)

def test_add_ticker_and_int_shares():
    assert parse_add("/add NVDA 12") == (["NVDA"], 12.0, None)

def test_add_fractional_shares():
    assert parse_add("/add NVDA 12.5") == (["NVDA"], 12.5, None)

def test_add_lowercase_ticker_uppercased():
    assert parse_add("/add nvda 5") == (["NVDA"], 5.0, None)

def test_add_dotted_ticker():
    assert parse_add("/add BRK.B") == (["BRK.B"], None, None)

def test_add_bulk_comma():
    assert parse_add("/add NVDA, MSFT, LMT") == (["NVDA", "MSFT", "LMT"], None, None)

def test_add_bulk_dedups_and_uppercases():
    assert parse_add("/add nvda, NVDA, msft") == (["NVDA", "MSFT"], None, None)

def test_add_non_numeric_second_token_is_error():
    # "2 years of runway" -> the old ambiguity; now rejected, not silently eaten
    tickers, shares, err = parse_add("/add NVDA years of runway")
    assert tickers == [] and shares is None and err is not None

def test_add_invalid_ticker_is_error():
    tickers, _, err = parse_add("/add 123$$")
    assert tickers == [] and err is not None

def test_add_empty_is_error():
    tickers, _, err = parse_add("/add")
    assert tickers == [] and err is not None


# --- /thesis (prose, case preserved) ---
def test_thesis_preserves_case_and_ticker_upper():
    assert parse_thesis("/thesis nvda Azure Capex Cycle") == ("NVDA", "Azure Capex Cycle", None)

def test_thesis_missing_text_is_error():
    tk, txt, err = parse_thesis("/thesis NVDA")
    assert tk == "NVDA" and txt is None and err is not None

def test_thesis_missing_ticker_is_error():
    tk, txt, err = parse_thesis("/thesis")
    assert tk is None and err is not None


# --- /hold, /remove (ticker + optional note) ---
def test_ticker_note_bare():
    assert parse_ticker_note("/hold NVDA") == ("NVDA", None, None)

def test_ticker_note_with_note_preserves_case():
    assert parse_ticker_note("/remove NVDA thesis broke") == ("NVDA", "thesis broke", None)

def test_ticker_note_missing_ticker_is_error():
    tk, _, err = parse_ticker_note("/hold")
    assert tk is None and err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_bot_position_parse.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_add'`

- [ ] **Step 3: Implement the parsers in `bot.py`**

Extend `_KNOWN` (at `bot.py:26`):

```python
_KNOWN = {"screen", "deep", "portfolio", "help", "start", "explain",
          "add", "thesis", "hold", "remove", "sold"}
```

Add these functions near `explain_term` (after `bot.py:61`):

```python
from ..validation import valid_format   # add to the existing validation import line


def _strip_cmd(raw: str) -> str:
    """Everything after the leading /command token (mirrors explain_term's split)."""
    parts = raw.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def parse_add(raw: str) -> tuple[list[str], float | None, str | None]:
    """(tickers, shares, error). Comma anywhere => bulk bare tickers. Else ticker + optional
    NUMERIC shares. A non-numeric second token is rejected (it is almost certainly a thesis
    typed in the wrong command)."""
    args = _strip_cmd(raw)
    if not args:
        return [], None, "Usage: /add NVDA [shares]  or  /add NVDA, MSFT, LMT"
    if "," in args:
        seen: list[str] = []
        for tok in args.split(","):
            t = tok.strip().upper()
            if not t:
                continue
            if not valid_format(t):
                return [], None, f"Invalid ticker: {t}. Use US symbols like NVDA, BRK.B."
            if t not in seen:
                seen.append(t)
        if not seen:
            return [], None, "Usage: /add NVDA, MSFT, LMT"
        return seen, None, None
    toks = args.split()
    ticker = toks[0].upper()
    if not valid_format(ticker):
        return [], None, f"Invalid ticker: {ticker}. Use US symbols like NVDA, BRK.B."
    if len(toks) == 1:
        return [ticker], None, None
    try:
        shares = float(toks[1])
    except ValueError:
        return [], None, ("Usage: /add NVDA [shares]. Set a thesis separately with "
                          "/thesis NVDA <why you own it>.")
    return [ticker], shares, None


def parse_thesis(raw: str) -> tuple[str | None, str | None, str | None]:
    """(ticker, thesis_text, error). Ticker upper-cased; thesis prose keeps its case."""
    args = _strip_cmd(raw)
    parts = args.split(maxsplit=1)
    if not parts:
        return None, None, "Usage: /thesis NVDA <why you own it>"
    ticker = parts[0].upper()
    if not valid_format(ticker):
        return None, None, f"Invalid ticker: {ticker}."
    if len(parts) == 1:
        return ticker, None, "Usage: /thesis NVDA <why you own it>"
    return ticker, parts[1].strip(), None


def parse_ticker_note(raw: str) -> tuple[str | None, str | None, str | None]:
    """(ticker, note, error) for /hold and /remove. Note prose keeps its case."""
    args = _strip_cmd(raw)
    parts = args.split(maxsplit=1)
    if not parts:
        return None, None, "Usage: TICKER [reason]"
    ticker = parts[0].upper()
    if not valid_format(ticker):
        return None, None, f"Invalid ticker: {ticker}."
    note = parts[1].strip() if len(parts) > 1 else None
    return ticker, note, None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_bot_position_parse.py -q`
Expected: PASS (16 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/bot.py tests/scout/test_bot_position_parse.py
git commit -m "feat(bot): unambiguous /add /thesis /hold /remove command grammar"
```

---

### Task 4: Bot handlers + `_HELP` + empty-state `/portfolio` rewire (the CRUD deliverable)

**Files:**
- Modify: `src/shortlist/scout/bot.py` (`__init__` config, `_handle` routing, new handlers, `_HELP`, `_do_portfolio`)
- Test: `tests/scout/test_bot_position_handlers.py` (create)

**Interfaces:**
- Consumes: Task 1 (`positions` module), Task 3 (parsers), existing `self._screen_fn()`, `self._report_fn()`, `self._deliver_fn()`, `self._partition_present`.
- Produces: working `/add`, `/thesis`, `/hold`, `/remove`, and `/portfolio` reading `positions.json`.

**Context:** `_do_portfolio` (`bot.py:282`) currently calls `pf.load_holdings(path)` and prints a `portfolio.csv` empty-state message (`bot.py:290`). `_handle` (`bot.py:196`) routes by `cmd.name`. Store/decision paths come from config `portfolio.store` / `portfolio.decisions`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_bot_position_handlers.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_bot_position_handlers.py -q`
Expected: FAIL (handlers route to "unknown"; no positions.json written)

- [ ] **Step 3: Implement the handlers**

In `__init__` (after `bot.py:137`, near the other cfg reads), add store/decisions paths:

```python
        pf_cfg = config.get("portfolio", {})
        self.store_path = pf_cfg.get("store", "positions.json")
        self.decisions_path = pf_cfg.get("decisions", "decisions.jsonl")
```

Replace `_HELP` (`bot.py:84`) — drop the `portfolio.csv` phrasing, add the new commands:

```python
_HELP = (
    "Shortlist scout bot. Commands:\n"
    "/add NVDA 12 — track a holding (shares optional; paste several: /add NVDA, MSFT)\n"
    "/thesis NVDA <why you own it> — record your thesis for a holding\n"
    "/portfolio — view your holdings: exposure, sectors, per-name scores\n"
    "/hold NVDA <note> — after an alert, log that you looked and held\n"
    "/remove NVDA <reason> — stop tracking (recoverable)\n"
    "/screen NVDA, LMT — score tickers (seconds), reply with the dashboard\n"
    "/deep TSLA — score + Claude 10-K research brief (slower)\n"
    "/explain 13d — what a term in these reports means\n"
    "/help — this message\n"
    "(type your note/reason right after the command)"
)
```

Extend `_handle` (`bot.py:196`) with the new routes:

```python
        elif cmd.name == "add":
            self._do_add(cmd.raw)
        elif cmd.name == "thesis":
            self._do_thesis(cmd.raw)
        elif cmd.name == "hold":
            self._do_decision(cmd.raw, "hold")
        elif cmd.name in ("remove", "sold"):
            self._do_remove(cmd.raw)
```

Add the handlers (near `_do_portfolio`). Note the free-chain resolution and the lazy `positions`/`digest_sources` imports (keep the always-on import path light):

```python
    def _free_sources(self):
        from .daily import digest_sources
        base = self.scout_cfg.get("deep_screen_sources",
                                  ["yahoo", "fmp", "finnhub", "edgar"])
        return digest_sources(base, include_fmp=False)

    def _do_add(self, raw: str) -> None:
        from .. import positions as pos
        tickers, shares, err = parse_add(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        macro = self._fetch_macro(self.config)
        entry_by_ticker = {}
        # Screen (free chain) to capture entry_card + reply with the card.
        cards = self._screen_fn()(tickers, self._free_sources(), self.config, macro=macro)
        present, _missing = self._partition_present(cards)
        session = datetime.now(timezone.utc).date().isoformat()
        for c in present:
            entry_by_ticker[c.ticker] = {
                "composite": getattr(c, "composite", None),
                "sources": list(self._free_sources()),
                "as_of": session}
        for t in tickers:
            pos.add_or_update(store, t, shares=shares,
                              entry_card=entry_by_ticker.get(t))
        pos.save_store(self.store_path, store)
        n = len(store["positions"])
        nudge = ""
        if len(tickers) == 1 and not store["positions"][tickers[0]].get("thesis"):
            nudge = f"  ⚠ no thesis — /thesis {tickers[0]} <why you own it>"
        self.notifier.send_message(
            f"Tracking {', '.join(tickers)} — {n} holding(s). /portfolio to view.{nudge}")

    def _do_thesis(self, raw: str) -> None:
        from .. import positions as pos
        ticker, text, err = parse_thesis(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        if not pos.set_thesis(store, ticker, text):
            self.notifier.send_message(f"{ticker} not tracked — /add {ticker} first.")
            return
        pos.save_store(self.store_path, store)
        self.notifier.send_message(f"Thesis saved for {ticker}.")

    def _do_decision(self, raw: str, action: str) -> None:
        from .. import positions as pos
        ticker, note, err = parse_ticker_note(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        if ticker not in store.get("positions", {}):
            self.notifier.send_message(f"{ticker} not tracked — /add {ticker} first.")
            return
        pos.append_decision(self.decisions_path,
                            {"ts": datetime.now(timezone.utc).date().isoformat(),
                             "ticker": ticker, "action": action, "note": note})
        self.notifier.send_message(f"Logged: held {ticker}." if action == "hold"
                                   else f"Logged {ticker}.")

    def _do_remove(self, raw: str) -> None:
        from .. import positions as pos
        ticker, note, err = parse_ticker_note(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        rec = pos.remove(store, ticker)
        if rec is None:
            self.notifier.send_message(f"{ticker} not tracked.")
            return
        pos.append_decision(self.decisions_path,
                            {"ts": datetime.now(timezone.utc).date().isoformat(),
                             "ticker": ticker, "action": "remove", "note": note,
                             "position": rec})       # full record embedded => recoverable
        pos.save_store(self.store_path, store)
        self.notifier.send_message(f"Removed {ticker} (recoverable from the log).")
```

Rewire `_do_portfolio` (`bot.py:282`): replace the `load_holdings` + CSV empty-state block. Change the top of the method:

```python
    def _do_portfolio(self) -> None:
        from .. import positions as pos
        store = pos.load_store(self.store_path)
        holdings = pos.holdings_view(store)
        if not holdings:
            self.notifier.send_message(
                "No holdings yet. Add one with /add NVDA (shares optional), "
                "or paste several: /add NVDA, MSFT, LMT.")
            return
        cap = int((self.config.get("portfolio") or {}).get("max_holdings", 50))
        screened_holdings, dropped = holdings[:cap], [h.ticker for h in holdings[cap:]]
        tickers = [h.ticker for h in screened_holdings]
        # ... (keep the rest of the existing method unchanged: send_chat_action,
        #      macro, screen, summarize, report, deliver, dropped-warning)
```

Reuse the existing `pf.summarize(...)` / report / deliver tail (it already imports `from .. import portfolio as pf` — keep that import for `summarize`). Remove the old `warnings` plumbing from `load_holdings` (the new store has no parse warnings).

Ensure `_fetch_macro` is available (it is a static method at `bot.py:181`; call as `self._fetch_macro(self.config)`).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_bot_position_handlers.py tests/scout/test_bot_position_parse.py -q`
Expected: PASS

- [ ] **Step 5: Run the bot's existing test suite for regressions**

Run: `uv run pytest tests/scout/ -q`
Expected: PASS (no regressions in existing bot/report/portfolio-section tests)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/bot.py tests/scout/test_bot_position_handlers.py
git commit -m "feat(bot): /add /thesis /hold /remove handlers; /portfolio reads positions.json"
```

---

### Task 5: `ScoutState.position_alerts_seen` — the dedup ledger

**Files:**
- Modify: `src/shortlist/scout/state.py` (add accessors)
- Test: `tests/scout/test_state.py` (add cases)

**Interfaces:**
- Produces:
  - `ScoutState.position_alerts_seen() -> list[str]`
  - `ScoutState.add_position_alerts(keys: list[str], cap: int = 500) -> None`

**Context:** Copy the `eightk_seen_accessions` / `add_eightk_accessions` pair (`state.py:130-140`) verbatim in shape. `_append_capped` (`state.py:118`) dedups, preserves order, evicts oldest past cap, one save. Cap 500 is far above the 30-day held-book negative-8-K inflow, so eviction can never re-arm a live alert (§5.1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_state.py — add
def test_position_alerts_seen_roundtrip(tmp_path):
    from shortlist.scout.state import ScoutState
    s = ScoutState(tmp_path / "state.json")
    assert s.position_alerts_seen() == []
    s.add_position_alerts(["8k:0001-1", "8k:0001-2"])
    assert s.position_alerts_seen() == ["8k:0001-1", "8k:0001-2"]
    s.add_position_alerts(["8k:0001-2", "8k:0001-3"])   # dedup
    assert s.position_alerts_seen() == ["8k:0001-1", "8k:0001-2", "8k:0001-3"]

def test_position_alerts_absent_key_back_compat(tmp_path):
    from shortlist.scout.state import ScoutState
    (tmp_path / "state.json").write_text('{"held": []}')   # old file, no key
    s = ScoutState(tmp_path / "state.json")
    assert s.position_alerts_seen() == []

def test_position_alerts_cap_evicts_oldest(tmp_path):
    from shortlist.scout.state import ScoutState
    s = ScoutState(tmp_path / "state.json")
    s.add_position_alerts([f"8k:{i}" for i in range(5)], cap=3)
    assert s.position_alerts_seen() == ["8k:2", "8k:3", "8k:4"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_state.py -k position_alerts -q`
Expected: FAIL — `AttributeError: 'ScoutState' object has no attribute 'position_alerts_seen'`

- [ ] **Step 3: Implement the accessors**

Add after `add_buyback_accessions` (near `state.py:153`):

```python
    # --- position monitor: capped rolling alert-dedup ledger (8k:<accession> keys) ---
    def position_alerts_seen(self) -> list[str]:
        """Alert keys (8k:<accession>) already surfaced for held names. Absent key (old
        state files) reads as [] — back-compatible, no migration."""
        return list(self._data.get("position_alerts_seen", []))

    def add_position_alerts(self, keys: list[str], cap: int = 500) -> None:
        """Append newly-surfaced alert keys. Cap 500 ≫ the 30-day held-book negative-8-K
        inflow (the veto map is 30-day-pruned), so eviction can never re-arm a live alert."""
        self._append_capped("position_alerts_seen", keys, cap)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_state.py -k position_alerts -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/state.py tests/scout/test_state.py
git commit -m "feat(state): position_alerts_seen dedup ledger"
```

---

### Task 6: Monitor payload — pure emitter + `KNOWN_BREACH_KINDS` + glossary

**Files:**
- Create: `src/shortlist/scout/monitor.py`
- Modify: `src/shortlist/scout/glossary.py` (3 item entries)
- Test: `tests/scout/test_monitor.py` (create), `tests/scout/test_monitor_names.py` (create, AST scan)

**Interfaces:**
- Consumes: a `positions` dict (Task 1 schema), a `veto_map` (`{ticker: {last_date, items, adsh}}`), the item subset, and a seen-keys set.
- Produces:
  - `KNOWN_BREACH_KINDS = frozenset({"8k_negative"})`
  - `DEFAULT_ITEMS = ("1.03", "2.04", "4.02")`
  - `ITEM_MEANINGS: dict[str, str]` — plain-English gloss per item.
  - `compute_alerts(positions, veto_map, items, seen) -> list[dict]` — each alert dict: `{ticker, kind, key, adsh, items, date, meaning, thesis}`. `kind` ∈ `KNOWN_BREACH_KINDS`.
  - `heartbeat(positions, session_iso) -> dict` — `{"count": N, "as_of": session_iso}`.

**Context:** The `veto_map` record shape is `{last_date, items, adsh}` (verified: `state.update_eightk_negative`). Match `rec["items"]` against the subset; skip if `8k:<adsh>` already in `seen`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_monitor.py
from shortlist.scout import monitor as mon


def _veto(items, adsh="0001-23-01", date="2026-07-19"):
    return {"items": list(items), "adsh": adsh, "last_date": date}


def _positions(*tickers):
    return {"version": 1, "positions": {
        t: {"added": "2026-01-01", "shares": 10, "thesis": None, "entry_card": None}
        for t in tickers}}


def test_alert_fires_for_held_ticker_with_subset_item():
    pos = _positions("NVDA")
    vm = {"NVDA": _veto(["4.02"])}
    alerts = mon.compute_alerts(pos["positions"], vm, mon.DEFAULT_ITEMS, set())
    assert len(alerts) == 1
    a = alerts[0]
    assert a["ticker"] == "NVDA" and a["kind"] == "8k_negative"
    assert a["key"] == "8k:0001-23-01"
    assert "relied on" in a["meaning"]           # plain-English gloss for 4.02

def test_no_alert_for_unheld_ticker():
    vm = {"MSFT": _veto(["4.02"])}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, set()) == []

def test_non_subset_item_is_filtered():
    # 5.01 (change of control) is in the veto set but NOT the monitor subset
    vm = {"NVDA": _veto(["5.01"])}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, set()) == []

def test_seen_key_is_deduped():
    vm = {"NVDA": _veto(["1.03"], adsh="AAA")}
    seen = {"8k:AAA"}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, seen) == []

def test_thesis_carried_into_alert():
    pos = _positions("NVDA")
    pos["positions"]["NVDA"]["thesis"] = "capex cycle"
    vm = {"NVDA": _veto(["2.04"])}
    assert mon.compute_alerts(pos["positions"], vm, mon.DEFAULT_ITEMS, set())[0]["thesis"] == "capex cycle"

def test_heartbeat_counts_positions():
    hb = mon.heartbeat(_positions("NVDA", "MSFT")["positions"], "2026-07-22")
    assert hb == {"count": 2, "as_of": "2026-07-22"}

def test_all_default_items_have_meanings():
    for it in mon.DEFAULT_ITEMS:
        assert it in mon.ITEM_MEANINGS and mon.ITEM_MEANINGS[it]
```

```python
# tests/scout/test_monitor_names.py — AST scan (mirror tests/test_scoring_names.py)
import ast
import inspect
from shortlist.scout import monitor as mon


def test_emitted_breach_kinds_subset_of_declared():
    """Every string literal assigned to `kind=` inside compute_alerts must be declared in
    KNOWN_BREACH_KINDS — so a new breach kind can't ship undocumented."""
    src = inspect.getsource(mon.compute_alerts)
    tree = ast.parse(src)
    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "kind" and \
                isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            emitted.add(node.value.value)
    assert emitted, "AST scan found no kind= literals — scan is vacuous, fix it"
    assert emitted <= set(mon.KNOWN_BREACH_KINDS), emitted - set(mon.KNOWN_BREACH_KINDS)


def test_every_breach_kind_documented_in_glossary():
    from shortlist.scout.glossary import lookup
    for kind in sorted(mon.KNOWN_BREACH_KINDS):
        assert lookup(kind) is not None, f"no /explain entry for breach kind {kind}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_monitor.py tests/scout/test_monitor_names.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.scout.monitor'`

- [ ] **Step 3: Implement `monitor.py`**

```python
"""Position-monitor payload: which held names have a fresh clean-negative 8-K.

Pure leaf (stdlib only). Reads the daily run's already-computed veto_map — zero fetches.
See docs/POSITION_MONITOR.md §5. The alert routes to the filing; it emits no stance.
"""
from __future__ import annotations

KNOWN_BREACH_KINDS = frozenset({"8k_negative"})

# v1 subset of the negative-8-K item set — the clean, unambiguous negatives (§5.1).
DEFAULT_ITEMS = ("1.03", "2.04", "4.02")

ITEM_MEANINGS = {
    "1.03": "filed for bankruptcy",
    "2.04": "a lender is calling debt due early (default/acceleration)",
    "4.02": "its past financial statements can no longer be relied on — a restatement is coming",
}


def compute_alerts(positions: dict, veto_map: dict, items, seen) -> list[dict]:
    """One alert dict per held ticker with a fresh subset-item 8-K not already seen.

    positions: the {ticker: record} map (store["positions"]).
    veto_map:  {ticker: {"items": [...], "adsh": str, "last_date": iso}}.
    items:     iterable of item codes to alert on (the subset).
    seen:      set of already-surfaced "8k:<adsh>" keys.
    """
    wanted = set(items)
    out: list[dict] = []
    for ticker, rec in positions.items():
        v = veto_map.get(ticker)
        if not v:
            continue
        hit = [it for it in (v.get("items") or []) if it in wanted]
        if not hit:
            continue
        adsh = v.get("adsh")
        key = f"8k:{adsh}"
        if key in seen:
            continue
        lead = hit[0]
        out.append({
            "ticker": ticker,
            "kind": "8k_negative",
            "key": key,
            "adsh": adsh,
            "items": hit,
            "date": v.get("last_date"),
            "meaning": ITEM_MEANINGS.get(lead, "a material negative event was filed"),
            "thesis": rec.get("thesis"),
        })
    return out


def heartbeat(positions: dict, session_iso: str) -> dict:
    return {"count": len(positions), "as_of": session_iso}
```

- [ ] **Step 4: Add glossary entry for the breach kind**

In `src/shortlist/scout/glossary.py`, add one `Entry` to the `GLOSSARY` list (match the existing `Entry` fields — inspect a neighbor entry for the exact dataclass shape; it has `name`, `aliases`, `category`, and a body). Add an entry whose `name` is `"8k_negative"` with aliases covering the item codes, e.g.:

```python
    Entry(
        name="8k_negative",
        aliases=("8-k negative", "negative 8-k", "clean-negative 8-k",
                 "8k item 1.03", "8k item 2.04", "8k item 4.02"),
        category="SEC filings",
        body=("A clean-negative 8-K is a current report announcing an unambiguously bad, "
              "dated event: bankruptcy (item 1.03), a lender calling debt due early (2.04), "
              "or that past financial statements can no longer be relied on — a restatement "
              "(4.02). The position monitor surfaces one against a name you own as an "
              "attention flag routed to the SEC filing; it is screening triage, not advice, "
              "and never a recommendation to sell."),
    ),
```

Match the actual `Entry` constructor exactly (field names/order) — read `glossary.py:43` and a nearby entry first.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_monitor.py tests/scout/test_monitor_names.py tests/scout/test_glossary.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/monitor.py src/shortlist/scout/glossary.py \
        tests/scout/test_monitor.py tests/scout/test_monitor_names.py
git commit -m "feat(monitor): pure alert emitter + KNOWN_BREACH_KINDS + glossary"
```

---

### Task 7: Digest section — render the monitor payload in the daily report

**Files:**
- Modify: `src/shortlist/scout/report/viewmodel.py` (ReportVM field + build_view_model kwarg), `src/shortlist/scout/report/__init__.py` (build_report kwarg), `src/shortlist/scout/report/sections.py` (Section class + SECTIONS entry)
- Test: `tests/scout/test_monitor_section.py` (create)

**Interfaces:**
- Consumes: the payload dict `{"alerts": [...], "heartbeat": {"count", "as_of"}}` (assembled in Task 8).
- Produces: `ReportVM.positions_monitor: dict | None`; a `_PositionMonitor` section rendering alerts + heartbeat.

**Context:** Copy the `_ValidationScoreboard` pattern (`sections.py:644`). Thread the field through exactly like `validation` (`viewmodel.py:116`, `:240`; `report/__init__.py:35-40`). Place the section right after `_MacroHeader` in `SECTIONS` (`sections.py:730`) for "top". `applies()` keys on **payload presence** (heartbeat renders on quiet days), not alert presence.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_monitor_section.py
from datetime import date
from shortlist.scout.report.viewmodel import build_view_model
from shortlist.scout.report.sections import render_html_body, render_text, Detail
from shortlist.scout.models import RunManifest


def _manifest():
    return RunManifest(session=date(2026, 7, 22), signals=[], raw=0, after_dedup=0,
                       after_prefilter=0, screened=0, dropped_for_budget=0,
                       researched=[], notes=[])


def _pm(alerts):
    return {"alerts": alerts, "heartbeat": {"count": 3, "as_of": "2026-07-22"}}


def test_section_absent_when_payload_none():
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=None)
    assert "Monitoring" not in render_text(vm, Detail.FULL)


def test_heartbeat_renders_on_quiet_day():
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=_pm([]))
    txt = render_text(vm, Detail.FULL)
    assert "Monitoring 3 holding" in txt


def test_alert_renders_plain_english_and_ticker():
    alert = {"ticker": "NVDA", "kind": "8k_negative", "key": "8k:AAA", "adsh": "AAA",
             "items": ["4.02"], "date": "2026-07-19",
             "meaning": "its past financial statements can no longer be relied on — a restatement is coming",
             "thesis": "capex cycle"}
    vm = build_view_model([], _manifest(), assessments={}, positions_monitor=_pm([alert]))
    txt = render_text(vm, Detail.FULL)
    assert "NVDA" in txt and "relied on" in txt and "4.02" in txt
    html = render_html_body(vm)
    assert "NVDA" in html


def test_other_sections_byte_identical_when_payload_absent_vs_none():
    a = render_html_body(build_view_model([], _manifest(), assessments={}))
    b = render_html_body(build_view_model([], _manifest(), assessments={}, positions_monitor=None))
    assert a == b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_monitor_section.py -q`
Expected: FAIL — `TypeError: build_view_model() got an unexpected keyword argument 'positions_monitor'`

- [ ] **Step 3: Thread the field through the view-model**

`viewmodel.py` — add the field to `ReportVM` (after the `validation` field, ~`:116`):

```python
    positions_monitor: "dict | None" = None   # {"alerts": [...], "heartbeat": {...}} or None
```

`viewmodel.py` — extend `build_view_model` signature (`:219`) and the `ReportVM(...)` constructor (`:240`):

```python
def build_view_model(cards, manifest: RunManifest, *,
                     assessments: dict[str, dict], macro=None, portfolio=None,
                     prior_picks=None, validation=None, positions_monitor=None) -> ReportVM:
    ...
        validation=validation,
        positions_monitor=positions_monitor)
```

`report/__init__.py` — extend `build_report` (`:35`) and its `build_view_model` call (`:39`):

```python
def build_report(cards, manifest, *, assessments: dict[str, dict], macro=None,
                 portfolio=None, prior_picks=None, validation=None,
                 positions_monitor=None) -> ReportArtifacts:
    vm = build_view_model(cards, manifest, assessments=assessments, macro=macro,
                          portfolio=portfolio, prior_picks=prior_picks,
                          validation=validation, positions_monitor=positions_monitor)
```

- [ ] **Step 4: Add the `_PositionMonitor` section**

In `sections.py`, add the class (near `_ValidationScoreboard`, ~`:644`):

```python
class _PositionMonitor:
    """Held-name clean-negative 8-K alerts + a liveness heartbeat (docs/POSITION_MONITOR.md
    §5). Display-only; byte-identical when vm.positions_monitor is None. applies() keys on
    payload PRESENCE so the heartbeat renders on quiet days."""
    id, title = "positions", "Holdings watch"

    def applies(self, vm) -> bool:
        return isinstance(vm.positions_monitor, dict)

    @staticmethod
    def _alert_lines(pm) -> list[str]:
        lines = []
        for a in pm.get("alerts", []):
            lines.append(f"⚠ {a['ticker']} — {a['meaning']}. "
                         f"8-K item {'+'.join(a['items'])}, filed {a['date']}")
            if a.get("thesis"):
                lines.append(f"    your thesis: \"{a['thesis']}\"")
            else:
                lines.append(f"    ⚠ no thesis — /thesis {a['ticker']} <why you own it>")
        return lines

    def render_html(self, vm, h):
        pm = vm.positions_monitor
        parts = []
        alines = self._alert_lines(pm)
        if alines:
            items = "".join(h.tag("li", ln) for ln in alines)
            parts.append(h.raw("ul", items))
        hb = pm.get("heartbeat") or {}
        parts.append(h.tag("div", f"Monitoring {hb.get('count', 0)} holding(s) · "
                                  f"last filing check {hb.get('as_of', '—')}"))
        return "".join(parts)

    def render_text(self, vm, detail):
        pm = vm.positions_monitor
        out = list(self._alert_lines(pm))
        hb = pm.get("heartbeat") or {}
        out.append(f"Monitoring {hb.get('count', 0)} holding(s) · "
                   f"last filing check {hb.get('as_of', '—')}")
        return out
```

Note: confirm the `render_text` return contract against a neighbor section (`_ValidationScoreboard.render_text`) — some return `list[str]`, some `str`. Match the neighbor exactly.

Add to `SECTIONS` (`:730`), right after `_MacroHeader()`:

```python
SECTIONS: list[Section] = [_MacroHeader(), _PositionMonitor(), _Leaderboard(), _Fundamentals(),
                            _Research(), _DeepBlock(), _PriorPicks(), _ValidationScoreboard(),
                            _Portfolio(), _Glossary(), _Footer()]
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_monitor_section.py -q`
Expected: PASS

- [ ] **Step 6: Run the full report suite for byte-identical regressions**

Run: `uv run pytest tests/scout/ -q && uv run shortlist --demo --json > /tmp/pm_demo.json && echo OK`
Expected: PASS; demo still runs.

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/scout/report/ tests/scout/test_monitor_section.py
git commit -m "feat(report): position-monitor digest section (alerts + heartbeat)"
```

---

### Task 8: daily.py integration — set_held feed + failure-isolated monitor step + config

**Files:**
- Modify: `src/shortlist/scout/daily.py` (`run`: set_held feed; payload compute; thread into `build_report`; persist after deliver), `config.yaml` (`portfolio.monitor` block)
- Test: `tests/scout/test_daily_monitor.py` (create)

**Interfaces:**
- Consumes: Task 1 (`positions`), Task 5 (`state.position_alerts_seen`/`add_position_alerts`), Task 6 (`monitor`), Task 7 (`build_report(..., positions_monitor=)`).
- Produces: the daily digest carries the monitor section; `set_held` reflects the store; dedup persists.

**Context:** `veto_map` is live from `daily.py:505` through the `build_report` call at `:581`. The monitor is three insertions: (1) `set_held` feed near the top of `run` before prefilter (`:494`); (2) payload compute + thread just before `:581`; (3) persist after `deliver()` near `:604`. All failure-isolated (catch, note, never crash the delivered run — the `_record_session_picks` precedent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scout/test_daily_monitor.py
from datetime import date
from shortlist.scout import daily
from shortlist.scout.state import ScoutState
from shortlist.scout import positions as _unused  # noqa  (import guard)
from shortlist import positions as pos


def test_build_monitor_payload_filters_and_dedups(tmp_path):
    store = {"version": 1, "positions": {
        "NVDA": {"added": "2026-01-01", "shares": 10, "thesis": "t", "entry_card": None}}}
    pos.save_store(tmp_path / "positions.json", store)
    state = ScoutState(tmp_path / "state.json")
    veto_map = {"NVDA": {"items": ["4.02"], "adsh": "AAA", "last_date": "2026-07-19"},
                "MSFT": {"items": ["4.02"], "adsh": "BBB", "last_date": "2026-07-19"}}  # unheld
    payload = daily._build_monitor_payload(
        str(tmp_path / "positions.json"), veto_map,
        items=("1.03", "2.04", "4.02"), state=state, session=date(2026, 7, 22))
    assert payload["heartbeat"]["count"] == 1
    assert [a["ticker"] for a in payload["alerts"]] == ["NVDA"]   # MSFT filtered (unheld)


def test_monitor_payload_none_when_disabled():
    assert daily._build_monitor_payload_if_enabled(
        {"portfolio": {"monitor": {"enabled": False}}}, veto_map={}, state=None,
        session=date(2026, 7, 22)) is None


def test_persist_marks_alerts_seen(tmp_path):
    state = ScoutState(tmp_path / "state.json")
    payload = {"alerts": [{"key": "8k:AAA"}, {"key": "8k:BBB"}], "heartbeat": {}}
    daily._persist_monitor(state, payload)
    assert set(state.position_alerts_seen()) == {"8k:AAA", "8k:BBB"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scout/test_daily_monitor.py -q`
Expected: FAIL — `AttributeError: module 'shortlist.scout.daily' has no attribute '_build_monitor_payload'`

- [ ] **Step 3: Add the helper functions to `daily.py`**

Add near the other module helpers (e.g. after `_negative_veto_sweep`):

```python
def _build_monitor_payload(store_path, veto_map, *, items, state, session):
    """Pure-ish: read the store, compute alerts (dedup vs state) + heartbeat. Never raises
    into the caller — the caller wraps it, but keep it total anyway."""
    from .. import positions as pos
    from . import monitor as mon
    store = pos.load_store(store_path)
    positions = store.get("positions", {})
    seen = set(state.position_alerts_seen())
    alerts = mon.compute_alerts(positions, veto_map, items, seen)
    return {"alerts": alerts, "heartbeat": mon.heartbeat(positions, session.isoformat())}


def _build_monitor_payload_if_enabled(config, *, veto_map, state, session):
    """Returns the payload dict, or None when the monitor block is absent/disabled (→ the
    section never renders and the digest is byte-identical)."""
    mon_cfg = ((config.get("portfolio") or {}).get("monitor") or {})
    if not mon_cfg.get("enabled"):
        return None
    store_path = (config.get("portfolio") or {}).get("store", "positions.json")
    items = tuple(mon_cfg.get("items", ("1.03", "2.04", "4.02")))
    try:
        return _build_monitor_payload(store_path, veto_map, items=items,
                                      state=state, session=session)
    except Exception as exc:  # noqa: BLE001 — never crash a delivered run
        return {"alerts": [], "heartbeat": {"count": 0, "as_of": session.isoformat()},
                "note": f"position monitor failed: {redact_secrets(str(exc))}"}


def _persist_monitor(state, payload) -> None:
    """Mark this run's alert keys seen (dedup). Idempotent; failure-isolated by the caller."""
    if not payload:
        return
    keys = [a["key"] for a in payload.get("alerts", []) if a.get("key")]
    if keys:
        state.add_position_alerts(keys)


def _feed_held(config, state) -> None:
    """Populate ScoutState.held from the position store so discovery stops re-surfacing owned
    names (funnel.py uses is_held). Failure-isolated. Reads positions.json, never writes it."""
    try:
        from .. import positions as pos
        store_path = (config.get("portfolio") or {}).get("store", "positions.json")
        tickers = list(pos.load_store(store_path).get("positions", {}).keys())
        state.set_held(tickers)
    except Exception:  # noqa: BLE001
        pass
```

- [ ] **Step 4: Wire the three insertions into `run()`**

(a) `set_held` feed — add just before the `prefilter(...)` call (`daily.py:494`), guarded to non-demo:

```python
    if not demo:
        _feed_held(config, state)
```

(b) Payload compute + thread — just before `build_report` (`daily.py:581`):

```python
    monitor_payload = None if demo else _build_monitor_payload_if_enabled(
        config, veto_map=veto_map, state=state, session=session)
    if monitor_payload and monitor_payload.get("note"):
        manifest.notes.append(monitor_payload["note"])
    artifacts = build_report(cards, manifest, assessments=assessments, macro=macro,
                             prior_picks=prior_picks, validation=validation,
                             positions_monitor=monitor_payload)
```

(c) Persist — after the `_record_session_picks` block (`daily.py:608`), failure-isolated:

```python
    try:
        _persist_monitor(state, monitor_payload)
    except Exception as exc:  # noqa: BLE001 — persist must not crash a delivered run
        print(f"scout: monitor persist failed: {redact_secrets(str(exc))}", file=sys.stderr)
```

- [ ] **Step 5: Add the config block to `config.yaml`**

Under the existing `portfolio:` block (near `config.yaml:692`):

```yaml
portfolio:
  path: portfolio.csv      # legacy CSV — no longer read (superseded by the store)
  store: positions.json    # bot-owned source of truth (gitignored); atomic writes
  decisions: decisions.jsonl   # append-only decision ledger (gitignored)
  max_holdings: 50
  monitor:
    enabled: true          # remove this block -> daily run byte-identical (no monitor section)
    items: ["1.03", "2.04", "4.02"]   # v1 clean-negative subset (§5.1); widen on evidence
```

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/scout/test_daily_monitor.py -q`
Expected: PASS (3 passed)

- [ ] **Step 7: Full suite + demo byte-identical guard**

Run: `uv run pytest -q && uv run shortlist --demo --json > /tmp/pm_demo2.json && echo OK`
Expected: PASS. (The demo path sets `monitor_payload=None`, so its output is unaffected.)

- [ ] **Step 8: Commit**

```bash
git add src/shortlist/scout/daily.py config.yaml tests/scout/test_daily_monitor.py
git commit -m "feat(daily): failure-isolated position-monitor step + set_held feed + config"
```

---

### Task 9: gitignore + docs sync

**Files:**
- Modify: `.gitignore` (positions.json, decisions.jsonl), `CLAUDE.md` (one paragraph), `README.md`/`HARNESS.md` if they enumerate bot commands (check first)
- Test: none (docs); run the disabled-invariance check.

- [ ] **Step 1: Add the store files to `.gitignore`**

Append:

```
# Position monitor (user-owned holdings + decision log)
positions.json
decisions.jsonl
```

- [ ] **Step 2: Add a CLAUDE.md paragraph**

Under a suitable section (near the `/portfolio` / bot description), add a short paragraph describing the position monitor: bot-owned `positions.json`, the `/add`/`/thesis`/`/hold`/`/remove` commands, the clean-negative-8-K digest alert on `{1.03, 2.04, 4.02}`, the two-store split ownership, and a pointer to `docs/POSITION_MONITOR.md`. Match the file's existing prose density.

- [ ] **Step 3: Verify the disabled-block invariance end-to-end**

Run:
```bash
uv run pytest -q
git stash list  # ensure clean
# Temporarily confirm: with portfolio.monitor removed from a copied config, the demo output is unchanged.
uv run shortlist --demo --json | head -c 200 && echo " ... OK"
```
Expected: suite green; demo unaffected.

- [ ] **Step 4: Commit**

```bash
git add .gitignore CLAUDE.md
git commit -m "docs: gitignore position store; document the monitor in CLAUDE.md"
```

---

## Self-Review

**1. Spec coverage** (`docs/POSITION_MONITOR.md` → task):
- §3.1 two-store ownership → Task 1 (store), Task 5 (state), Task 8 (`_feed_held`/`_persist_monitor` read-only on store). ✓
- §3.2 schema + minimal entry_card → Task 1, Task 4 (`_do_add` captures composite/sources/as_of). ✓
- §3.3 non-destructive `/remove` + decision ledger → Task 1 (`append_decision`), Task 4 (embeds full record). ✓
- §4 commands + grammar + first-run (_HELP, empty-state) → Task 3 (parse), Task 4 (handlers + _HELP + empty-state). ✓
- §5.1 item subset + zero-fetch veto_map reuse + dedup → Task 6 (`DEFAULT_ITEMS`, `compute_alerts`), Task 8 (veto_map reuse). ✓
- §5.2 digest section + heartbeat + applies-on-presence → Task 7. ✓
- §5.3 plain-English copy + thesis-less nudge → Task 6 (`ITEM_MEANINGS`), Task 7 (alert lines). ✓
- §6 wiring (set_held, three insertions, summarize crash-fix) → Task 2, Task 8. ✓
- §7 config (`monitor` block, `include_fmp` free-chain) → Task 4 (`_free_sources`), Task 8 (config). ✓
- §8 failure modes (corrupt store, monitor raises, shares=None) → Task 1, Task 2, Task 8 (try/except). ✓
- §9 tests (dedup fire-once, KNOWN_BREACH_KINDS AST, section invariance, shares=None) → Tasks 5,6,7,2. ✓
- §11 framing (no stance) → Task 6 (routes to filing), Task 7 (no verdict). ✓
- Cut items (§10) — none implemented (no drawdown, no last_prompted, no lots). ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Two explicit "read the neighbor first" notes (Task 2 card helper, Task 6 `Entry` shape, Task 7 render_text contract) point at concrete existing code, not vague intent. ✓

**3. Type consistency:** `Holding.shares: Optional[float]` (Task 2) consumed by Task 1 `holdings_view`. `entry_card` dict shape `{composite, sources, as_of}` written in Task 4, stored by Task 1, never re-read in v1 (seam). `compute_alerts` alert dict keys (`ticker/kind/key/adsh/items/date/meaning/thesis`) produced in Task 6, consumed by Task 7 (`_alert_lines`) and Task 8 (`_persist_monitor` reads `key`). `positions_monitor` payload `{alerts, heartbeat}` produced in Task 8, consumed in Task 7 — consistent. `position_alerts_seen`/`add_position_alerts` (Task 5) called in Task 8. ✓

Fixes applied inline: none needed after review.
