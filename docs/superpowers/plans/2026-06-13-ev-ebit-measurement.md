# EV/EBIT Absolute-Valuation Leg — Measurement-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an EV/EBIT earnings-yield metric and the backtest instrumentation to measure whether it is additive or dilutive to the existing `value` sub-score — **without changing any production score**.

**Architecture:** A pure `stats.py` helper computes the EBIT/EV earnings yield (with EBIT>0 / EV>0 / net-debt-known guards). It is wired into both data paths (the harness `bridge.py` and the XBRL backtest panel `_xbrl_facts.py`) onto a new `StockMetrics.ebit_ev_yield` field. Backtest-only scoring functions expose the new leg, the two XBRL-reconstructable existing value legs, and a `value_plus_evebit` combined axis; a cross-signal rank-correlation diagnostic answers the collinearity question. The production scoring leg is **deferred** (spec §11) — nothing in `scoring.score()` changes.

**Tech Stack:** Python 3, `uv`, `pytest`. Pure-stdlib statistics (no numpy/scipy). SEC companyfacts + EDGAR for fundamentals (already wired).

**Spec:** `docs/superpowers/specs/2026-06-13-absolute-valuation-leg-ev-ebit-design.md` (v1 scope = §9 "MEASUREMENT-FIRST").

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/shortlist/stats.py` | Pure financial-math leaves (imported by bridge + xbrl) | Add `net_debt_from`, `compute_ebit_ev_yield` |
| `src/shortlist/models.py` | `StockMetrics` dataclass | Add `ebit_ev_yield` field |
| `src/shortlist/data/bridge.py` | Harness snapshot → `StockMetrics` | Wire `ebit_ev_yield` derivation (~line 181) |
| `src/shortlist/providers/_xbrl_facts.py` | Point-in-time XBRL panel → `StockMetrics` | Wire `ebit_ev_yield` derivation (~line 322) |
| `config.yaml` | Thresholds | Add `thresholds.ebit_ev_yield` band |
| `src/shortlist/scoring.py` | Scoring + backtest-only leg scores | Add 4 backtest-only `*_score` fns |
| `src/shortlist/backtest/signals.py` | `XbrlSignalSource._AXES` | Add 4 axis names |
| `src/shortlist/backtest/metrics.py` | Pure stats | Add `cross_signal_xs_corr` |
| `src/shortlist/backtest/engine.py` | Grid iteration | Add `collect_observations` |
| `src/shortlist/backtest/cli.py` | CLI run path | Print leg-correlation diagnostic |
| `tests/test_stats.py`, `tests/test_bridge_leverage.py`, `tests/test_xbrl_facts.py`, `tests/test_scoring.py`, `tests/test_backtest_signals.py`, `tests/test_backtest_metrics.py`, `tests/test_backtest_engine.py` | Tests | Add/extend |

**Naming convention (avoid collisions — three similar names):**
- `StockMetrics.ebit_ev_yield` — the metric **field** (a yield; higher = cheaper).
- `stats.compute_ebit_ev_yield(...)` — the pure **derivation** helper.
- `scoring.ebit_ev_yield_score(m, t)` — the backtest-only **0–100 axis** score.

---

## Task 1: Pure derivation helpers in `stats.py`

**Files:**
- Modify: `src/shortlist/stats.py`
- Test: `tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stats.py`:

```python
from shortlist.stats import net_debt_from, compute_ebit_ev_yield


def test_net_debt_from_both_present():
    assert net_debt_from(100.0, 30.0) == 70.0

def test_net_debt_from_net_cash_is_negative():
    assert net_debt_from(20.0, 50.0) == -30.0

def test_net_debt_from_one_missing_treats_other_as_zero():
    assert net_debt_from(100.0, None) == 100.0
    assert net_debt_from(None, 40.0) == -40.0

def test_net_debt_from_both_missing_abstains():
    # O1: a market-cap-only EV would silently ignore leverage -> abstain.
    assert net_debt_from(None, None) is None

def test_ebit_ev_yield_basic():
    # EBIT=100, mktcap=900, net_debt=100 -> EV=1000 -> yield 0.10
    assert compute_ebit_ev_yield(100.0, 900.0, 100.0) == 0.10

