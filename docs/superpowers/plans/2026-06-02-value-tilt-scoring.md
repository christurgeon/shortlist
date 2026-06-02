# Value-Tilt Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `opportunity = max(momentum, value)` composite component with independent, value-tilted `value` (0.22) and `momentum` (0.08) weights, lower the `scored` floor so the tilt doesn't drop gated names, and add a soft value-trap advisory flag.

**Architecture:** `scoring.py:score()` builds one `components` list that feeds both the composite and `confidence`. We split the `opportunity` entry into two independent entries, retain `ScoreCard.opportunity` as a display-only `max(...)`, lower `validity.min_scored_weight` to 0.25 to offset the confidence loss when the FMP-gated `value` axis is absent, and compute a `value_trap` flag inside `score()` from the value/quality/growth sub-scores.

**Tech Stack:** Python 3, `uv` + `pytest`, dataclasses. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-02-value-tilt-scoring-design.md`

---

## File Structure

| File | Change |
|---|---|
| `config.yaml` | weights (remove `opportunity`, add `value`/`momentum`); floors (`validity.min_scored_weight`, `ranking.thin_below`); new `flags.value_trap` block |
| `src/shortlist/scoring.py` | delete `max()` collapse from composite; split `components`; value-trap flag wiring |
| `src/shortlist/models.py` | `ScoreCard.opportunity` field comment only |
| `src/shortlist/backtest/signals.py` | axis tuple rename |
| `src/shortlist/screen.py` | table title string |
| `tests/test_scoring.py` | fixtures + rewritten/new tests |
| `tests/test_backtest_signals.py` | config weights dict |
| `tests/test_backtest_fit.py` | rewrite planted axis |
| `CLAUDE.md`, `README.md`, `.claude/skills/run/SKILL.md` | docs |

Every commit must leave the full suite (`uv run pytest`) green.

---

## Task 1: Split the composite into independent value + momentum

**Files:**
- Modify: `config.yaml` (weights block)
- Modify: `src/shortlist/scoring.py` (around lines 296-319)
- Modify: `src/shortlist/models.py:118` (comment)
- Test: `tests/test_scoring.py` (CONFIG fixture, `_risk_config`, rewrite the opportunity test, fix risk arithmetic)
- Test: `tests/test_backtest_signals.py` (config weights dict)

- [ ] **Step 1: Update the test fixtures + write the new composite-arithmetic test**

In `tests/test_scoring.py`, change the `CONFIG` weights (lines 42-43) from:
```python
    "weights": {"quality": 0.20, "moat": 0.20, "growth": 0.15,
                "opportunity": 0.30, "insider": 0.15},
```
to:
```python
    "weights": {"quality": 0.20, "moat": 0.20, "growth": 0.15,
                "value": 0.22, "momentum": 0.08, "insider": 0.15},
```

Change `_risk_config` (lines 306-313) from:
```python
def _risk_config():
    c = copy.deepcopy(CONFIG)
    c["thresholds"]["realized_vol"] = [0.45, 0.15]
    c["thresholds"]["max_drawdown"] = [-0.50, -0.10]
    # proportional rescale (x0.9) + risk 0.10
    c["weights"] = {"quality": 0.18, "moat": 0.18, "growth": 0.135,
                    "opportunity": 0.27, "insider": 0.135, "risk": 0.10}
    return c
```
to:
```python
def _risk_config():
    c = copy.deepcopy(CONFIG)
    c["thresholds"]["realized_vol"] = [0.45, 0.15]
    c["thresholds"]["max_drawdown"] = [-0.50, -0.10]
    # six independent components + risk 0.10 (the old x0.9 risk-absent invariant
    # is retired by the value/momentum split)
    c["weights"] = {"quality": 0.18, "moat": 0.18, "growth": 0.135,
                    "value": 0.22, "momentum": 0.08, "insider": 0.135, "risk": 0.10}
    return c
