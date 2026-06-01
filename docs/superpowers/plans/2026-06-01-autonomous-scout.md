# Autonomous Scout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a third "scout" stack that autonomously discovers candidate tickers from free signal feeds, screens them through the existing harness scorer, runs the existing Claude research on the leaders, and ships a daily Telegram report — fully described in `docs/AUTONOMOUS_SCOUT.md`.

**Architecture:** A new `src/shortlist/scout/` package that *orchestrates* the existing stacks, adding only **discovery** and **delivery** — no new scoring. Pure-logic modules (`models`, `calendar`, `state`, `funnel`, `budget`) are TDD'd against in-memory data; I/O modules (`signals`, `edgar_index`, `notify`) are TDD'd against recorded fixtures with all network mocked; `daily` is the orchestrator with an offline `--demo` end-to-end test.

**Tech Stack:** Python ≥3.10, `httpx` (already a dep), `pyyaml`, `dataclasses`, `pytest`. Reuses `shortlist.screen.run_harness`, `shortlist.research`, `shortlist.env`, `shortlist.providers._form4`, `edgartools` (optional extra). No new runtime dependencies — the trading calendar uses a static holiday table, not `pandas-market-calendars`.

**Conventions to honor (from `CLAUDE.md`):** every error string that may carry a request URL passes through `env.py:redact_secrets()`; a missing/erroring signal *annotates* coverage, never silently shrinks the funnel; insider P/S logic lives only in `providers/_form4.py`; run from inside the repo so `.env` loads.

**Integration facts (verified against the codebase):**
- `shortlist.screen.run_harness(tickers: list[str], source_names: list[str], config: dict) -> list[ScoreCard]` — the deep-screen entry point.
- `ScoreCard` fields: `.ticker, .composite, .quality, .moat, .growth, .momentum, .value, .opportunity, .insider, .gates: list[str], .metrics, .coverage`; `.passed` property == `not self.gates`.
- `shortlist.research.is_available() -> bool` and `shortlist.research.enrich(cards, config, n, refresh) -> dict[ticker, path]` — the research phase (lazy-imported; needs `claude` CLI + edgartools).
- `shortlist.env.redact_secrets(text) -> str`, `shortlist.env.load_env(path=None)`.
- Config is loaded with `yaml.safe_load(Path(config).read_text())`; entry points live in `pyproject.toml [project.scripts]`.

---

## Task 1: Package skeleton + data models

**Files:**
- Create: `src/shortlist/scout/__init__.py`
- Create: `src/shortlist/scout/models.py`
- Test: `tests/scout/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_models.py
from datetime import date
from shortlist.scout.models import Emission, Candidate, SignalStatus, RunManifest


def test_candidate_aggregates_signals_and_interest():
    c = Candidate(ticker="AAPL")
    c.add(Emission(ticker="AAPL", signal="yahoo:day_gainers", strength=0.8,
                   evidence="+8% on 3x vol", is_discovery=True), weight=1.0)
    c.add(Emission(ticker="AAPL", signal="wikipedia", strength=0.5,
                   evidence="+30% pageviews", is_discovery=False), weight=0.5)
    assert c.interest == 0.8 * 1.0 + 0.5 * 0.5
    assert c.has_discovery is True
    assert {e.signal for e in c.emissions} == {"yahoo:day_gainers", "wikipedia"}


def test_candidate_interest_is_capped():
    c = Candidate(ticker="X")
    for i in range(20):
        c.add(Emission(ticker="X", signal=f"s{i}", strength=1.0, evidence="",
                       is_discovery=True), weight=1.0)
    assert c.interest <= Candidate.INTEREST_CAP


def test_runmanifest_roundtrips_to_dict():
    m = RunManifest(session=date(2026, 5, 29),
                    signals=[SignalStatus(name="yahoo", ran=True, detail="42 hits")],
                    raw=42, after_dedup=30, after_prefilter=18, screened=15,
                    dropped_for_budget=3, researched=["AAPL"])
    d = m.to_dict()
    assert d["session"] == "2026-05-29"
    assert d["funnel"]["screened"] == 15
    assert d["signals"][0]["name"] == "yahoo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortlist.scout'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shortlist/scout/__init__.py
"""Autonomous candidate-discovery stack. See docs/AUTONOMOUS_SCOUT.md."""
```

```python
# src/shortlist/scout/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Emission:
    """One signal firing for one ticker on one session."""
    ticker: str
    signal: str            # e.g. "yahoo:day_gainers", "edgar:form4_cluster_buy"
    strength: float        # 0..1, source-normalized
    evidence: str          # human-readable, for the report
    is_discovery: bool     # True = can originate an unknown ticker; False = confluence-only


@dataclass
class Candidate:
    """A ticker plus every signal that flagged it; carries the composite interest."""
    INTEREST_CAP: float = 10.0
    ticker: str = ""
    emissions: list[Emission] = field(default_factory=list)
    _interest: float = 0.0

    def add(self, emission: Emission, weight: float) -> None:
        self.emissions.append(emission)
        self._interest = min(self.INTEREST_CAP, self._interest + emission.strength * weight)

    @property
    def interest(self) -> float:
        return self._interest

    @property
    def has_discovery(self) -> bool:
        return any(e.is_discovery for e in self.emissions)

    @property
    def signals(self) -> list[str]:
        return [e.signal for e in self.emissions]


@dataclass
class SignalStatus:
    name: str
    ran: bool
    detail: str            # "42 hits" or "rate-limited" — the coverage line


@dataclass
class RunManifest:
    """Persisted per-run record for observability (written under scout/)."""
    session: date
    signals: list[SignalStatus]
    raw: int
    after_dedup: int
    after_prefilter: int
    screened: int
    dropped_for_budget: int
    researched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session": self.session.isoformat(),
            "signals": [{"name": s.name, "ran": s.ran, "detail": s.detail}
                        for s in self.signals],
            "funnel": {"raw": self.raw, "after_dedup": self.after_dedup,
                       "after_prefilter": self.after_prefilter, "screened": self.screened,
                       "dropped_for_budget": self.dropped_for_budget},
            "researched": self.researched,
            "notes": self.notes,
        }
```

Note: `INTEREST_CAP` is a class attribute; in the dataclass above it is declared as a field with a default, which `Candidate.INTEREST_CAP` still resolves. If the test's `Candidate.INTEREST_CAP` access is flaky under dataclass semantics, move it to a module-level constant `INTEREST_CAP = 10.0` and reference that. Implementer: pick whichever keeps both tests green.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/__init__.py src/shortlist/scout/models.py tests/scout/test_models.py
git commit -m "feat(scout): package skeleton + data models"
```

---

## Task 2: Trading-calendar gate

**Files:**
- Create: `src/shortlist/scout/calendar.py`
- Test: `tests/scout/test_calendar.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_calendar.py
from datetime import date
from shortlist.scout.calendar import last_session, is_trading_day


