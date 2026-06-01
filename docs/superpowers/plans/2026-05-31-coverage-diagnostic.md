# Coverage Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-card `coverage` diagnostic — machine-readable in `--json` and human-readable on stderr — that explains *why* sub-scores are null (chiefly FMP `402`/empty-`200` symbol gating) instead of leaving the cause inferred from absence.

**Architecture:** A pure-function module (`coverage.py`) classifies each provider's per-ticker fetch outcome and assembles a `Coverage` record from the captured outcomes plus the existing `metrics.sources` audit trail. Structured fields carry only facts; an interpretive `note` carries causation. No hand-maintained `field→provider→subscore` map. `screen.run()` captures outcomes and attaches `Coverage` to each `ScoreCard`; `_card_dict()` emits it in JSON and `main()` prints a stderr summary.

**Tech Stack:** Python 3.13, dataclasses, pytest, `uv run`. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-31-coverage-diagnostic-design.md`

---

## File structure

- **Modify** `src/shortlist/models.py` — add `Coverage` dataclass; add `coverage: Optional[Coverage] = None` to `ScoreCard`.
- **Create** `src/shortlist/coverage.py` — `classify_failure`, `build_coverage`, `coverage_note_line` (pure functions).
- **Modify** `src/shortlist/screen.py` — capture per-ticker provider outcomes in `run()`, attach coverage; emit in `_card_dict()`; print stderr block in `main()`.
- **Create** `tests/test_coverage.py` — unit tests for all three functions, `_card_dict` emission, and the `run()` leak-guard.
- **Modify** `CLAUDE.md` and `.claude/skills/run/SKILL.md` — document the new `coverage` field (final task).

**Known limitation (by design, matches existing behavior):** if *every* provider fails for a ticker, `run()` already does `if not per_provider: continue` and drops the ticker entirely — so a fully-dropped ticker has no card and therefore no coverage. Out of scope.

---

## Task 1: `Coverage` dataclass + `ScoreCard.coverage` field

**Files:**
- Modify: `src/shortlist/models.py` (add `Coverage` before `ScoreCard` at line 70; add field after `ScoreCard.metrics` at line 81)
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_coverage.py`:

```python
from __future__ import annotations

from shortlist.models import Coverage, ScoreCard, StockMetrics


def _card(**over):
    base = dict(
        ticker="T", composite=1.0, quality=None, moat=None, momentum=None,
        value=None, opportunity=None, insider=None, metrics=StockMetrics(ticker="T"),
    )
    base.update(over)
    return ScoreCard(**base)


def test_coverage_dataclass_holds_fields():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"], note="x")
    assert cov.providers == {"fmp": "gated_402"}
    assert cov.unavailable == ["value"]
    assert cov.note == "x"


def test_scorecard_coverage_defaults_to_none():
    assert _card().coverage is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: FAIL — `ImportError: cannot import name 'Coverage'`.

- [ ] **Step 3: Write minimal implementation**

In `src/shortlist/models.py`, add the `Coverage` dataclass immediately before `@dataclass class ScoreCard` (line 70):

```python
@dataclass
class Coverage:
    """Why a ticker's data is thin. `providers` maps provider name -> status
    ("ok" | "gated_402" | "empty" | "error"); `unavailable` lists output fields
    that came out null (fact); `note` is interpretive prose for recognized
    patterns (e.g. FMP symbol gating). See coverage.py for assembly."""
    providers: dict
    unavailable: list
    note: Optional[str] = None
```

In `ScoreCard`, add the field right after `metrics: Optional[StockMetrics] = None` (line 81):

```python
    coverage: Optional["Coverage"] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py tests/test_coverage.py
git commit -m "feat: add Coverage dataclass and ScoreCard.coverage field"
```

---

## Task 2: `classify_failure`

**Files:**
- Create: `src/shortlist/coverage.py`
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coverage.py`:

```python
import requests

from shortlist.coverage import classify_failure


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


def test_classify_failure_402_is_gated():
    assert classify_failure(_http_error(402)) == "gated_402"


def test_classify_failure_other_http_is_error():
    assert classify_failure(_http_error(500)) == "error"


def test_classify_failure_non_http_is_error():
    assert classify_failure(RuntimeError("boom")) == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -k classify_failure -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.coverage'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shortlist/coverage.py`:

```python
from __future__ import annotations

from typing import Optional

from .models import Coverage, ScoreCard


def classify_failure(exc: Exception) -> str:
    """Map a provider fetch exception to a status. HTTP 402 (FMP paid/gated
    symbol) -> "gated_402"; anything else -> "error". Detection is by status
    code (not string parsing) and needs no `requests` import: requests.HTTPError
    exposes `.response.status_code`, and other exceptions simply lack it."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return "gated_402" if status == 402 else "error"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coverage.py -k classify_failure -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/coverage.py tests/test_coverage.py
git commit -m "feat: add classify_failure (402 -> gated_402)"
```