```

Replace `test_opportunity_takes_the_stronger_axis` (lines 162-175) with:
```python
def test_value_and_momentum_weighted_independently():
    base = metrics_all_50()
    # momentum maxed (all 1.0 -> 100), value floored (upside/fcf/pe all 0).
    m = dataclasses.replace(
        base,
        price_vs_200dma=1.0, rel_strength_6m=1.0, eps_revision=1.0,
        target_median=100.0,        # upside_to_target = 0
        fcf_yield=0.0,
        pe_median_5y=10.0,          # pe_vs_history = 0
    )
    card = score(m, CONFIG)
    assert card.momentum == 100.0
    assert card.value == 0.0
    # Display field still reports the stronger axis (max), even though the
    # composite no longer uses it.
    assert card.opportunity == 100.0
    # Composite weights value (0.22) far above momentum (0.08): the floored value
    # axis drags the composite well below the old max()-driven 65.
    # num = 50*0.20 + 50*0.20 + 50*0.15 + 100*0.08 + 0*0.22 + 50*0.15 = 43.0; den = 1.00
    assert card.composite == 43.0
```

Update the section comment above it (line 160) from `# --- opportunity = max(momentum, value) -----` to `# --- value/momentum independent weighting -----`.

Update `test_composite_shifts_when_risk_present` (lines 333-339) — change the arithmetic comment and assertion:
```python
def test_composite_shifts_when_risk_present():
    rc = _risk_config()
    safe = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10)
    card = score(safe, rc)
    assert card.risk == 100.0
    # composite = (50*0.93 + 100*0.10) / 1.03 = 56.5/1.03 = 54.9
    assert card.composite == 54.9
```
(`test_composite_invariant_when_risk_absent` at lines 342-346 needs **no** edit — with all-50 metrics both configs yield 50.0; it now also exercises the value/momentum keys.)

In `tests/test_backtest_signals.py`, change the weights dict (lines 68-69) from:
```python
        "weights": {"quality": 0.2, "moat": 0.2, "growth": 0.15,
                    "opportunity": 0.3, "insider": 0.15},
```
to:
```python
        "weights": {"quality": 0.2, "moat": 0.2, "growth": 0.15,
                    "value": 0.22, "momentum": 0.08, "insider": 0.15},
```

- [ ] **Step 2: Run the affected tests — verify they fail**

Run: `uv run pytest tests/test_scoring.py::test_value_and_momentum_weighted_independently tests/test_scoring.py::test_composite_shifts_when_risk_present -v`
Expected: FAIL — `score()` still reads `w["opportunity"]` and builds the single opportunity component, so it raises `KeyError: 'value'` (the fixtures no longer define `opportunity`).

- [ ] **Step 3: Update `config.yaml` weights**

Replace the `opportunity` line in the `weights:` block:
```yaml
  opportunity:  0.27   # was 0.30 — max(momentum, value), qualifies on either axis
```
with:
```yaml
  value:        0.22   # value-tilt: independent leg (was inside opportunity's 0.27 max)
  momentum:     0.08   # independent leg; value pulls ~3x momentum
```
Leave `quality`, `moat`, `growth`, `insider`, `risk` unchanged.

- [ ] **Step 4: Split the components in `scoring.py`**

Replace the `max()` collapse comment (lines 297-300):
```python
    # Chris's brief: momentum OR deep undervaluation. Take the stronger axis so a
    # name can qualify on either, rather than being averaged down by the weaker one.
    pres = [x for x in (mom, val) if x is not None]
    opp = max(pres) if pres else None
```
with:
```python
    # Value-tilt: value and momentum are weighted INDEPENDENTLY in the composite
    # (see spec 2026-06-02-value-tilt-scoring-design). `opp` is retained only as a
    # display-only convenience on the ScoreCard, not as a composite component.
    pres = [x for x in (mom, val) if x is not None]
    opp = max(pres) if pres else None
```

Replace the `opportunity` entry in the `components` list (line 317):
```python
        ("opportunity", opp, w["opportunity"], ("momentum", "value")),
```
with two entries:
```python
        ("momentum", mom, w["momentum"], ("momentum",)),
        ("value", val, w["value"], ("value",)),
```
(Leave the `ScoreCard(... opportunity=_round(opp) ...)` construction at line 352 unchanged — the display field stays.)

- [ ] **Step 5: Update the `ScoreCard.opportunity` comment**

In `src/shortlist/models.py:118`, change:
```python
    opportunity: Optional[float]  # max(momentum, value): qualifies on either axis
```
to:
```python
    opportunity: Optional[float]  # display-only: max(momentum, value); does NOT feed the composite
```