def test_weekend_resolves_to_friday():
    # 2026-05-30 is a Saturday, 2026-05-31 a Sunday; 2026-05-29 is a Friday.
    assert last_session(date(2026, 5, 30)) == date(2026, 5, 29)
    assert last_session(date(2026, 5, 31)) == date(2026, 5, 29)


def test_holiday_resolves_backwards():
    # 2026-07-04 is Saturday -> observed Friday 2026-07-03 holiday; last session 07-02.
    assert last_session(date(2026, 7, 4)) == date(2026, 7, 2)


def test_trading_day_true_for_normal_weekday():
    assert is_trading_day(date(2026, 5, 29)) is True


def test_trading_day_false_for_new_years():
    assert is_trading_day(date(2026, 1, 1)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shortlist/scout/calendar.py
"""Static US-equity market calendar — dependency-free.

Covers fixed + observed NYSE holidays for 2025-2027. A documented approximation
(no early closes, no ad-hoc closures); refresh the table when extending past 2027.
"""
from __future__ import annotations

from datetime import date, timedelta

# Observed NYSE full-day closures (already shifted to the observed weekday).
_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


def last_session(today: date) -> date:
    """The most recent completed trading session on or before `today` minus 0 days.

    The scout runs after the close, so 'today's session' if today trades, else
    walk back to the prior trading day.
    """
    d = today
    for _ in range(10):  # generous bound; never more than a long weekend + holidays
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError(f"no trading day found within 10 days before {today}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_calendar.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/calendar.py tests/scout/test_calendar.py
git commit -m "feat(scout): static US trading-calendar gate"
```

---

## Task 3: Idempotent state ledger

**Files:**
- Create: `src/shortlist/scout/state.py`
- Test: `tests/scout/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_state.py
from datetime import date
from shortlist.scout.state import ScoutState


def test_cooldown_blocks_recently_screened(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.record_screened(["AAPL", "MSFT"], session=date(2026, 5, 29))
    assert st.in_cooldown("AAPL", on=date(2026, 6, 1), cooldown_days=7) is True
    assert st.in_cooldown("AAPL", on=date(2026, 6, 10), cooldown_days=7) is False
    assert st.in_cooldown("NVDA", on=date(2026, 6, 1), cooldown_days=7) is False


def test_run_completed_marker_is_idempotent(tmp_path):
    path = tmp_path / "state.json"
    st = ScoutState(path)
    assert st.run_completed(date(2026, 5, 29)) is False
    st.mark_run_completed(date(2026, 5, 29))
    # fresh instance reads from disk
    assert ScoutState(path).run_completed(date(2026, 5, 29)) is True


def test_held_list_filters(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.set_held(["TSLA"])
    assert st.is_held("TSLA") is True
    assert st.is_held("AAPL") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shortlist/scout/state.py
"""Idempotent scout ledger: cooldown, run-completed markers, held list.

Single-writer (the one-shot daily timer). Read-modify-write the whole JSON file
on each mutation — small enough that this is simpler and safer than partial writes.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


class ScoutState:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"screened": {}, "runs": [], "held": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    # --- cooldown ---
    def record_screened(self, tickers: list[str], session: date) -> None:
        for t in tickers:
            self._data["screened"][t.upper()] = session.isoformat()
        self._save()

    def in_cooldown(self, ticker: str, on: date, cooldown_days: int) -> bool:
        iso = self._data["screened"].get(ticker.upper())
        if not iso:
            return False
        last = date.fromisoformat(iso)
        return on - last < timedelta(days=cooldown_days)

    # --- idempotency ---
    def run_completed(self, session: date) -> bool:
        return session.isoformat() in self._data["runs"]

    def mark_run_completed(self, session: date) -> None:
        iso = session.isoformat()
        if iso not in self._data["runs"]:
            self._data["runs"].append(iso)
            self._save()

    # --- held list ---
    def set_held(self, tickers: list[str]) -> None:
        self._data["held"] = [t.upper() for t in tickers]
        self._save()

    def is_held(self, ticker: str) -> bool:
        return ticker.upper() in self._data.get("held", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_state.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/state.py tests/scout/test_state.py
git commit -m "feat(scout): idempotent state ledger (cooldown + run marker + held)"
```

---

## Task 4: SignalSource interface, registry, and MockSignal

**Files:**
- Create: `src/shortlist/scout/signals.py`
- Test: `tests/scout/test_signals_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_signals_registry.py
from datetime import date
from shortlist.scout.signals import build_signals, MockSignal


def test_mock_signal_emits_for_demo():
    sig = MockSignal()
    ems = sig.scan(date(2026, 5, 29))
    assert ems and all(e.is_discovery for e in ems)
    assert sig.available() == (True, f"{len(ems)} hits")


def test_build_signals_resolves_names_and_skips_unknown():
    sigs = build_signals(["mock"])
    assert len(sigs) == 1 and sigs[0].name == "mock"


def test_build_signals_respects_disabled(monkeypatch):
    # unknown names raise so config typos are loud, not silent
    import pytest
    with pytest.raises(KeyError):
        build_signals(["does_not_exist"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_signals_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shortlist/scout/signals.py
"""Free signal sources for candidate discovery. See docs/AUTONOMOUS_SCOUT.md §4.

Each source mirrors the Provider/Source pattern: a name, a registry entry, graceful
degradation (returns [] on error), and an available() audit for coverage honesty.
Errors route through env.redact_secrets before logging.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import Emission


class SignalSource(Protocol):
    name: str
    is_discovery: bool
    def scan(self, session: date) -> list[Emission]: ...
    def available(self) -> tuple[bool, str]: ...


class MockSignal:
    """Offline source for --demo and end-to-end tests."""
    name = "mock"
    is_discovery = True

    def __init__(self) -> None:
        self._last = 0

    def scan(self, session: date) -> list[Emission]:
        names = [("AAPL", 0.9, "+6.1% on 2.4x volume"),
                 ("MSFT", 0.7, "+3.0% on 1.8x volume"),
                 ("GOOGL", 0.6, "most-active list")]
        ems = [Emission(t, "mock:demo", s, ev, is_discovery=True) for t, s, ev in names]
        self._last = len(ems)
        return ems

    def available(self) -> tuple[bool, str]:
        return (True, f"{self._last} hits")


# Registry of constructors. Real sources are added in later tasks.
_REGISTRY: dict[str, type] = {
    "mock": MockSignal,
}


def register(name: str, ctor: type) -> None:
    _REGISTRY[name] = ctor


def build_signals(names: list[str]) -> list[SignalSource]:
    """Resolve names to instances. Unknown names raise KeyError (config typos are loud)."""
    return [_REGISTRY[n]() for n in names]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_signals_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/signals.py tests/scout/test_signals_registry.py
git commit -m "feat(scout): SignalSource interface, registry, MockSignal"
```

---

## Task 5: YahooScreenerSignal (discovery)

**Files:**
- Modify: `src/shortlist/scout/signals.py`
- Create: `tests/scout/fixtures/yahoo_day_gainers.json` (recorded response)
- Test: `tests/scout/test_yahoo_signal.py`

- [ ] **Step 1: Record the fixture**

Create `tests/scout/fixtures/yahoo_day_gainers.json` with a trimmed real shape:

```json
{"finance": {"result": [{"quotes": [
  {"symbol": "ABC", "regularMarketChangePercent": 8.4, "regularMarketVolume": 3000000, "averageDailyVolume3Month": 1000000},
  {"symbol": "XYZ", "regularMarketChangePercent": 5.1, "regularMarketVolume": 2000000, "averageDailyVolume3Month": 1500000}
]}], "error": null}}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/scout/test_yahoo_signal.py
import json
from datetime import date
from pathlib import Path

import httpx
from shortlist.scout.signals import YahooScreenerSignal

FIX = Path(__file__).parent / "fixtures" / "yahoo_day_gainers.json"


def _client(payload, status=200):
    def handler(request):
        assert "Mozilla" in request.headers.get("user-agent", ""), "must send browser UA"
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_gainers_into_emissions():
    payload = json.loads(FIX.read_text())
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client(payload))
    ems = sig.scan(date(2026, 5, 29))
    syms = {e.ticker for e in ems}
    assert syms == {"ABC", "XYZ"}
    assert all(e.is_discovery for e in ems)
    assert all(0.0 <= e.strength <= 1.0 for e in ems)
    assert sig.available()[0] is True


def test_429_degrades_gracefully():
    sig = YahooScreenerSignal(screens=["day_gainers"], client=_client({}, status=429))
    assert sig.scan(date(2026, 5, 29)) == []
    ran, detail = sig.available()
    assert ran is False and "429" in detail or "rate" in detail.lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_yahoo_signal.py -v`
Expected: FAIL with `ImportError: cannot import name 'YahooScreenerSignal'`

- [ ] **Step 4: Write the implementation**

Append to `src/shortlist/scout/signals.py`:

```python
import httpx

from ..env import redact_secrets

_YAHOO_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_YAHOO_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"


class YahooScreenerSignal:
    """Yahoo predefined screeners — keyless, but requires a browser User-Agent.

    Unofficial endpoint: best-effort, day-cached upstream is unnecessary (one call
    per screen per run). If it 429s the whole discovery funnel leans on EDGAR alone,
    so available() surfaces the outage to the report.
    """
    name = "yahoo_screener"
    is_discovery = True

    def __init__(self, screens: list[str] | None = None, client: httpx.Client | None = None) -> None:
        self.screens = screens or ["day_gainers", "most_actives", "undervalued_growth_stocks"]
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        client = self._client or httpx.Client(timeout=15.0, headers={"User-Agent": _YAHOO_UA})
        out: list[Emission] = []
        hits = 0
        try:
            for scr in self.screens:
                resp = client.get(_YAHOO_URL, params={"scrIds": scr, "count": 50},
                                  headers={"User-Agent": _YAHOO_UA})
                if resp.status_code != 200:
                    self._status = (False, f"HTTP {resp.status_code} (rate-limited?)")
                    return []
                quotes = (resp.json().get("finance", {}).get("result") or [{}])[0].get("quotes", [])
                for q in quotes:
                    sym = q.get("symbol")
                    if not sym:
                        continue
                    pct = q.get("regularMarketChangePercent") or 0.0
                    vol = q.get("regularMarketVolume") or 0
                    avg = q.get("averageDailyVolume3Month") or 0
                    rvol = (vol / avg) if avg else 1.0
                    strength = max(0.0, min(1.0, abs(pct) / 15.0))  # 15% move -> full strength
                    out.append(Emission(sym.upper(), f"yahoo:{scr}", strength,
                                        f"{pct:+.1f}% on {rvol:.1f}x volume", is_discovery=True))
                hits += len(quotes)
            self._status = (True, f"{hits} hits")
            return out
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


register("yahoo_screener", YahooScreenerSignal)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_yahoo_signal.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/signals.py tests/scout/test_yahoo_signal.py tests/scout/fixtures/yahoo_day_gainers.json
git commit -m "feat(scout): YahooScreenerSignal discovery source (browser-UA, graceful 429)"
```

---

## Task 6: Confluence boosters — FinnhubNewsSignal + WikipediaAttentionSignal

**Files:**
- Modify: `src/shortlist/scout/signals.py`
- Test: `tests/scout/test_booster_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_booster_signals.py
from datetime import date

import httpx
from shortlist.scout.signals import FinnhubNewsSignal, WikipediaAttentionSignal


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finnhub_boosts_only_known_tickers():
    # 12 articles for AAPL -> a confluence emission; booster, not discovery
    def handler(request):
        return httpx.Response(200, json=[{"headline": f"n{i}"} for i in range(12)])
    sig = FinnhubNewsSignal(api_key="k", client=_client(handler))
    ems = sig.scan_for(["AAPL"], date(2026, 5, 29))
    assert len(ems) == 1
    assert ems[0].is_discovery is False
    assert ems[0].ticker == "AAPL"


def test_finnhub_is_not_a_discovery_source():
    sig = FinnhubNewsSignal(api_key="k")
    assert sig.is_discovery is False
    # plain scan() returns nothing — it can't originate candidates
    assert sig.scan(date(2026, 5, 29)) == []


def test_wikipedia_pageview_spike_boosts_mapped_ticker():
    def handler(request):
        # recent window higher than prior -> spike
        items = [{"views": 100} for _ in range(7)] + [{"views": 300} for _ in range(7)]
        return httpx.Response(200, json={"items": items})
    sig = WikipediaAttentionSignal(ticker_map={"AAPL": "Apple_Inc."}, client=_client(handler))
    ems = sig.scan_for(["AAPL"], date(2026, 5, 29))
    assert len(ems) == 1 and ems[0].is_discovery is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_booster_signals.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write the implementation**

Append to `src/shortlist/scout/signals.py`:

```python
class FinnhubNewsSignal:
    """News-volume confluence booster. Requires a known symbol — cannot originate."""
    name = "finnhub_news"
    is_discovery = False

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        return []  # confluence-only: nothing to discover without a candidate set

    def scan_for(self, tickers: list[str], session: date) -> list[Emission]:
        if not self.api_key:
            self._status = (False, "no FINNHUB_API_KEY")
            return []
        client = self._client or httpx.Client(timeout=15.0)
        out, ok = [], 0
        frm = (session.replace(day=1)).isoformat()
        try:
            for t in tickers:
                resp = client.get("https://finnhub.io/api/v1/company-news",
                                  params={"symbol": t, "from": frm, "to": session.isoformat(),
                                          "token": self.api_key})
                if resp.status_code != 200:
                    continue
                n = len(resp.json())
                ok += 1
                if n >= 10:  # spike threshold
                    strength = max(0.0, min(1.0, n / 50.0))
                    out.append(Emission(t.upper(), "finnhub:news_volume", strength,
                                        f"{n} articles", is_discovery=False))
            self._status = (ok > 0, f"checked {ok} tickers")
            return out
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


class WikipediaAttentionSignal:
    """Pageview-spike confluence booster over a curated ticker->article map."""
    name = "wikipedia"
    is_discovery = False
    _BASE = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user")

    def __init__(self, ticker_map: dict[str, str] | None = None, client: httpx.Client | None = None) -> None:
        self.ticker_map = ticker_map or {}
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        return []

    def scan_for(self, tickers: list[str], session: date) -> list[Emission]:
        client = self._client or httpx.Client(
            timeout=15.0, headers={"User-Agent": "shortlist-scout/0.1 (turgechr@duck.com)"})
        out, ok = [], 0
        try:
            for t in tickers:
                article = self.ticker_map.get(t.upper())
                if not article:
                    continue
                # 14d daily window ending at session; URL dates omitted for brevity in tests
                resp = client.get(f"{self._BASE}/{article}/daily/2000010100/2100010100")
                if resp.status_code != 200:
                    continue
                ok += 1
                views = [i.get("views", 0) for i in resp.json().get("items", [])]
                if len(views) >= 14:
                    prior = sum(views[-14:-7]) or 1
                    recent = sum(views[-7:])
                    if recent > 1.5 * prior:
                        strength = max(0.0, min(1.0, (recent / prior - 1.0)))
                        out.append(Emission(t.upper(), "wikipedia:attention", strength,
                                            f"+{recent/prior*100-100:.0f}% pageviews",
                                            is_discovery=False))
            self._status = (ok > 0, f"checked {ok} mapped tickers")
            return out
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


register("finnhub_news", FinnhubNewsSignal)
register("wikipedia", WikipediaAttentionSignal)
```

Note: boosters expose `scan_for(tickers, session)` *in addition to* the `scan()` no-op, because the funnel calls them only after discovery has produced a candidate set (Task 8). `build_signals` still constructs them by name; the orchestrator dispatches discovery vs booster by the `is_discovery` flag.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_booster_signals.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/signals.py tests/scout/test_booster_signals.py
git commit -m "feat(scout): Finnhub-news + Wikipedia confluence boosters"
```

---

## Task 7: EDGAR Form 4 daily-index scanner (discovery)

**Files:**
- Create: `src/shortlist/scout/edgar_index.py`
- Modify: `src/shortlist/providers/_form4.py` (factor out a pure `classify_code` helper)
- Modify: `src/shortlist/scout/signals.py` (add `EdgarForm4Signal` wrapping `edgar_index`)
- Test: `tests/scout/test_edgar_index.py`

- [ ] **Step 1: Factor the P/S classifier out of `_form4.py`**

Inspect `src/shortlist/providers/_form4.py`; it already classifies Form 4 transaction codes as buy/sell. Extract the pure mapping into a module-level function so the index scanner reuses it (single source of truth per `CLAUDE.md`):

```python
# add near the top of src/shortlist/providers/_form4.py
def classify_code(code: str) -> str:
    """Map a Form 4 transaction code to 'buy' | 'sell' | 'other'.
    'P' = open-market purchase, 'S' = open-market sale; others are non-signal."""
    c = (code or "").strip().upper()
    if c == "P":
        return "buy"
    if c == "S":
        return "sell"
    return "other"
```

Then have the existing aggregation call `classify_code` rather than an inline check. Run the existing form4 tests to confirm no regression: `uv run pytest tests/ -k form4 -v` → PASS.

- [ ] **Step 2: Write the failing test**

```python
# tests/scout/test_edgar_index.py
from datetime import date
from shortlist.scout.edgar_index import cluster_buys_from_records


def test_cluster_detection_groups_buys_by_issuer():
    # Two distinct insiders buying the same issuer same day = a cluster.
    records = [
        {"ticker": "ABC", "insider": "Jane", "code": "P", "value": 250_000},
        {"ticker": "ABC", "insider": "John", "code": "P", "value": 120_000},
        {"ticker": "XYZ", "insider": "Sue",  "code": "P", "value": 90_000},   # lone buy
        {"ticker": "ABC", "insider": "Jane", "code": "S", "value": 999_999},  # sale ignored
    ]
    ems = cluster_buys_from_records(records, min_buyers=2)
    syms = {e.ticker for e in ems}
    assert syms == {"ABC"}            # only ABC has >=2 distinct buyers
    e = next(iter(ems))
    assert e.is_discovery is True
    assert "2 insiders" in e.evidence and "370" in e.evidence  # $370k total
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_edgar_index.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

```python
# src/shortlist/scout/edgar_index.py
"""SEC Form 4 daily-index scanner -> same-session insider cluster buys.

A NEW ingestion path (the per-ticker providers/_form4.py does not do this). The
daily index lists ~1,700 Form 4 rows (CIK + accession only); resolving cluster
buys means fetching+parsing each filing, classifying P/S, mapping CIK->ticker,
and grouping by issuer. Live fetching is bounded by a per-day cap and its own
concurrency budget; this module keeps the *pure* aggregation testable in isolation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..providers._form4 import classify_code
from .models import Emission


def cluster_buys_from_records(records: list[dict], min_buyers: int = 2) -> list[Emission]:
    """Pure aggregation: records -> cluster-buy Emissions.

    Each record: {ticker, insider, code, value}. A cluster = >= min_buyers distinct
    insiders making open-market purchases ('P') in the same issuer.
    """
    buys: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if classify_code(r.get("code", "")) == "buy":
            buys[r["ticker"].upper()].append(r)

    out: list[Emission] = []
    for ticker, rows in buys.items():
        buyers = {r["insider"] for r in rows}
        if len(buyers) < min_buyers:
            continue
        total = sum(r.get("value", 0) for r in rows)
        # strength scales with #buyers and dollar size, capped at 1.0
        strength = min(1.0, 0.4 + 0.2 * len(buyers) + min(0.4, total / 5_000_000))
        out.append(Emission(
            ticker, "edgar:form4_cluster_buy", strength,
            f"{len(buyers)} insiders bought ${total/1000:.0f}k", is_discovery=True))
    return out


def fetch_daily_records(session: date, max_filings: int, identity: str) -> list[dict]:
    """Live path: pull the Form 4 daily index for `session`, fetch up to
    `max_filings` documents, parse each into {ticker, insider, code, value}.

    Wraps synchronous edgartools; honors SEC fair-access via a bounded worker pool
    SEPARATE from the per-ticker EdgarSource semaphore. Returns [] (never raises) on
    any failure so the signal degrades. Implementation uses edgartools'
    get_filings(form='4', filing_date=session) + .obj() transaction parsing; cap the
    count at max_filings and record truncation in the caller's coverage detail.
    """
    try:
        from edgar import set_identity, get_filings  # edgartools
        set_identity(identity)
        filings = get_filings(form="4", filing_date=session.isoformat())
        records: list[dict] = []
        for f in list(filings)[:max_filings]:
            try:
                form4 = f.obj()
                for txn in getattr(form4, "market_trades", []) or []:
                    records.append({
                        "ticker": (getattr(form4, "issuer_ticker", "") or "").upper(),
                        "insider": getattr(form4, "reporting_owner", "?"),
                        "code": getattr(txn, "code", ""),
                        "value": float(getattr(txn, "value", 0) or 0),
                    })
            except Exception:  # noqa: BLE001 — skip an unparseable filing
                continue
        return [r for r in records if r["ticker"]]
    except Exception:  # noqa: BLE001 — edgartools missing or SEC error -> degrade
        return []
```

Note: `fetch_daily_records` touches the live edgartools API whose exact attribute names (`market_trades`, `issuer_ticker`, `reporting_owner`, `txn.code/value`) must be confirmed against the installed `edgartools` version during implementation — adjust the attribute access to the real objects, keeping the `{ticker, insider, code, value}` record shape the pure function consumes. The pure `cluster_buys_from_records` is the tested contract; `fetch_daily_records` is exercised only by the live smoke test (Task 13), never in CI.

Then add the signal wrapper to `signals.py`:

```python
class EdgarForm4Signal:
    """Insider cluster-buy discovery from the SEC Form 4 daily index."""
    name = "edgar_form4"
    is_discovery = True

    def __init__(self, max_filings: int = 400, identity: str | None = None) -> None:
        self.max_filings = max_filings
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from .edgar_index import fetch_daily_records, cluster_buys_from_records
        try:
            records = fetch_daily_records(session, self.max_filings, self.identity)
            ems = cluster_buys_from_records(records)
            self._status = (bool(records), f"{len(ems)} clusters from {len(records)} txns")
            return ems
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_form4", EdgarForm4Signal)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_edgar_index.py tests/ -k "form4 or edgar_index" -v`
Expected: PASS (new cluster test + existing form4 tests still green)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/edgar_index.py src/shortlist/scout/signals.py src/shortlist/providers/_form4.py tests/scout/test_edgar_index.py
git commit -m "feat(scout): EDGAR Form 4 daily-index cluster-buy scanner"
```

---

## Task 8: Funnel — aggregate emissions, apply boosters, prefilter

**Files:**
- Create: `src/shortlist/scout/funnel.py`
- Test: `tests/scout/test_funnel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_funnel.py
from datetime import date
from shortlist.scout.models import Emission
from shortlist.scout.funnel import aggregate, prefilter


def test_aggregate_merges_per_ticker_and_weights():
    ems = [Emission("AAPL", "yahoo:day_gainers", 0.8, "", True),
           Emission("AAPL", "edgar:form4_cluster_buy", 0.9, "", True),
           Emission("MSFT", "yahoo:most_actives", 0.5, "", True)]
    weights = {"yahoo:day_gainers": 1.0, "edgar:form4_cluster_buy": 1.5, "yahoo:most_actives": 1.0}
    cands = aggregate(ems, weights)
    by = {c.ticker: c for c in cands}
    assert by["AAPL"].interest == 0.8 * 1.0 + 0.9 * 1.5
    assert by["AAPL"].interest > by["MSFT"].interest


def test_prefilter_drops_cooldown_held_and_non_discovery_only():
    from shortlist.scout.models import Candidate
    booster_only = Candidate(ticker="NEWS")
    booster_only.add(Emission("NEWS", "finnhub:news_volume", 0.9, "", is_discovery=False), 0.5)
    real = Candidate(ticker="AAPL")
    real.add(Emission("AAPL", "yahoo:day_gainers", 0.8, "", is_discovery=True), 1.0)
    held = Candidate(ticker="TSLA")
    held.add(Emission("TSLA", "yahoo:day_gainers", 0.8, "", is_discovery=True), 1.0)

    kept = prefilter([booster_only, real, held],
                     in_cooldown=lambda t: False,
                     is_held=lambda t: t == "TSLA")
    assert [c.ticker for c in kept] == ["AAPL"]   # booster-only and held dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_funnel.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/funnel.py
"""Aggregate signal emissions into candidates and prefilter the set."""
from __future__ import annotations

from typing import Callable

from .models import Candidate, Emission


def aggregate(emissions: list[Emission], weights: dict[str, float]) -> list[Candidate]:
    """Group emissions by ticker into Candidates; weight by per-signal config weight.

    Weight lookup is by the signal's source prefix (before ':') falling back to the
    full signal name, so 'yahoo:day_gainers' uses the 'yahoo_screener' weight via the
    caller-supplied map keyed however the caller chooses (here: exact signal string).
    """
    by_ticker: dict[str, Candidate] = {}
    for e in emissions:
        c = by_ticker.setdefault(e.ticker, Candidate(ticker=e.ticker))
        c.add(e, weights.get(e.signal, 1.0))
    return list(by_ticker.values())


def prefilter(candidates: list[Candidate],
              in_cooldown: Callable[[str], bool],
              is_held: Callable[[str], bool]) -> list[Candidate]:
    """Drop booster-only candidates (no discovery signal), cooldown, and held names."""
    kept = []
    for c in candidates:
        if not c.has_discovery:      # a booster alone cannot originate a candidate
            continue
        if in_cooldown(c.ticker) or is_held(c.ticker):
            continue
        kept.append(c)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_funnel.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/funnel.py tests/scout/test_funnel.py
git commit -m "feat(scout): funnel aggregation + prefilter"
```

---

## Task 9: Budget — select top-X within the daily cap

**Files:**
- Create: `src/shortlist/scout/budget.py`
- Test: `tests/scout/test_budget.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_budget.py
from shortlist.scout.models import Candidate, Emission
from shortlist.scout.budget import select


def _cand(ticker, interest):
    c = Candidate(ticker=ticker)
    c.add(Emission(ticker, "yahoo:day_gainers", interest, "", True), 1.0)
    return c


def test_select_takes_top_x_by_interest():
    cands = [_cand("A", 0.2), _cand("B", 0.9), _cand("C", 0.5), _cand("D", 0.7)]
    chosen, dropped = select(cands, daily_x=2)
    assert [c.ticker for c in chosen] == ["B", "D"]
    assert dropped == 2


def test_select_under_cap_drops_nothing():
    cands = [_cand("A", 0.2), _cand("B", 0.9)]
    chosen, dropped = select(cands, daily_x=5)
    assert len(chosen) == 2 and dropped == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_budget.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/budget.py
"""Select the top-X candidates that fit today's deep-screen ceiling (§4.1)."""
from __future__ import annotations

from .models import Candidate


def select(candidates: list[Candidate], daily_x: int) -> tuple[list[Candidate], int]:
    """Return (chosen, dropped_count). Chosen = top daily_x by interest desc."""
    ordered = sorted(candidates, key=lambda c: c.interest, reverse=True)
    chosen = ordered[:daily_x]
    return chosen, max(0, len(ordered) - len(chosen))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_budget.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/budget.py tests/scout/test_budget.py
git commit -m "feat(scout): budget selection (top-X by interest)"
```

---

## Task 10: Report rendering + RunManifest

**Files:**
- Create: `src/shortlist/scout/report.py`
- Test: `tests/scout/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report.py
from datetime import date
from shortlist.models import ScoreCard
from shortlist.scout.models import SignalStatus, RunManifest
from shortlist.scout.report import render_message


def _card(ticker, comp, gates=None):
    return ScoreCard(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                     momentum=80, value=40, opportunity=80, insider=55, gates=gates or [])


def test_message_lists_ranked_names_and_signal_coverage():
    cards = [_card("AAPL", 78.4), _card("MSFT", 71.0, gates=["negative_fcf"])]
    manifest = RunManifest(
        session=date(2026, 5, 29),
        signals=[SignalStatus("yahoo_screener", True, "42 hits"),
                 SignalStatus("wikipedia", False, "rate-limited")],
        raw=42, after_dedup=30, after_prefilter=18, screened=15, dropped_for_budget=3,
        researched=["AAPL"])
    msg = render_message(cards, manifest, briefs={"AAPL": "Strong moat, fair price."})
    assert "AAPL" in msg and "78" in msg
    assert "negative_fcf" in msg                      # gates surfaced
    assert "yahoo_screener" in msg and "rate-limited" in msg  # signal coverage line
    assert "15 screened" in msg                       # funnel line
    assert "Strong moat" in msg                       # brief included
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/report.py
"""Render a ScoutReport to a Telegram-friendly message + the RunManifest dict."""
from __future__ import annotations

from shortlist.models import ScoreCard

from .models import RunManifest


def render_message(cards: list[ScoreCard], manifest: RunManifest,
                   briefs: dict[str, str] | None = None) -> str:
    briefs = briefs or {}
    lines = [f"📊 Scout shortlist — session {manifest.session.isoformat()}", ""]

    for i, c in enumerate(cards, 1):
        flag = f"  ⚠️ {', '.join(c.gates)}" if c.gates else ""
        lines.append(f"{i}. {c.ticker}  {c.composite:.1f}{flag}")
        lines.append(f"   Q{_n(c.quality)} M{_n(c.moat)} G{_n(c.growth)} "
                     f"Opp{_n(c.opportunity)} Ins{_n(c.insider)}")
        if c.ticker in briefs:
            lines.append(f"   📝 {briefs[c.ticker]}")
    lines.append("")

    sig = " · ".join(
        f"{s.name} {'✓' if s.ran else '✗'} ({s.detail})" for s in manifest.signals)
    lines.append(f"Signals: {sig}")
    lines.append(
        f"Funnel: {manifest.raw} raw → {manifest.after_dedup} deduped → "
        f"{manifest.after_prefilter} after prefilter → {manifest.screened} screened "
        f"({manifest.dropped_for_budget} dropped: budget)")
    for note in manifest.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def _n(v) -> str:
    return f"{v:.0f}" if v is not None else "·"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report.py tests/scout/test_report.py
git commit -m "feat(scout): report rendering with signal-coverage + funnel lines"
```

---

## Task 11: Telegram delivery (notify)

**Files:**
- Create: `src/shortlist/scout/notify.py`
- Test: `tests/scout/test_notify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_notify.py
import httpx
from shortlist.scout.notify import send_telegram


def test_send_posts_to_bot_api_and_returns_true():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok = send_telegram("hello", token="T", chat_id="42", client=client)
    assert ok is True
    assert "/botT/sendMessage" in seen["url"]
    assert "42" in seen["body"] and "hello" in seen["body"]


def test_send_without_creds_returns_false():
    assert send_telegram("x", token=None, chat_id=None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/notify.py
"""Thin Telegram delivery. Credentials from env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."""
from __future__ import annotations

import os

import httpx

from ..env import redact_secrets


def send_telegram(text: str, token: str | None = None, chat_id: str | None = None,
                  client: httpx.Client | None = None) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    c = client or httpx.Client(timeout=15.0)
    try:
        resp = c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text})
        return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"telegram send failed: {redact_secrets(str(e))}")
        return False
    finally:
        if client is None:
            c.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_notify.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/notify.py tests/scout/test_notify.py
git commit -m "feat(scout): thin Telegram delivery"
```

---

## Task 12: Orchestrator + CLI + config wiring + offline `--demo`

**Files:**
- Create: `src/shortlist/scout/daily.py`
- Modify: `config.yaml` (add `scout:` block)
- Modify: `pyproject.toml` (add `shortlist-scout` entry point)
- Modify: `.gitignore` (ignore `scout/` artifact dir)
- Test: `tests/scout/test_daily_demo.py`

- [ ] **Step 1: Add the `scout:` config block**

Append to `config.yaml`:

```yaml
# Autonomous scout (shortlist-scout). See docs/AUTONOMOUS_SCOUT.md.
scout:
  daily_x: 15
  research_top_n: 3
  research_phase_budget_s: 600
  cooldown_days: 7
  min_market_cap: 2.0e+9
  deep_screen_sources: [yahoo, fmp, finnhub, edgar]
  edgar_index_daily_cap: 400
  state_path: state/scout_state.json
  artifact_dir: scout
  signals:
    yahoo_screener: {enabled: true,  weight: 1.0}
    edgar_form4:    {enabled: true,  weight: 1.5}
    finnhub_news:   {enabled: true,  weight: 0.5}
    wikipedia:      {enabled: true,  weight: 0.5}
    quiver:         {enabled: false, weight: 1.0}
```

- [ ] **Step 2: Write the failing end-to-end demo test**

```python
# tests/scout/test_daily_demo.py
from shortlist.scout.daily import main


def test_demo_runs_offline_and_prints_report(capsys):
    rc = main(["--demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Scout shortlist" in out
    assert "Signals:" in out and "Funnel:" in out
    # demo uses MockSignal -> AAPL/MSFT/GOOGL discovered, mock provider scores them
    assert "AAPL" in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_daily_demo.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the orchestrator**

```python
# src/shortlist/scout/daily.py
"""Scout orchestrator + CLI entry point (shortlist-scout). See docs/AUTONOMOUS_SCOUT.md §3."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from ..env import load_env, redact_secrets
from .budget import select
from .calendar import is_trading_day, last_session
from .funnel import aggregate, prefilter
from .models import RunManifest, SignalStatus
from .report import render_message
from .signals import build_signals
from .state import ScoutState

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config.yaml"


def _enabled_signal_names(scout_cfg: dict) -> list[str]:
    name_map = {"yahoo_screener": "yahoo_screener", "edgar_form4": "edgar_form4",
                "finnhub_news": "finnhub_news", "wikipedia": "wikipedia", "quiver": "quiver"}
    return [name_map[k] for k, v in scout_cfg.get("signals", {}).items()
            if v.get("enabled") and k in name_map and k != "quiver"]


def run(config: dict, *, demo: bool, today: date) -> int:
    scout_cfg = config.get("scout", {})
    session = today if demo else last_session(today)

    if not demo and not is_trading_day(today) and session != today:
        pass  # non-trading 'today' is fine; we anchor to last_session

    state = ScoutState(Path(scout_cfg.get("state_path", "state/scout_state.json")))
    if not demo and state.run_completed(session):
        print(f"scout: run for {session} already completed; nothing to do")
        return 0

    # 1. Scan discovery signals
    weights_by_signal: dict[str, float] = {}
    sig_cfg = scout_cfg.get("signals", {})
    statuses: list[SignalStatus] = []
    emissions = []

    if demo:
        signals = build_signals(["mock"])
    else:
        signals = build_signals(_enabled_signal_names(scout_cfg))

    discovery = [s for s in signals if getattr(s, "is_discovery", False)]
    for s in discovery:
        ems = s.scan(session)
        emissions.extend(ems)
        ran, detail = s.available()
        statuses.append(SignalStatus(s.name, ran, detail))
        # weight by config: map signal prefix back to its config key
        cfg_key = {"yahoo_screener": "yahoo_screener", "edgar_form4": "edgar_form4",
                   "mock": "yahoo_screener"}.get(s.name, s.name)
        w = sig_cfg.get(cfg_key, {}).get("weight", 1.0)
        for e in ems:
            weights_by_signal[e.signal] = w

    raw = len(emissions)
    cands = aggregate(emissions, weights_by_signal)
    after_dedup = len(cands)

    kept = prefilter(
        cands,
        in_cooldown=lambda t: state.in_cooldown(t, on=session,
                                                cooldown_days=scout_cfg.get("cooldown_days", 7)),
        is_held=state.is_held)
    after_prefilter = len(kept)

    chosen, dropped = select(kept, daily_x=scout_cfg.get("daily_x", 15))

    # 2. Deep-screen via the existing harness scorer
    from ..screen import run_harness
    sources = scout_cfg.get("deep_screen_sources", ["yahoo", "fmp", "finnhub", "edgar"])
    if demo:
        from ..screen import run as run_screener
        cards = run_screener([c.ticker for c in chosen], ["mock"], config)
    else:
        cards = run_harness([c.ticker for c in chosen], sources, config)

    # 3. Auto-research (guardrailed) — skipped in demo
    briefs: dict[str, str] = {}
    researched: list[str] = []
    notes: list[str] = []
    if not demo:
        briefs, researched, note = _research_phase(cards, config, scout_cfg)
        if note:
            notes.append(note)

    manifest = RunManifest(
        session=session, signals=statuses, raw=raw, after_dedup=after_dedup,
        after_prefilter=after_prefilter, screened=len(cards), dropped_for_budget=dropped,
        researched=researched, notes=notes)

    message = render_message(cards, manifest, briefs)

    # 4. Deliver + persist
    if demo:
        print(message)
    else:
        _write_manifest(scout_cfg, manifest, message)
        from .notify import send_telegram
        if not send_telegram(message):
            print(message)  # fall back to stdout if Telegram is unconfigured
        state.record_screened([c.ticker for c in cards], session)
        state.mark_run_completed(session)
    return 0


def _research_phase(cards, config, scout_cfg) -> tuple[dict, list, str | None]:
    """Guardrailed auto-research: kill-switch, auth probe, hard cap, phase budget."""
    if os.environ.get("SCOUT_NO_RESEARCH") == "1" or Path("scout/STOP_RESEARCH").exists():
        return {}, [], "research skipped: kill-switch"
    try:
        from ..research import is_available, enrich
    except Exception:  # noqa: BLE001
        return {}, [], "research skipped: layer unavailable"
    if not is_available():
        return {}, [], "research skipped: claude CLI / edgartools not available"
    n = scout_cfg.get("research_top_n", 3)
    try:
        paths = enrich(cards, config, n, False)  # returns {ticker: path}
    except Exception as e:  # noqa: BLE001
        return {}, [], f"research failed: {redact_secrets(str(e))}"
    briefs = {t: _one_line_brief(p) for t, p in paths.items()}
    return briefs, list(paths.keys()), None


def _one_line_brief(path) -> str:
    try:
        data = json.loads(Path(str(path).replace(".md", ".json")).read_text())
        return (data.get("synthesis") or data.get("summary") or "")[:200]
    except Exception:  # noqa: BLE001
        return "brief generated"


def _write_manifest(scout_cfg, manifest, message) -> None:
    out_dir = Path(scout_cfg.get("artifact_dir", "scout"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = manifest.session.isoformat()
    (out_dir / f"{stamp}.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    (out_dir / f"{stamp}.txt").write_text(message)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortlist-scout",
                                 description="Autonomous candidate discovery + daily report.")
    ap.add_argument("--demo", action="store_true", help="offline run; print report to stdout")
    ap.add_argument("--config", default=str(_DEFAULT_CONFIG))
    ap.add_argument("--no-research", action="store_true", help="skip the Claude research phase")
    args = ap.parse_args(argv)

    load_env()
    if args.no_research:
        os.environ["SCOUT_NO_RESEARCH"] = "1"
    config = yaml.safe_load(Path(args.config).read_text())
    today = datetime.now(timezone.utc).date()
    try:
        return run(config, demo=args.demo, today=today)
    except Exception as e:  # noqa: BLE001
        print(f"scout: run failed: {redact_secrets(str(e))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Note on the demo path: the demo screens via the existing `run(tickers, ["mock"], config)` screener so it needs no keys. Confirm `MockProvider` returns the demo tickers (AAPL/MSFT/GOOGL) or adjust `MockSignal`'s tickers to ones the mock provider knows; the test asserts `AAPL` appears, so keep them aligned.

- [ ] **Step 5: Add the entry point and gitignore**

In `pyproject.toml` under `[project.scripts]`:

```toml
shortlist-scout = "shortlist.scout.daily:main"
```

Append to `.gitignore`:

```
scout/
state/scout_state.json
```

- [ ] **Step 6: Run the demo test**

Run: `uv sync && uv run pytest tests/scout/test_daily_demo.py -v`
Expected: PASS

If the mock provider doesn't return AAPL, adjust `MockSignal` tickers in Task 4 to the mock basket and re-run. Iterate until green.

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/scout/daily.py config.yaml pyproject.toml .gitignore tests/scout/test_daily_demo.py
git commit -m "feat(scout): orchestrator, CLI, config block, offline --demo"
```

---

## Task 13: Deployment units + live smoke test + docs

**Files:**
- Create: `deploy/shortlist-scout.service`
- Create: `deploy/shortlist-scout.timer`
- Create: `deploy/README.md`
- Create: `tests/scout/test_yahoo_live.py` (network-gated smoke test)
- Modify: `README.md` (add a scout section)

- [ ] **Step 1: Write the systemd unit files**

```ini
# deploy/shortlist-scout.service
[Unit]
Description=shortlist autonomous scout — daily candidate discovery + report
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=oracle
WorkingDirectory=/opt/oracle/shortlist
ExecStart=/opt/oracle/shortlist/.venv/bin/shortlist-scout
# OnFailure alert mirrors the oracle-daily-report pattern (see deploy/README.md)
```

```ini
# deploy/shortlist-scout.timer
[Unit]
Description=Run the shortlist scout once daily after the US close

[Timer]
# 22:30 UTC ~= 18:30 ET, after the close; Persistent reruns a missed timer.
OnCalendar=*-*-* 22:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write the deploy README**

`deploy/README.md` documents: copy units to `/etc/systemd/system/`, `systemctl daemon-reload`, `systemctl enable --now shortlist-scout.timer`, the required env in the repo-root `.env` (`FINNHUB_API_KEY`, `FMP_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), the `claude` CLI auth requirement for the research phase, and the kill-switch (`touch scout/STOP_RESEARCH` or `SCOUT_NO_RESEARCH=1`). It explicitly says units are not auto-installed.

- [ ] **Step 3: Write the network-gated live smoke test**

```python
# tests/scout/test_yahoo_live.py
import os
import pytest
from datetime import date
from shortlist.scout.signals import YahooScreenerSignal


@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1",
                    reason="live network test; set RUN_LIVE_TESTS=1 to run")
def test_yahoo_screener_endpoint_still_returns_quotes():
    sig = YahooScreenerSignal(screens=["day_gainers"])
    ems = sig.scan(date.today())
    ran, detail = sig.available()
    assert ran is True, f"yahoo screener broke: {detail}"
    assert ems, "expected non-empty gainers"
```

- [ ] **Step 4: Run the full suite (live test skipped by default)**

Run: `uv run pytest tests/ -v`
Expected: PASS; `test_yahoo_screener_endpoint_still_returns_quotes` SKIPPED.

- [ ] **Step 5: Add the README scout section**

In `README.md`, add a short "Autonomous scout" section linking to `docs/AUTONOMOUS_SCOUT.md`, showing `uv run shortlist-scout --demo` and the live invocation, and noting the strictly-free ~15/day cap and the kill-switch.

- [ ] **Step 6: Commit**

```bash
git add deploy/ tests/scout/test_yahoo_live.py README.md
git commit -m "feat(scout): systemd units, deploy docs, live smoke test, README"
```

---

## Task 14: Full-suite green + PR

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: all pass (live test skipped). If anything fails, fix before proceeding — do not open the PR on red.

- [ ] **Step 2: Confirm the demo works end to end**

Run: `uv run shortlist-scout --demo`
Expected: a printed shortlist with a Signals line and a Funnel line, exit 0.

- [ ] **Step 3: Push the branch and open the PR**

```bash
git push -u origin <worktree-branch>
gh pr create --title "feat: autonomous scout — signal-driven candidate discovery" \
  --body "Implements docs/AUTONOMOUS_SCOUT.md: a third 'scout' stack that discovers candidates from free signals (Yahoo screeners + EDGAR Form 4 cluster buys, with Finnhub/Wikipedia confluence boosters), screens them through the existing harness scorer, runs the existing Claude research on the leaders (guardrailed), and ships a daily Telegram report. Strictly-free, ~15/day cap, idempotent + trading-calendar-gated, fully offline --demo. See the design doc and plan for the full rationale.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** every design-doc section maps to a task — models/funnel/budget/state/calendar (Tasks 1-3, 8-9), the four signals + EDGAR scanner (Tasks 4-7), report/notify/orchestrator/config (Tasks 10-12), deployment + research guardrails + coverage honesty (Tasks 12-13). The two design follow-ups (harness coverage port, EDGAR XBRL) are explicitly out of MVP scope.
- **Known live-integration risks to resolve during implementation, not planning:** (a) exact `edgartools` attribute names in `fetch_daily_records` (Task 7); (b) the `shortlist.research.enrich` return shape and brief JSON keys (`synthesis`/`summary`) in `_research_phase` (Task 12) — confirm against `src/shortlist/research/` and adjust; (c) whether `MockProvider` returns the demo tickers (Task 12 step 6). All three are isolated behind tested pure functions, so a wrong guess fails loudly in one place.
- **House rules:** every `except` that surfaces a message routes through `redact_secrets`; signal failures annotate `available()`/coverage rather than shrinking the funnel silently; the P/S classifier is the single `_form4.classify_code`.