---

## Task 3: `build_coverage`

**Files:**
- Modify: `src/shortlist/coverage.py`
- Test: `tests/test_coverage.py`

Logic: copy `outcomes`; using `card.metrics.sources` (field -> provider), reclassify any `"ok"` provider that contributed **zero** fields to `"empty"`; return `None` if every provider is `"ok"`; else build `unavailable` (null card sub-scores + null `upside_to_target`, guarding `metrics is None`) and a `note` (FMP-specific when `"fmp"` is flagged — that string is **load-bearing**, coupled to `fmp.py: name = "fmp"`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coverage.py`:

```python
from shortlist.coverage import build_coverage


def test_build_coverage_gated_fmp_lists_value_and_note():
    m = StockMetrics(ticker="SCHW")
    m.sources = {"price": "finnhub", "insider_net_6m": "edgar"}  # no fmp fields
    card = _card(ticker="SCHW", composite=43.2, quality=45.4, moat=50.0,
                 momentum=57.1, value=None, opportunity=57.1, insider=10.8, metrics=m)
    cov = build_coverage({"fmp": "gated_402", "finnhub": "ok", "edgar": "ok"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "gated_402"
    assert "value" in cov.unavailable
    assert "upside_to_target" in cov.unavailable  # price set but no target_median
    assert "Starter" in cov.note


def test_build_coverage_reclassifies_ok_but_empty_provider():
    m = StockMetrics(ticker="X")
    m.sources = {"price": "finnhub"}  # fmp contributed nothing despite not raising
    card = _card(ticker="X", metrics=m)
    cov = build_coverage({"fmp": "ok", "finnhub": "ok"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "empty"


def test_build_coverage_all_ok_returns_none():
    m = StockMetrics(ticker="X")
    m.sources = {"roe": "fmp", "price": "finnhub"}  # both contributed
    card = _card(ticker="X", quality=80.0, metrics=m)
    assert build_coverage({"fmp": "ok", "finnhub": "ok"}, card) is None


def test_build_coverage_handles_none_metrics():
    card = _card(ticker="X", metrics=None)
    cov = build_coverage({"fmp": "gated_402"}, card)  # must not raise
    assert "upside_to_target" in cov.unavailable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -k build_coverage -v`
Expected: FAIL — `ImportError: cannot import name 'build_coverage'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/shortlist/coverage.py`:

```python
_SUBSCORE_FIELDS = ("quality", "moat", "momentum", "value", "insider")

_FMP_NOTE = (
    "FMP gated this symbol (402); value axis (PE-vs-history, FCF yield, "
    "target upside) needs FMP Starter tier"
)


def build_coverage(outcomes: dict, card: ScoreCard) -> Optional[Coverage]:
    """Assemble a Coverage record, or None when every provider is "ok".

    `outcomes` maps provider name -> raise-time status ("ok" on success, else the
    classify_failure result). A provider that did not raise but contributed zero
    fields to the merged metrics (per `card.metrics.sources`) is reclassified
    "empty"."""
    providers = dict(outcomes)
    contributed = set(card.metrics.sources.values()) if card.metrics else set()
    for name, status in list(providers.items()):
        if status == "ok" and name not in contributed:
            providers[name] = "empty"

    if all(status == "ok" for status in providers.values()):
        return None

    unavailable = [f for f in _SUBSCORE_FIELDS if getattr(card, f) is None]
    upside = card.metrics.upside_to_target() if card.metrics else None
    if upside is None:
        unavailable.append("upside_to_target")

    return Coverage(providers=providers, unavailable=unavailable,
                    note=_build_note(providers))


def _build_note(providers: dict) -> Optional[str]:
    flagged = {n: s for n, s in providers.items() if s in ("gated_402", "empty")}
    if not flagged:
        return None
    # "fmp" is the registry name (fmp.py: name = "fmp") — load-bearing string.
    if providers.get("fmp") in ("gated_402", "empty"):
        return _FMP_NOTE
    return (f"{', '.join(sorted(flagged))}: provider supplied no data for this "
            "symbol (see stderr)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coverage.py -k build_coverage -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/coverage.py tests/test_coverage.py
git commit -m "feat: add build_coverage (factual fields + interpretive note)"
```

---

## Task 4: `coverage_note_line`

**Files:**
- Modify: `src/shortlist/coverage.py`
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coverage.py`:

```python
from shortlist.coverage import coverage_note_line


def test_coverage_note_line_renders_flagged_providers():
    cov = Coverage(providers={"fmp": "gated_402", "finnhub": "ok"},
                   unavailable=["value", "upside_to_target"], note="x")
    line = coverage_note_line("SCHW", cov)
    assert "SCHW" in line
    assert "fmp gated (402)" in line
    assert "value, upside_to_target" in line
    assert "finnhub" not in line  # ok providers are not listed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -k note_line -v`
Expected: FAIL — `ImportError: cannot import name 'coverage_note_line'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/shortlist/coverage.py`:

```python
_STATUS_LABEL = {"gated_402": "gated (402)", "empty": "empty"}


def coverage_note_line(ticker: str, cov: Coverage) -> str:
    """One-line stderr rendering, e.g.
    `  SCHW   fmp gated (402) -> value, upside_to_target unavailable`."""
    flagged = [f"{n} {_STATUS_LABEL[s]}"
               for n, s in sorted(cov.providers.items())
               if s in _STATUS_LABEL]
    unavail = ", ".join(cov.unavailable) or "—"
    return f"  {ticker:<6} {'; '.join(flagged)} -> {unavail} unavailable"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coverage.py -k note_line -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/coverage.py tests/test_coverage.py
git commit -m "feat: add coverage_note_line stderr renderer"
```

---

## Task 5: Wire into `screen.py` (run, _card_dict, main)

**Files:**
- Modify: `src/shortlist/screen.py` (imports; `run()` lines 17-39; `_card_dict()` lines 197-208; `main()` lines 116-147)
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coverage.py`:

```python
from pathlib import Path

import yaml

from shortlist import screen
from shortlist.models import StockMetrics as SM


class _Resp:
    def __init__(self, status): self.status_code = status


class _Http402(Exception):
    def __init__(self): self.response = _Resp(402)


class _FakeFMP:
    name = "fmp"
    def fetch(self, t):
        if t == "GATED":
            raise _Http402()
        m = SM(ticker=t)
        m.market_cap = 1.0e10
        m.sources["market_cap"] = "fmp"
        return m


class _FakeFinnhub:
    name = "finnhub"
    def fetch(self, t):
        m = SM(ticker=t)
        m.market_cap = 2.0e10
        m.roe = 0.2
        m.sources["market_cap"] = "finnhub"
        m.sources["roe"] = "finnhub"
        return m


def _config():
    path = Path(__file__).resolve().parents[1] / "config.yaml"
    return yaml.safe_load(path.read_text())


def test_run_attaches_coverage_and_does_not_leak(monkeypatch):
    monkeypatch.setattr(screen, "build_providers",
                        lambda names: [_FakeFMP(), _FakeFinnhub()])
    cards = screen.run(["GATED", "OK"], ["dummy"], _config())
    by = {c.ticker: c for c in cards}
    # GATED: fmp raised 402 but finnhub succeeded -> card exists with coverage
    assert by["GATED"].coverage is not None
    assert by["GATED"].coverage.providers["fmp"] == "gated_402"
    # OK: both providers contributed -> no coverage; outcomes did NOT leak
    assert by["OK"].coverage is None


def test_card_dict_emits_coverage_when_present():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"], note="x")
    d = screen._card_dict(_card(coverage=cov))
    assert d["coverage"]["providers"]["fmp"] == "gated_402"
    assert d["coverage"]["unavailable"] == ["value"]
    assert d["coverage"]["note"] == "x"


def test_card_dict_omits_coverage_when_absent():
    assert "coverage" not in screen._card_dict(_card())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_coverage.py -k "run_attaches or card_dict" -v`
Expected: FAIL — `run()` does not set `.coverage` (AttributeError or `coverage is None` for GATED), and `_card_dict` has no `coverage` key.

- [ ] **Step 3: Write minimal implementation**

In `src/shortlist/screen.py`, add the import near the other local imports (after line 13 `from .providers import build_providers`):

```python
from .coverage import build_coverage, classify_failure, coverage_note_line
```

Replace the `run()` per-ticker loop body (lines 27-39) so it captures outcomes and attaches coverage:

```python
    cards: list[ScoreCard] = []
    for t in tickers:
        per_provider = []
        outcomes: dict[str, str] = {}        # reset per ticker — must not leak
        for p in providers:
            try:
                per_provider.append(p.fetch(t))
                outcomes[p.name] = "ok"
            except Exception as e:  # one bad source shouldn't kill the run
                outcomes[p.name] = classify_failure(e)
                print(f"  ! {p.name} failed for {t}: {redact_secrets(e)}", file=sys.stderr)
        if not per_provider:
            continue
        card = score(merge(per_provider), config)
        card.coverage = build_coverage(outcomes, card)
        cards.append(card)
    cards.sort(key=lambda c: c.composite, reverse=True)
    return cards
```

In `_card_dict()` (lines 197-208), add the conditional emit just before `return d` (mirrors the `research_path` pattern):

```python
    if c.coverage is not None:
        cov = {"providers": c.coverage.providers, "unavailable": c.coverage.unavailable}
        if c.coverage.note:
            cov["note"] = c.coverage.note
        d["coverage"] = cov
```

Add a stderr printer near the other `_print_*` helpers (e.g. after `_print_plain`):

```python
def _print_coverage_notes(cards: list[ScoreCard]) -> None:
    flagged = [c for c in cards if c.coverage is not None]
    if not flagged:
        return
    print("\nCoverage notes", file=sys.stderr)
    for c in flagged:
        print(coverage_note_line(c.ticker, c.coverage), file=sys.stderr)
```

In `main()`, call it right after `cards = run(tickers, providers, config)` (line 134):

```python
    cards = run(tickers, providers, config)
    _print_coverage_notes(cards)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: PASS (all coverage tests green).

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run pytest`
Expected: PASS — previous 83 plus the new tests, no failures, output pristine.

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/screen.py tests/test_coverage.py
git commit -m "feat: capture provider outcomes and surface coverage in run/json/stderr"
```

---

## Task 6: Manual live verification

**Files:** none (verification only)

- [ ] **Step 1: Run the originally-failing basket and inspect coverage**

Run: `uv run shortlist --tickers AXON,MELI,ISRG,SCHW,TMO --provider fmp,finnhub,edgar --json`

Expected:
- **stdout** JSON: each card has a `"coverage"` block with `"providers": {"fmp": "gated_402", ...}`, `"unavailable"` containing `"value"` and `"upside_to_target"`, and a `"note"` mentioning FMP Starter tier.
- **stderr**: a `Coverage notes` block with one `fmp gated (402) -> ...` line per ticker.

- [ ] **Step 2: Confirm a covered name stays clean**

Run: `uv run shortlist --tickers AAPL --provider fmp,finnhub,edgar --json`
Expected: AAPL's card has **no** `"coverage"` key (all providers ok), and no `Coverage notes` block on stderr.

- [ ] **Step 3: Confirm `--json` stdout is still pure JSON**

Run: `uv run shortlist --tickers SCHW --provider fmp,finnhub,edgar --json 2>/dev/null | python -c "import sys, json; json.load(sys.stdin); print('valid json')"`
Expected: prints `valid json` (the stderr Coverage notes did not contaminate stdout).

---

## Task 7: Documentation

**Files:**
- Modify: `.claude/skills/run/SKILL.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the `/run` skill's "Coverage gaps" section**

In `.claude/skills/run/SKILL.md`, under `### Coverage gaps`, add after the existing "Null `value` + FMP `402`s" paragraph:

```markdown
The screener now emits this machine-readably: each affected card carries a `coverage` block (`providers` map with per-provider status — `ok`/`gated_402`/`empty`/`error` —, the `unavailable` output fields, and an interpretive `note`), and a `Coverage notes` summary prints to stderr. Read `coverage` directly instead of inferring the cause from a null `value`; a `gated_402`/`empty` status on `fmp` with a non-null `insider` is the FMP-gating signature.
```

- [ ] **Step 2: Update CLAUDE.md screener data-flow section**

In `CLAUDE.md`, under `## Screener data flow`, add a bullet after the `scoring.score(...)` step:

```markdown
A `coverage` diagnostic (`coverage.py`) annotates each `ScoreCard`: per-provider
fetch status (`ok`/`gated_402`/`empty`/`error`, the latter two derived from the
fetch exception and the `metrics.sources` audit trail), the null output fields,
and an interpretive note. It surfaces in `--json` (a `coverage` block, emitted
only when a provider had trouble) and as a stderr `Coverage notes` summary — so a
null `value` reads as "FMP gated this symbol," not an unexplained gap.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md .claude/skills/run/SKILL.md
git commit -m "docs: document the coverage diagnostic in CLAUDE.md and /run skill"
```

---

## Self-review notes (author)

- **Spec coverage:** data model (Task 1), `classify_failure`/`build_coverage`/`coverage_note_line` (Tasks 2-4), `run`/`_card_dict`/`main` wiring (Task 5), both gating modes — `gated_402` via exception, `empty` via `metrics.sources` (Task 3), stderr + JSON surfaces (Task 5), live verification of both modes and clean-stdout (Task 6), docs follow-up (Task 7). All spec sections mapped.
- **Must-fix items from spec review:** heterogeneous `upside_to_target` null-check + `metrics is None` guard (Task 3, Step 3 + `test_build_coverage_handles_none_metrics`); per-ticker `outcomes` reset (Task 5, Step 3 + `test_run_attaches_coverage_and_does_not_leak`); load-bearing `"fmp"` string documented at the call site (Task 3 `_build_note` comment).
- **Type consistency:** `Coverage(providers, unavailable, note)`, statuses `ok|gated_402|empty|error`, and function names `classify_failure`/`build_coverage`/`coverage_note_line` are used identically across all tasks.