- [ ] **Step 6: Run the full suite — verify green**

Run: `uv run pytest`
Expected: PASS. If any test that loads the shipped `config.yaml` asserts an exact composite from the old weights, fix that assertion to the new value (none are expected; the integration test only checks ranges and the `opportunity == max(...)` display identity).

- [ ] **Step 7: Commit**

```bash
git add config.yaml src/shortlist/scoring.py src/shortlist/models.py tests/test_scoring.py tests/test_backtest_signals.py
git commit -m "feat(scoring): split opportunity into independent value/momentum weights"
```

---

## Task 2: Align the backtest axis vocabulary

**Files:**
- Modify: `src/shortlist/backtest/signals.py:87`
- Test: `tests/test_backtest_fit.py` (rewrite planted axis)

- [ ] **Step 1: Rewrite the backtest-fit test to plant on `value`**

Replace `tests/test_backtest_fit.py` lines 5-19:
```python
PRIOR = {"quality": 0.2, "moat": 0.2, "growth": 0.15, "opportunity": 0.3, "insider": 0.15}


def _planted(n_periods=40):
    # 'opportunity' perfectly predicts return; others are noise. The fitter should
    # tilt weight toward opportunity vs the prior.
    rows = []
    for p in range(n_periods):
        for k in range(30):
            sub = {"quality": float((k * 7) % 100), "moat": float((k * 13) % 100),
                   "growth": float((k * 3) % 100), "opportunity": float(k),
                   "insider": float((k * 11) % 100)}
            fwd = float(k)                      # return tracks opportunity exactly
            rows.append((p, sub, fwd))
    return rows
```
with:
```python
PRIOR = {"quality": 0.2, "moat": 0.2, "growth": 0.15, "value": 0.22,
         "momentum": 0.08, "insider": 0.15}


def _planted(n_periods=40):
    # 'value' perfectly predicts return; others are noise. The fitter should
    # tilt weight toward value vs the prior.
    rows = []
    for p in range(n_periods):
        for k in range(30):
            sub = {"quality": float((k * 7) % 100), "moat": float((k * 13) % 100),
                   "growth": float((k * 3) % 100), "value": float(k),
                   "momentum": float((k * 17) % 100),
                   "insider": float((k * 11) % 100)}
            fwd = float(k)                      # return tracks value exactly
            rows.append((p, sub, fwd))
    return rows
```

Replace the assertion at line 29:
```python
    assert res.weights["opportunity"] > PRIOR["opportunity"]
```
with:
```python
    assert res.weights["value"] > PRIOR["value"]
```

- [ ] **Step 2: Run the test — verify it still passes against current signals**

Run: `uv run pytest tests/test_backtest_fit.py -v`
Expected: PASS (fit operates on the supplied rows generically; `PRIOR` now sums to 1.0).

- [ ] **Step 3: Rename the emitted axes in `signals.py`**

In `src/shortlist/backtest/signals.py:87`, change:
```python
        for axis in ("quality", "moat", "growth", "opportunity", "insider"):
```
to:
```python
        for axis in ("quality", "moat", "growth", "value", "momentum", "insider"):
```

- [ ] **Step 4: Run the backtest tests — verify green**

Run: `uv run pytest tests/test_backtest_signals.py tests/test_backtest_fit.py -v`
Expected: PASS — `SnapshotSignalSource.observe()` now emits `value`/`momentum` from the retained `ScoreCard` fields; the existing assertions only check `composite`.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/backtest/signals.py tests/test_backtest_fit.py
git commit -m "feat(backtest): replace opportunity axis with value/momentum"
```

---

## Task 3: Lower the scored floor + regression-test the worst case

**Files:**
- Modify: `config.yaml` (`validity.min_scored_weight`, `ranking.thin_below`)
- Test: `tests/test_scoring.py` (two new tests + a shipped-config helper)

- [ ] **Step 1: Write the two C2 regression tests**

Append to `tests/test_scoring.py`:
```python
# --- C2 regression: value-tilt must not drop gated financials below `scored` ---

def _shipped_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    return yaml.safe_load(config_path.read_text())