def test_ebit_ev_yield_net_cash_raises_yield():
    # net cash shrinks EV: EV = 900 - 100 = 800 -> 100/800 = 0.125
    assert compute_ebit_ev_yield(100.0, 900.0, -100.0) == 0.125

def test_ebit_ev_yield_abstains_on_nonpositive_ebit():
    assert compute_ebit_ev_yield(0.0, 900.0, 100.0) is None
    assert compute_ebit_ev_yield(-50.0, 900.0, 100.0) is None

def test_ebit_ev_yield_abstains_on_nonpositive_ev():
    # net cash exceeds market cap -> EV <= 0 artifact
    assert compute_ebit_ev_yield(100.0, 50.0, -60.0) is None

def test_ebit_ev_yield_abstains_on_missing_inputs():
    assert compute_ebit_ev_yield(None, 900.0, 100.0) is None
    assert compute_ebit_ev_yield(100.0, None, 100.0) is None
    assert compute_ebit_ev_yield(100.0, 900.0, None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stats.py -k "ebit_ev or net_debt" -v`
Expected: FAIL — `ImportError: cannot import name 'net_debt_from'`.

- [ ] **Step 3: Implement the helpers**

Append to `src/shortlist/stats.py` (the file already exposes pure leaves; keep the `Optional` import that's already at the top):

```python
def net_debt_from(total_debt: Optional[float],
                  cash: Optional[float]) -> Optional[float]:
    """Net debt = total_debt - cash (signed; net cash -> negative). Returns None
    only when BOTH inputs are missing — a market-cap-only enterprise value would
    silently ignore leverage, so we abstain rather than guess (spec O1). A single
    missing side is treated as zero (conservative)."""
    if total_debt is None and cash is None:
        return None
    return (total_debt or 0.0) - (cash or 0.0)


def compute_ebit_ev_yield(ebit: Optional[float], market_cap: Optional[float],
                          net_debt: Optional[float]) -> Optional[float]:
    """EBIT/EV earnings yield (higher = cheaper). EV = market_cap + net_debt.
    Abstains (None) on EBIT <= 0 (unprofitable is growth/quality's job, not a
    valuation of negative earnings) and on EV <= 0 (net-cash-exceeds-market-cap
    artifact). UNITS: ebit/market_cap/net_debt all absolute USD."""
    if ebit is None or market_cap is None or net_debt is None or ebit <= 0:
        return None
    ev = market_cap + net_debt
    if ev <= 0:
        return None
    return ebit / ev
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stats.py -k "ebit_ev or net_debt" -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/stats.py tests/test_stats.py
git commit -m "feat(stats): EV/EBIT earnings-yield + net-debt derivation helpers"
```

---

## Task 2: `StockMetrics.ebit_ev_yield` field + harness bridge wiring

**Files:**
- Modify: `src/shortlist/models.py:25` (add field in the Valuation block)
- Modify: `src/shortlist/data/bridge.py` (import + derivation after the `net_debt_to_ebitda` block ~line 181)
- Test: `tests/test_bridge_leverage.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bridge_leverage.py` (this file already builds `TickerSnapshot`s with `Statements` and asserts on bridged leverage fields — mirror its existing fixture style; check the top of the file for the exact `Statements`/`TickerSnapshot` construction helper and reuse it):

```python
def test_bridge_derives_ebit_ev_yield(snapshot_with_statements):
    # Helper builds a snapshot whose Statements have operating_income[0]=200,
    # total_debt[0]=500, cash_and_equivalents[0]=100, and market_cap=1300.
    # EV = 1300 + (500 - 100) = 1700 -> yield = 200/1700 ~= 0.1176
    snap = snapshot_with_statements(operating_income=[200.0], total_debt=[500.0],
                                    cash=[100.0], market_cap=1300.0)
    m = snapshot_to_metrics(snap)
    assert m.ebit_ev_yield == pytest.approx(200.0 / 1700.0, rel=1e-6)


def test_bridge_ebit_ev_yield_none_on_negative_ebit(snapshot_with_statements):
    snap = snapshot_with_statements(operating_income=[-10.0], total_debt=[500.0],
                                    cash=[100.0], market_cap=1300.0)
    m = snapshot_to_metrics(snap)
    assert m.ebit_ev_yield is None
```

> If `tests/test_bridge_leverage.py` has no reusable `snapshot_with_statements`
> fixture, write the two tests inline using the same `TickerSnapshot(...)` /
> `Statements(...)` construction the existing leverage tests in that file use
> (search the file for `Statements(` and copy the pattern). Do NOT invent a new
> snapshot shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bridge_leverage.py -k ebit_ev -v`
Expected: FAIL — `AttributeError: 'StockMetrics' object has no attribute 'ebit_ev_yield'`.

- [ ] **Step 3a: Add the field to `StockMetrics`**

In `src/shortlist/models.py`, in the `# Valuation` block (after line 25, `fcf_yield`):

```python
    fcf_yield: Optional[float] = None
    # EBIT/EV earnings yield (absolute valuation leg, §2.2; higher = cheaper).
    # Backtest-measured; NOT yet a production sub-score leg (spec §11). UNFITTED prior.
    ebit_ev_yield: Optional[float] = None
```

- [ ] **Step 3b: Wire the derivation in `bridge.py`**

Add the import near the existing stats import (`src/shortlist/data/bridge.py:7`):

```python
from ..stats import (cagr, compute_ebit_ev_yield, gross_margin_stability,
                     growth_persistence, median_pe, net_debt_from, piotroski_f)
```

Then, immediately after the `net_debt_to_ebitda` derivation block that ends at line 181 (`m.net_debt_to_ebitda = (debt0 - m.cash_and_equivalents) / m.ebitda`) and before the `# Value-leg derivation` comment at line 182, insert:

```python
        # EV/EBIT earnings yield (absolute valuation leg, §2.2). EBIT = operating
        # income; EV = market_cap + net_debt. Same positional [0] alignment the
        # net_debt_to_ebitda derivation above already relies on. Sole producer
        # (no source sets it) -> the is-None guard is for symmetry. UNITS: USD.
        if m.ebit_ev_yield is None:
            m.ebit_ev_yield = compute_ebit_ev_yield(
                oi0, m.market_cap, net_debt_from(debt0, m.cash_and_equivalents))
```

(`oi0` is in scope at line 165, `debt0` at line 169.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bridge_leverage.py -k ebit_ev -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py src/shortlist/data/bridge.py tests/test_bridge_leverage.py
git commit -m "feat(bridge): derive ebit_ev_yield on the harness path"
```

---

## Task 3: XBRL backtest panel wiring

**Files:**
- Modify: `src/shortlist/providers/_xbrl_facts.py` (import + derivation after the `net_debt_to_ebitda` block ~line 322)
- Test: `tests/test_xbrl_facts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_xbrl_facts.py` (reuse the file's existing `XbrlPanel(...)` / `panel_to_metrics(...)` construction — search for `panel_to_metrics(` to copy the call shape, including how `price`/`price_at` are passed):

```python
def test_panel_to_metrics_ebit_ev_yield():
    # operating_income latest=300, total_debt latest=400, cash latest=100,
    # shares=10, price=120 -> market_cap=1200; net_debt=300; EV=1500 -> 300/1500=0.20
    p = XbrlPanel(
        revenue={"2023-12-31": 1000.0},
        operating_income={"2023-12-31": 300.0},
        total_debt={"2023-12-31": 400.0},
        cash={"2023-12-31": 100.0},
        dep_amort={"2023-12-31": 0.0},
        shares=10.0,
    )
    m = panel_to_metrics(p, ticker="TEST", sic=None, price=120.0,
                         price_at=lambda d: None)
    assert m.ebit_ev_yield == pytest.approx(0.20, rel=1e-6)
```

> Match the real `XbrlPanel` field names by reading its `@dataclass` definition
> (`_xbrl_facts.py:~150-170`). Only set the fields the metric needs; leave the
> rest default. If `panel_to_metrics` requires `revenue` to be non-empty to
> proceed (it does — `XbrlSignalSource` drops empty-revenue panels), keep the
> `revenue` entry above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_xbrl_facts.py -k ebit_ev -v`
Expected: FAIL — `assert None == 0.20` (field exists from Task 2 but is unset here).

- [ ] **Step 3: Wire the derivation in `panel_to_metrics`**

Add to the stats import block at `_xbrl_facts.py:215` (`from ..stats import (...)`):

```python
from ..stats import (  # noqa: E402
    # ... existing names ...
    compute_ebit_ev_yield,
)
```

Then, immediately after the existing net-debt block that ends with
`m.net_debt_to_ebitda = ratio_latest(net_debt_series, ebitda_series)` (around line
323) and before `return m`, insert:

```python
    # EV/EBIT earnings yield, point-in-time. EBIT and net_debt are both taken at
    # the latest common fiscal end (net_debt_series is sum_aligned, so no
    # cross-end mixing); EV pairs that net_debt with the as_of market cap (the
    # standard EV convention). Backtest axis only.
    m.ebit_ev_yield = compute_ebit_ev_yield(
        latest(p.operating_income), m.market_cap, latest(net_debt_series))
```

(`net_debt_series` and `m.market_cap` are both already computed above in the same
function; `latest` is imported in this module at line 140.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_xbrl_facts.py -k ebit_ev -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/providers/_xbrl_facts.py tests/test_xbrl_facts.py
git commit -m "feat(xbrl): derive ebit_ev_yield point-in-time in panel_to_metrics"
```

---

## Task 4: Config threshold band

**Files:**
- Modify: `config.yaml` (`thresholds:` block, near `fcf_yield`)

- [ ] **Step 1: Add the band**

Find the `fcf_yield:` entry under `thresholds:` in `config.yaml` and add directly below it:

```yaml
  # EV/EBIT earnings yield (EBIT/EV; higher = cheaper). UNFITTED prior: ~4% yield
  # -> 0, ~12%+ -> 100 (Greenblatt-cheap territory). Backtest-measured only; no
  # production sub-score reads this yet (spec §2.2 / 2026-06-13 design §9).
  ebit_ev_yield: [0.04, 0.12]
```

- [ ] **Step 2: Verify config loads and the band is present**

Run:
```bash
uv run python -c "import yaml; print(yaml.safe_load(open('config.yaml'))['thresholds']['ebit_ev_yield'])"
```
Expected: `[0.04, 0.12]`

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "feat(config): add ebit_ev_yield threshold band (unfitted prior)"
```

---

## Task 5: Backtest-only scoring functions

**Files:**
- Modify: `src/shortlist/scoring.py` (after `net_debt_to_ebitda_score`, ~line 165)
- Test: `tests/test_scoring.py`

These four functions mirror the `share_count_score` / `net_debt_to_ebitda_score`
precedent: single-leg or combined 0–100 maps used **only** by the backtest, never
by `score()`. `value_plus_evebit_score` is the load-bearing one — it is the value
average **with** the EV/EBIT leg folded in, so the backtest can compare
`IC(value_plus_evebit)` to `IC(value)` (standalone-leg IC cannot answer
"additive-or-dilutive to the average").

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scoring.py`:

```python
from shortlist.scoring import (ebit_ev_yield_score, value_fcf_yield_score,
                               value_pe_vs_history_score, value_plus_evebit_score)

_T = {
    "ebit_ev_yield": [0.04, 0.12],
    "fcf_yield": [0.0, 0.08],
    "pe_vs_history": [-0.3, 0.3],
    "upside_to_target": [0.0, 0.4],
    "peg": [3.0, 0.5],
}

def test_ebit_ev_yield_score_maps_band():
    m = StockMetrics(ticker="X", ebit_ev_yield=0.12)
    assert ebit_ev_yield_score(m, _T) == 100.0
    m2 = StockMetrics(ticker="X", ebit_ev_yield=0.04)
    assert ebit_ev_yield_score(m2, _T) == 0.0

def test_ebit_ev_yield_score_none_when_absent():
    assert ebit_ev_yield_score(StockMetrics(ticker="X"), _T) is None
    assert ebit_ev_yield_score(StockMetrics(ticker="X", ebit_ev_yield=0.1), {}) is None

def test_value_fcf_yield_score_single_leg():
    m = StockMetrics(ticker="X", fcf_yield=0.08)
    assert value_fcf_yield_score(m, _T) == 100.0

def test_value_pe_vs_history_score_single_leg():
    m = StockMetrics(ticker="X", pe_ttm=10.0, pe_median_5y=13.0)  # pe_vs_history = 0.3
    assert value_pe_vs_history_score(m, _T) == 100.0

def test_value_plus_evebit_folds_leg_into_average():
    # fcf_yield=0.04 -> _norm(0.04;0,0.08)=50; ebit_ev_yield=0.08 -> _norm(0.08;0.04,0.12)=50
    # only two legs present -> average 50
    m = StockMetrics(ticker="X", fcf_yield=0.04, ebit_ev_yield=0.08)
    assert value_plus_evebit_score(m, _T) == 50.0

def test_value_plus_evebit_equals_value_when_leg_absent():
    # With no ebit_ev_yield, value_plus_evebit == value_score (same legs).
    m = StockMetrics(ticker="X", fcf_yield=0.04)
    assert value_plus_evebit_score(m, _T) == value_score(m, _T)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scoring.py -k "ebit_ev or value_fcf or value_pe_vs or value_plus" -v`
Expected: FAIL — `ImportError: cannot import name 'ebit_ev_yield_score'`.

- [ ] **Step 3: Implement the functions**

In `src/shortlist/scoring.py`, after `net_debt_to_ebitda_score` (ends at line 164) and before the `# --- Sector-aware abstention ---` divider at line 167:

```python
def ebit_ev_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone absolute-valuation axis for the backtest: the EBIT/EV earnings-
    yield band -> 0..100 (higher yield = cheaper scores higher). Backtest-only,
    like share_count_score; there is NO production sub-score reading ebit_ev_yield
    yet (spec §11 deferred the leg). None when the band or the signal is absent."""
    if "ebit_ev_yield" not in t or m.ebit_ev_yield is None:
        return None
    return _norm(m.ebit_ev_yield, *t["ebit_ev_yield"])


def value_fcf_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only per-leg attribution: the value axis's fcf_yield leg in
    isolation, so its standalone rank IC sits beside the combined `value` IC."""
    return _norm(m.fcf_yield, *t["fcf_yield"])


def value_pe_vs_history_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only per-leg attribution: the value axis's pe_vs_history leg in
    isolation (see value_fcf_yield_score)."""
    return _norm(m.pe_vs_history(), *t["pe_vs_history"])


def value_plus_evebit_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the `value` average WITH the EV/EBIT earnings-yield leg
    folded in. Comparing IC(value_plus_evebit) vs IC(value) answers whether the
    leg is additive or dilutive TO THE AVERAGE — the question a standalone-leg IC
    cannot. Mirrors value_score() exactly plus the (None-safe) 5th leg, so it
    equals value_score when ebit_ev_yield / its band is absent. NOT a production
    sub-score."""
    legs = [
        _norm(m.upside_to_target(), *t["upside_to_target"]),
        _norm(m.fcf_yield, *t["fcf_yield"]),
        _norm(m.pe_vs_history(), *t["pe_vs_history"]),
        _norm(m.peg, *t["peg"]),
    ]
    if "ebit_ev_yield" in t and m.ebit_ev_yield is not None:
        legs.append(_norm(m.ebit_ev_yield, *t["ebit_ev_yield"]))
    return _avg(legs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py -k "ebit_ev or value_fcf or value_pe_vs or value_plus" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Guard against production regressions**

Run the full scoring suite to confirm `score()` is untouched (these are additive functions):
Run: `uv run pytest tests/test_scoring.py tests/test_scoring_abstention.py -q`
Expected: PASS (all pre-existing tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): backtest-only EV/EBIT + per-leg value attribution scores"
```

---

## Task 6: Wire the new axes into `XbrlSignalSource`

**Files:**
- Modify: `src/shortlist/backtest/signals.py:111-112` (`_AXES`) + the docstring
- Test: `tests/test_backtest_signals.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backtest_signals.py` (reuse the file's existing companyfacts
fixture / `XbrlSignalSource` construction — search for `XbrlSignalSource(` to copy
the setup, including the `histories` and `thresholds` it passes):

```python
def test_xbrl_source_emits_ev_ebit_axes(xbrl_source_with_facts):
    # Fixture builds an XbrlSignalSource over a name with positive EBIT, debt,
    # cash, and a price history, plus a thresholds dict including ebit_ev_yield.
    src, as_of = xbrl_source_with_facts
    obs = src.observe("TEST", as_of)
    assert obs is not None
    assert "ebit_ev_yield" in obs.signals
    assert "value_fcf_yield" in obs.signals
    assert "value_pe_vs_history" in obs.signals
    assert "value_plus_evebit" in obs.signals
```

> If no reusable fixture exists, construct the source inline the way the existing
> `test_xbrl_signal.py` / `test_backtest_signals.py` tests do (a small
> companyfacts dict + a `PriceHistory` + `config["thresholds"]` that now includes
> `ebit_ev_yield`). The point is only that the four axis names appear in
> `obs.signals` for a name with the inputs present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest_signals.py -k ev_ebit -v`
Expected: FAIL — the four keys are absent from `obs.signals`.

- [ ] **Step 3: Add the axis names**

In `src/shortlist/backtest/signals.py`, extend `_AXES` (lines 111-112):

```python
    _AXES = ("quality", "moat", "growth", "value", "piotroski", "share_count",
             "net_debt_to_ebitda", "ebit_ev_yield", "value_fcf_yield",
             "value_pe_vs_history", "value_plus_evebit")
```

Update the class docstring (after the `net_debt_to_ebitda` axis sentence ~line 108)
to add:

```
    Also emits the absolute-valuation axes `ebit_ev_yield` (EBIT/EV earnings yield,
    unfitted prior), the per-leg value-attribution axes `value_fcf_yield` /
    `value_pe_vs_history`, and `value_plus_evebit` (the value average WITH the
    EV/EBIT leg) so the leg's additive-or-dilutive effect on the combined `value`
    IC is measurable before any production use (spec §11).
```

The `observe()` loop already calls `getattr(scoring, f"{axis}_score")(m, t)` for
each axis, so the four functions from Task 5 are picked up automatically.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest_signals.py -k ev_ebit -v`
Expected: PASS.

- [ ] **Step 5: Run the full backtest signal suite**

Run: `uv run pytest tests/test_backtest_signals.py tests/test_xbrl_signal.py -q`
Expected: PASS (the added axes don't disturb existing ones).

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/backtest/signals.py tests/test_backtest_signals.py
git commit -m "feat(backtest): emit EV/EBIT + per-leg value attribution axes"
```

---

## Task 7: Cross-signal correlation diagnostic

**Files:**
- Modify: `src/shortlist/backtest/metrics.py` (add `cross_signal_xs_corr`)
- Modify: `src/shortlist/backtest/engine.py` (add `collect_observations`)
- Modify: `src/shortlist/backtest/cli.py` (print the diagnostic)
- Test: `tests/test_backtest_metrics.py`, `tests/test_backtest_engine.py`

The combined-IC comparison (`value_plus_evebit` vs `value`) is already visible in
the report. This task adds the second half of the enable rule: the cross-sectional
rank correlation between `ebit_ev_yield` and `value_fcf_yield` (if > ~0.5 the leg
is dilutive-by-construction in an average).

- [ ] **Step 1: Write the failing test for the pure stat**

Add to `tests/test_backtest_metrics.py`:

```python
from shortlist.backtest.metrics import cross_signal_xs_corr
from shortlist.backtest.signals import Observation
from datetime import date

def test_cross_signal_xs_corr_perfect_rank_agreement():
    d = date(2023, 1, 1)
    obs = [
        Observation(d, "A", {"x": 1.0, "y": 10.0}),
        Observation(d, "B", {"x": 2.0, "y": 20.0}),
        Observation(d, "C", {"x": 3.0, "y": 30.0}),
    ]
    assert cross_signal_xs_corr(obs, "x", "y") == 1.0

def test_cross_signal_xs_corr_skips_rows_missing_a_leg():
    d = date(2023, 1, 1)
    obs = [
        Observation(d, "A", {"x": 1.0, "y": 30.0}),
        Observation(d, "B", {"x": 2.0}),            # no y -> skipped
        Observation(d, "C", {"x": 3.0, "y": 10.0}),
        Observation(d, "D", {"x": 4.0, "y": 20.0}),
    ]
    # usable: (1,30),(3,10),(4,20) -> ranks x:1,2,3 y:3,1,2 -> negative corr
    assert cross_signal_xs_corr(obs, "x", "y") < 0

def test_cross_signal_xs_corr_none_when_too_few_pairs():
    d = date(2023, 1, 1)
    obs = [Observation(d, "A", {"x": 1.0, "y": 1.0})]
    assert cross_signal_xs_corr(obs, "x", "y") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest_metrics.py -k cross_signal -v`
Expected: FAIL — `ImportError: cannot import name 'cross_signal_xs_corr'`.

- [ ] **Step 3: Implement the pure stat**

Add to `src/shortlist/backtest/metrics.py` (it already imports `mean`; add the
collections + typing imports if absent). It mirrors the engine's cross-sectional
treatment: per-date Spearman, averaged over dates.

```python
from collections import defaultdict


def cross_signal_xs_corr(observations, sig_a: str, sig_b: str) -> Optional[float]:
    """Mean per-date Spearman rank correlation between two emitted signals over the
    names where BOTH are present. Diagnoses leg collinearity (e.g. ebit_ev_yield vs
    fcf_yield): a high value means a new leg duplicates an existing one and would
    dilute, not add to, an unweighted value average. None if no date has >= 3
    co-present pairs."""
    by_date: dict = defaultdict(lambda: ([], []))
    for obs in observations:
        sigs = obs.signals
        if sig_a in sigs and sig_b in sigs:
            a, b = by_date[obs.as_of]
            a.append(sigs[sig_a])
            b.append(sigs[sig_b])
    cors = [spearman_ic(a, b) for a, b in by_date.values()]
    cors = [c for c in cors if c is not None]
    return round(mean(cors), 4) if cors else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest_metrics.py -k cross_signal -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test for `collect_observations`**

Add to `tests/test_backtest_engine.py`:

```python
from shortlist.backtest.engine import collect_observations, observation_grid
from shortlist.backtest.signals import Observation
from datetime import date

class _StubSource:
    def observe(self, ticker, as_of):
        return Observation(as_of, ticker, {"x": 1.0})

def test_collect_observations_covers_grid_and_universe():
    grid = [date(2023, 1, 1), date(2023, 4, 1)]
    obs = collect_observations(_StubSource(), ["A", "B"], grid)
    assert len(obs) == 4
    assert {o.ticker for o in obs} == {"A", "B"}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest_engine.py -k collect_observations -v`
Expected: FAIL — `ImportError: cannot import name 'collect_observations'`.

- [ ] **Step 7: Implement `collect_observations`**

Add to `src/shortlist/backtest/engine.py` (near `_collect_rows`):

```python
def collect_observations(src: SignalSource, universe: list[str],
                         grid: list[date]) -> list:
    """All non-empty Observations a source emits over (grid x universe). Used by
    diagnostics (e.g. cross-signal correlation) that need raw signal vectors, not
    forward-return joins. Cheap: companyfacts/prices are already cached."""
    out = []
    for t in grid:
        for tk in universe:
            obs = src.observe(tk, t)
            if obs is not None and obs.signals:
                out.append(obs)
    return out
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest_engine.py -k collect_observations -v`
Expected: PASS.

- [ ] **Step 9: Wire the diagnostic into the CLI**

In `src/shortlist/backtest/cli.py`, replace the existing engine import at line 16
(`from .engine import run_backtest`) with:

```python
from .engine import collect_observations, observation_grid, run_backtest
from .metrics import cross_signal_xs_corr
```

(`import sys` is already present at line 8.) Then, in `main()`, immediately after
`report = run_backtest(...)`
(line 244-247) and before the `if args.csv:` block, insert:

```python
    # Collinearity diagnostic (spec §11): is the new EV/EBIT leg redundant with the
    # existing absolute fcf_yield leg? Only meaningful for the XBRL fundamental
    # source. Printed to stderr so --json stdout stays clean.
    if args.source == "xbrl":
        diag_grid = observation_grid(start, end, args.step_months or horizons[0])
        diag_obs = collect_observations(src, sorted(hists.keys()), diag_grid)
        corr = cross_signal_xs_corr(diag_obs, "ebit_ev_yield", "value_fcf_yield")
        if corr is not None:
            print(f"Leg collinearity  corr(ebit_ev_yield, fcf_yield) = {corr:+.3f} "
                  f"(>~0.5 => the EV/EBIT leg largely duplicates fcf_yield)",
                  file=sys.stderr)
```

- [ ] **Step 10: Verify the CLI smoke-runs (offline-safe check)**

The XBRL path needs `SEC_IDENTITY` + network; do a structural smoke test instead —
confirm the module imports and the new symbols resolve:

Run:
```bash
uv run python -c "from shortlist.backtest.cli import main; from shortlist.backtest.metrics import cross_signal_xs_corr; from shortlist.backtest.engine import collect_observations; print('ok')"
```
Expected: `ok`

- [ ] **Step 11: Commit**

```bash
git add src/shortlist/backtest/metrics.py src/shortlist/backtest/engine.py src/shortlist/backtest/cli.py tests/test_backtest_metrics.py tests/test_backtest_engine.py
git commit -m "feat(backtest): cross-signal collinearity diagnostic for the EV/EBIT leg"
```

---

## Task 8: Full-suite regression + docs note

**Files:**
- Modify: `docs/ASSESSMENT_GAPS.md` §2.2 (status update)

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions; the work is purely additive to production scoring).

- [ ] **Step 2: Update the roadmap status**

In `docs/ASSESSMENT_GAPS.md` §2.2, update the "Still open — absolute-multiple half"
bullet to note the measurement slice shipped:

```markdown
- **MEASUREMENT SHIPPED (2026-06-13), production leg DEFERRED:** the EV/EBIT
  earnings-yield metric (`StockMetrics.ebit_ev_yield`, derived on both the harness
  and the XBRL panel) plus backtest instrumentation — standalone `ebit_ev_yield`
  axis, per-leg `value_fcf_yield` / `value_pe_vs_history` attribution, a
  `value_plus_evebit` combined axis, and a `corr(ebit_ev_yield, fcf_yield)`
  collinearity diagnostic (`--source xbrl`). The production scoring leg ships
  ONLY IF `IC(value_plus_evebit) > IC(value)` AND the leg correlation is materially
  < 0.5 (spec `2026-06-13-absolute-valuation-leg-ev-ebit-design.md` §9/§11).
```

- [ ] **Step 3: Commit**

```bash
git add docs/ASSESSMENT_GAPS.md
git commit -m "docs: mark EV/EBIT measurement slice shipped, production leg deferred"
```

---

## Self-Review Notes

- **Spec coverage:** §9 step 1 (derivation + field) → Tasks 1–3; §9 step 2
  (standalone axes) → Tasks 5–6; §9 step 3 (`value_plus_evebit` + correlation) →
  Tasks 5 & 7; §11(3) date-alignment → handled by fiscal-end `sum_aligned` on the
  XBRL side (Task 3) and the documented positional-[0] parity with the existing
  `net_debt_to_ebitda` derivation on the harness side (Task 2); deferred items
  (production leg, config block, masking, surfacing, `ScoreCard` field) are
  explicitly OUT of scope and have no tasks — correct.
- **Production untouched:** no task edits `scoring.score()`, `_value_legs`,
  `config.yaml` weights/gates, `sectors.masked_legs`, or `screen.py`. The only
  `config.yaml` change is an additive `thresholds.ebit_ev_yield` band that no
  production code path reads. Task 5 step 5 + Task 8 step 1 pin this.
- **Type consistency:** `compute_ebit_ev_yield(ebit, market_cap, net_debt)` and
  `net_debt_from(total_debt, cash)` signatures are used identically in Tasks 2 & 3;
  axis name ↔ function name mapping (`ebit_ev_yield`→`ebit_ev_yield_score`,
  `value_fcf_yield`→`value_fcf_yield_score`, `value_pe_vs_history`→
  `value_pe_vs_history_score`, `value_plus_evebit`→`value_plus_evebit_score`) is
  consistent between Task 5 (definitions) and Task 6 (`_AXES`) and is required by
  the existing `getattr(scoring, f"{axis}_score")` dispatch.
- **Fixture caveats:** Tasks 2, 3, 6 depend on existing test fixtures whose exact
  shape must be read from the target test file before writing (flagged inline in
  each task). This is the one place the engineer must inspect surrounding code.
```