def test_value_gated_financials_still_scored_worst_case():
    # Financials (SIC 6020): moat masked, and quality/growth/value all
    # gated/absent, so only momentum + insider are present.
    # appl_w = quality .18 + growth .135 + value .22 + momentum .08 + insider .135
    #        = 0.75 (moat .18 masked); pres_w = .08 + .135 = 0.215
    # confidence = 0.215 / 0.75 = 0.287 -> must clear min_scored_weight 0.25.
    cfg = _shipped_config()
    m = StockMetrics(
        ticker="BANK", sic="6020", market_cap=10e9,
        price_vs_200dma=0.1, rel_strength_6m=0.1, eps_revision=0.02,   # momentum present
        insider_sentiment=0.0, insider_net_6m=0.0,                     # insider present, clean
        # quality/growth/value legs all None -> absent
    )
    card = score(m, cfg)
    assert card.sic_bucket == "financials"
    assert card.quality is None and card.growth is None and card.value is None
    assert card.momentum is not None and card.insider is not None
    assert card.confidence == pytest.approx(0.287, abs=0.005)
    assert card.scored is True
    assert card.passed is True


def test_insider_only_financials_not_scored():
    # Only insider present -> confidence 0.135 / 0.75 = 0.18 < 0.25 -> not scored.
    cfg = _shipped_config()
    m = StockMetrics(
        ticker="BANK2", sic="6020", market_cap=10e9,
        insider_sentiment=0.0, insider_net_6m=0.0,
    )
    card = score(m, cfg)
    assert card.sic_bucket == "financials"
    assert card.confidence == pytest.approx(0.18, abs=0.005)
    assert card.scored is False
    assert card.passed is False
```

- [ ] **Step 2: Run the new tests — verify the worst-case one fails**

Run: `uv run pytest tests/test_scoring.py::test_value_gated_financials_still_scored_worst_case tests/test_scoring.py::test_insider_only_financials_not_scored -v`
Expected: `test_value_gated_financials_still_scored_worst_case` FAILS (`scored` is False at the current floor 0.34 — confidence 0.287 < 0.34). `test_insider_only_financials_not_scored` passes already.

- [ ] **Step 3: Lower the floors in `config.yaml`**

In the `validity:` block, change:
```yaml
  min_scored_weight:      0.34  # known-bucket composite 'scored' iff present/applicable component weight >= this
```
to:
```yaml
  min_scored_weight:      0.25  # known-bucket composite 'scored' iff present/applicable component weight >= this.
                                # Lowered 0.34->0.25 for the value/momentum split: a gated financials name with
                                # only momentum+insider present sits at 0.287 and must still rank (spec 2026-06-02).
```

In the `ranking:` block, change:
```yaml
  thin_below: 0.5   # mark a card "thin" when confidence < this. Omit/null to disable.
```
to:
```yaml
  thin_below: 0.40  # mark a card "thin" when confidence < this. Omit/null to disable.
                    # Lowered 0.5->0.40 alongside the value/momentum split (spec 2026-06-02).
```

- [ ] **Step 4: Run the full suite — verify green**

Run: `uv run pytest`
Expected: PASS. The floor change can flip some financials/insurer/REIT fixtures from not-scored to scored; if a test asserted `scored is False`/`thin is True` for a known-bucket name whose confidence is now ≥ 0.25/≥ 0.40, update that assertion to match the new floor (check `tests/test_screen_engine.py`, `tests/test_coverage_abstention.py`).

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_scoring.py
git commit -m "feat(scoring): lower scored floor to 0.25 so value-tilt keeps gated names ranked"
```

---

## Task 4: Add the value-trap advisory flag

**Files:**
- Modify: `config.yaml` (`flags.value_trap` block)
- Modify: `src/shortlist/scoring.py` (flag wiring in `score()`, ~line 354)
- Test: `tests/test_scoring.py` (four new tests)

- [ ] **Step 1: Write the value-trap tests**

Append to `tests/test_scoring.py`:
```python
# --- value-trap advisory flag --------------------------------------------

VT_CFG = {"value_trap": {"min_value_score": 60, "max_quality_score": 40,
                         "max_growth_score": 40}}


def _value_trap_metrics(**kw):
    # value high (~80 on the [0,1] CONFIG bands), quality low (~20). growth stays
    # 50 (not weak), so the flag must fire via the quality clause.
    base = metrics_all_50()
    m = dataclasses.replace(
        base,
        # value legs high: upside 0.8, fcf_yield 0.8, pe_vs_history 0.8, peg 0.4(->80)
        price=100.0, target_median=180.0, fcf_yield=0.8,
        pe_ttm=10.0, pe_median_5y=18.0, peg=0.4,
        # quality legs low: roe/net_margin 0.2, interest_coverage 2(->20), d/e 1.6(->20)
        roe=0.2, net_margin=0.2, interest_coverage=2.0, debt_to_equity=1.6,
    )
    return dataclasses.replace(m, **kw)


def test_value_trap_fires_when_cheap_and_weak():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    card = score(_value_trap_metrics(), cfg)
    assert card.value >= 60
    assert card.quality < 40
    assert "value_trap" in card.flags
    assert card.passed is True            # advisory only — never disqualifies


def test_value_trap_silent_when_fundamentals_strong():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    m = _value_trap_metrics(roe=0.9, net_margin=0.9, interest_coverage=9.0,
                            debt_to_equity=0.2)
    card = score(m, cfg)
    assert card.value >= 60
    assert card.quality >= 40
    assert "value_trap" not in card.flags


def test_value_trap_silent_when_value_missing():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    m = StockMetrics(ticker="T", market_cap=10e9, roe=0.2, net_margin=0.2)
    card = score(m, cfg)
    assert card.value is None
    assert "value_trap" not in card.flags


def test_value_trap_noop_without_config_block():
    card = score(_value_trap_metrics(), CONFIG)   # CONFIG has no flags block
    assert "value_trap" not in card.flags
```

- [ ] **Step 2: Run the tests — verify they fail**

Run: `uv run pytest tests/test_scoring.py -k value_trap -v`
Expected: `test_value_trap_fires_when_cheap_and_weak` FAILS (`value_trap` not yet produced); the three "silent/noop" tests pass vacuously.

- [ ] **Step 3: Wire the flag in `scoring.py`**

In `score()`, find the `ScoreCard(...)` construction (line ~354) where `flags=check_flags(m, config.get("flags") or {})` is passed inline. Hoist it to a local **above** the `return ScoreCard(...)`:
```python
    flags = check_flags(m, config.get("flags") or {})
    # value-trap advisory: cheap (high value) but weak fundamentals. Soft/None-safe
    # like crowded_short — never affects passed/composite/scored. No-op if the
    # config block is absent.
    vt = (config.get("flags") or {}).get("value_trap")
    if (vt and val is not None and val >= vt["min_value_score"]
            and ((q is not None and q < vt["max_quality_score"])
                 or (gr is not None and gr < vt["max_growth_score"]))):
        flags.append("value_trap")
```
Then change the constructor argument from:
```python
        flags=check_flags(m, config.get("flags") or {}),
```
to:
```python
        flags=flags,
```

- [ ] **Step 4: Run the value-trap tests — verify green**

Run: `uv run pytest tests/test_scoring.py -k value_trap -v`
Expected: PASS (all four).

- [ ] **Step 5: Add the `flags.value_trap` block to `config.yaml`**

Under the existing `flags:` block (after the `insider_cluster_buy`/`planned_sale` entries), add:
```yaml
  # Soft value-trap advisory: a name that looks cheap (high value sub-score) while
  # its quality OR growth is weak. Level-based proxy (no deterioration trend without
  # snapshot history). Advisory only — never affects passed/composite/scored.
  # All three are UNFITTED priors — backtest before trusting.
  value_trap:
    min_value_score:   60   # "cheap" floor on the value sub-score (0-100)
    max_quality_score: 40   # below this, quality is "weak"
    max_growth_score:  40   # below this, growth is "weak"
```

- [ ] **Step 6: Run the full suite — verify green**

Run: `uv run pytest`
Expected: PASS. The shipped config now emits `value_trap` for qualifying names; if any integration/snapshot test asserts an exact `flags` list for a cheap-but-weak fixture, update it to include `value_trap`.

- [ ] **Step 7: Commit**

```bash
git add config.yaml src/shortlist/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add soft value-trap advisory flag"
```

---

## Task 5: Docs + cosmetic relabel

**Files:**
- Modify: `src/shortlist/screen.py:89`
- Modify: `CLAUDE.md`, `README.md`, `.claude/skills/run/SKILL.md`

- [ ] **Step 1: Relabel the screener table title**

In `src/shortlist/screen.py:89`, change:
```python
    table = Table(title="Moat + opportunity screen", title_style="bold")
```
to:
```python
    table = Table(title="Moat + value screen", title_style="bold")
```

- [ ] **Step 2: Fix the run skill doc**

In `.claude/skills/run/SKILL.md`:

Replace lines 100-101:
```
`opportunity = max(momentum, value)` — always state which one won.  
Example: "GOOGL's opportunity score of 78 was driven by momentum (78) rather than value (65)."
```
with:
```
`value` and `momentum` are weighted independently in the composite (value-tilted,
~3:1); `opportunity = max(momentum, value)` is reported but is **display-only**.  
Example: "GOOGL ranks on value (65) more than momentum (78); the composite weights value above momentum."
```

Replace line 153 (the weights table row):
```
| Opportunity | 30% | `max(momentum, value)` — qualifies on either axis |
```
with:
```
| Value | 22% | upside to analyst target + FCF yield + P/E vs own 5y median + PEG |
| Momentum | 8% | price vs 200DMA + 6m relative strength + EPS revision |
```

Replace line 156:
```
`momentum` and `value` are reported in the output but are not weighted directly — only their `max` (`opportunity`) is. `value` = upside to analyst target + FCF yield + P/E vs own 5y median + PEG. All scores are 0–100. **These are the defaults — always read the actual weights and gate thresholds from `config.yaml` before narrating; do not hardcode them.**
```
with:
```
`value` and `momentum` are now weighted **independently** (value-tilt: ~3:1); the `opportunity` column is `max(momentum, value)`, retained for display only. `value` = upside to analyst target + FCF yield + P/E vs own 5y median + PEG. All scores are 0–100. **These are the defaults — always read the actual weights and gate thresholds from `config.yaml` before narrating; do not hardcode them.**
```

- [ ] **Step 3: Update `CLAUDE.md`**

In the "Screener data flow" section, replace:
```
`opportunity = max(momentum, value)` so a name qualifies on **either** axis rather
than being averaged down. Composite is a weighted blend (default quality 0.18 /
moat 0.18 / growth 0.135 / opportunity 0.27 / insider 0.135 / risk 0.10).
```
with:
```
**Value and momentum are weighted independently** (value-tilt: default value 0.22 /
momentum 0.08 — value pulls ~3× momentum). `ScoreCard.opportunity = max(momentum,
value)` is retained for display only and does **not** feed the composite. Composite
is a weighted blend (default quality 0.18 / moat 0.18 / growth 0.135 / value 0.22 /
momentum 0.08 / insider 0.135 / risk 0.10).
```

In the same section, find the soft-`flags` paragraph and add `value_trap` to the advisory list, e.g. append after the `crowded_short` description:
```
The `value_trap` advisory fires when a name looks cheap (high `value` sub-score)
while quality OR growth is weak (`config.yaml` → `flags.value_trap`); it is purely
advisory (never affects `passed`/`composite`/`scored`).
```

In the abstention/`ScoreCard` paragraph, update the `min_scored_weight`/`thin_below` references if they cite the old 0.34/0.5 numbers (search for "0.34" and "thin"); set them to 0.25 / 0.40.

- [ ] **Step 4: Update `README.md`**

Search `README.md` for "opportunity" and "max(momentum". Update any axis/weight description to the independent value (0.22) / momentum (0.08) split, noting opportunity is display-only. (If README has no such mention, skip — record "no change needed" in the commit body.)

Run: `grep -n "opportunity\|momentum\|max(" README.md`

- [ ] **Step 5: Run the full suite — verify nothing regressed**

Run: `uv run pytest`
Expected: PASS (docs + a title string; no logic change).

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/screen.py CLAUDE.md README.md .claude/skills/run/SKILL.md
git commit -m "docs: describe independent value/momentum weighting + value-trap flag"
```

---

## Final verification

- [ ] Run the whole suite once more: `uv run pytest` → all green.
- [ ] Sanity-run the screener offline: `uv run shortlist --demo --json` → confirm cards carry distinct `value`/`momentum`, a display `opportunity`, and (where applicable) a `value_trap` flag; no crash.
- [ ] Confirm `git log --oneline` shows the five feature commits on the `value-tilt-scoring` branch.
