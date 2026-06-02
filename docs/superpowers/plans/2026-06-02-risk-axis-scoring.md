# Risk Sub-Score (7th Axis) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `risk` sub-score (from `realized_vol` + `max_drawdown`) as a composite-only tilt at weight 0.10, without disturbing the plain-screener path or the existing `confidence`/`scored`/`passed` accounting.

**Architecture:** `risk_score(m, t)` is a sector-neutral standalone helper (mirrors `insider_score`). It is blended into the composite *after* the five-component `num/den`, and is deliberately **excluded** from the `components` list that drives `confidence`/`scored`/coverage/abstention. Weights are proportionally rescaled (×0.9) so that when risk is absent the composite is bit-identical to the pre-change scorer. The component is config-gated (skipped if `weights.risk` or the risk thresholds are missing) so minimal-config unit tests don't `KeyError`.

**Tech Stack:** Python 3.11+, pytest, uv. Files: `src/shortlist/scoring.py`, `src/shortlist/models.py`, `config.yaml`, `src/shortlist/screen.py`, `src/shortlist/scout/report.py`.

**Spec:** `docs/superpowers/specs/2026-06-02-risk-axis-scoring-design.md`

---

## File structure

- `src/shortlist/models.py` — `ScoreCard` gains a `risk: Optional[float] = None` field (appended last).
- `src/shortlist/scoring.py` — new `risk_score(m, t)` helper; `score()` blends risk into the composite (config-gated) and passes `risk=` to `ScoreCard`.
- `config.yaml` — risk thresholds + rescaled weights with a `risk` weight.
- `src/shortlist/screen.py` — `_card_dict` (JSON), `_write_csv`, `_print_table`, `_print_plain` gain a risk column/key.
- `src/shortlist/scout/report.py` — human-facing axis line gains risk.
- Tests live in `tests/test_scoring.py` (scoring + invariants) and `tests/test_scorecard_fields.py` / `tests/test_card_dict_abstention.py` (field + output).

---

## Task 1: `ScoreCard.risk` field

**Files:**
- Modify: `src/shortlist/models.py:128` (append after `abstentions`)
- Test: `tests/test_scorecard_fields.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scorecard_fields.py`:

```python
def test_scorecard_has_risk_field_defaulting_none():
    from shortlist.models import ScoreCard
    card = ScoreCard(
        ticker="T", composite=50.0, quality=None, moat=None, growth=None,
        momentum=None, value=None, opportunity=None, insider=None,
    )
    assert card.risk is None
    card2 = ScoreCard(
        ticker="T", composite=50.0, quality=None, moat=None, growth=None,
        momentum=None, value=None, opportunity=None, insider=None, risk=42.0,
    )
    assert card2.risk == 42.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scorecard_fields.py::test_scorecard_has_risk_field_defaulting_none -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'risk'`

- [ ] **Step 3: Add the field**

In `src/shortlist/models.py`, the `ScoreCard` dataclass currently ends:

```python
    sic_bucket: Optional[str] = None
    confidence: float = 1.0
    scored: bool = True
    abstentions: list = field(default_factory=list)
```

Append one line after `abstentions`:

```python
    sic_bucket: Optional[str] = None
    confidence: float = 1.0
    scored: bool = True
    abstentions: list = field(default_factory=list)
    # 7th sub-score (risk). Appended last so positional construction through the
    # leading fields is unaffected. Composite-only tilt; excluded from confidence.
    risk: Optional[float] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scorecard_fields.py::test_scorecard_has_risk_field_defaulting_none -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py tests/test_scorecard_fields.py
git commit -m "feat(scoring): add ScoreCard.risk field (7th axis, appended last)"
```

---

## Task 2: `risk_score()` helper

**Files:**
- Modify: `src/shortlist/scoring.py` (add helper after `insider_score`, ~line 86)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py`. The bands are inverted (`lo > hi` for vol; `lo < hi` but both negative for drawdown), so safer → higher.

```python
from shortlist.scoring import risk_score

_RISK_T = {"realized_vol": [0.45, 0.15], "max_drawdown": [-0.50, -0.10]}


def test_risk_score_direction_and_clamp():
    import dataclasses
    base = metrics_all_50()
    # Safe name: low vol (0.15 -> 100), shallow drawdown (-0.10 -> 100)
    safe = dataclasses.replace(base, realized_vol=0.15, max_drawdown=-0.10)
    assert risk_score(safe, _RISK_T) == 100.0
    # Risky name: high vol (0.45 -> 0), deep drawdown (-0.50 -> 0)
    risky = dataclasses.replace(base, realized_vol=0.45, max_drawdown=-0.50)
    assert risk_score(risky, _RISK_T) == 0.0
    # Midpoint: vol 0.30 -> 50, drawdown -0.30 -> 50
    mid = dataclasses.replace(base, realized_vol=0.30, max_drawdown=-0.30)
    assert risk_score(mid, _RISK_T) == 50.0
    # Clamp beyond band ends
    extreme = dataclasses.replace(base, realized_vol=0.05, max_drawdown=-0.80)
    # vol 0.05 -> 100 (capped), drawdown -0.80 -> 0 (floored); avg = 50
    assert risk_score(extreme, _RISK_T) == 50.0


def test_risk_score_none_when_no_legs():
    import dataclasses
    base = metrics_all_50()
    no_risk = dataclasses.replace(base, realized_vol=None, max_drawdown=None)
    assert risk_score(no_risk, _RISK_T) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring.py::test_risk_score_direction_and_clamp -v`
Expected: FAIL — `ImportError: cannot import name 'risk_score'`

- [ ] **Step 3: Implement the helper**

In `src/shortlist/scoring.py`, immediately after `insider_score` (the function ending at ~line 85, before the `# --- Sector-aware abstention ---` comment), add:

```python
def risk_score(m: StockMetrics, t: dict) -> Optional[float]:
    # Sector-neutral (like insider_score): realized volatility and max drawdown are
    # well-defined for every sector, so risk is never masked. Both legs are inverted
    # (bands with safer -> higher): low vol and a shallow drawdown score high.
    return _avg([
        _norm(m.realized_vol, *t["realized_vol"]),
        _norm(m.max_drawdown, *t["max_drawdown"]),
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py::test_risk_score_direction_and_clamp tests/test_scoring.py::test_risk_score_none_when_no_legs -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add risk_score helper (vol + drawdown, sector-neutral)"
```

---

## Task 3: Blend risk into the composite (config-gated, composite-only)

**Files:**
- Modify: `src/shortlist/scoring.py` — `score()` (the `parts`/`num`/`den` block ~lines 274-296)
- Test: `tests/test_scoring.py`

This is the core task. It must satisfy three invariants: (a) composite shifts when risk is present, (b) composite is identical when risk is absent, (c) `confidence`/`scored`/`passed` are identical whether or not risk is present (risk never enters `components`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scoring.py`. `RISK_CONFIG` is `CONFIG` plus rescaled weights and risk thresholds.

```python
import copy

def _risk_config():
    c = copy.deepcopy(CONFIG)
    c["thresholds"]["realized_vol"] = [0.45, 0.15]
    c["thresholds"]["max_drawdown"] = [-0.50, -0.10]
    # proportional rescale (x0.9) + risk 0.10
    c["weights"] = {"quality": 0.18, "moat": 0.18, "growth": 0.135,
                    "opportunity": 0.27, "insider": 0.135, "risk": 0.10}
    return c


def test_composite_shifts_when_risk_present():
    import dataclasses
    rc = _risk_config()
    base = metrics_all_50()  # all five sub-scores = 50 -> composite 50
    # Add a strong (100) risk sub-score: composite must rise above 50.
    safe = dataclasses.replace(base, realized_vol=0.15, max_drawdown=-0.10)
    card = score(safe, rc)
    assert card.risk == 100.0
    # composite = (50*0.90 + 100*0.10) / 1.0 = 55.0
    assert card.composite == 55.0


def test_composite_invariant_when_risk_absent():
    """The x0.9 scalar cancels: rescaled-6-weight config with risk None gives the
    same composite as the original 5-weight config."""
    import dataclasses
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=None, max_drawdown=None)
    assert score(m, rc).composite == score(m, CONFIG).composite
    assert score(m, rc).composite == 50.0


def test_confidence_and_scored_invariant_when_risk_absent():
    """Risk is excluded from components, so confidence/scored/passed are identical
    with or without the risk weight when risk data is absent."""
    import dataclasses
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=None, max_drawdown=None)
    a, b = score(m, CONFIG), score(m, rc)
    assert a.confidence == b.confidence
    assert a.scored == b.scored
    assert a.passed == b.passed
    # And risk leaves no abstention footprint:
    assert all(x.get("field") != "risk" for x in b.abstentions)


def test_no_keyerror_on_config_without_risk():
    """Minimal config (no risk weight / thresholds) must score without KeyError and
    leave risk None."""
    import dataclasses
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.20, max_drawdown=-0.15)
    card = score(m, CONFIG)  # CONFIG has no risk keys
    assert card.risk is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scoring.py -k "composite_shifts or composite_invariant or confidence_and_scored_invariant or no_keyerror_on_config" -v`
Expected: FAIL — `test_composite_shifts_when_risk_present` and others fail because `card.risk` is always `None` / composite doesn't shift.

- [ ] **Step 3: Wire risk into `score()`**

In `src/shortlist/scoring.py`, locate the block in `score()` (currently ~lines 273-296):

```python
    # Composite: unchanged math over present components, weight redistributed.
    parts = [(s, weight) for _, s, weight, _ in components if s is not None]
    num = sum(s * weight for s, weight in parts)
    den = sum(weight for _, weight in parts)
    composite = round(num / den, 1) if den else 0.0
```

Replace it with (adds risk to `parts` only; `components` is untouched, so the
confidence block below it is unchanged):

```python
    # Risk: a composite-only tilt (config-gated). Sector-neutral like insider, but
    # deliberately NOT added to `components` -> it never enters appl_w/pres_w, so
    # confidence/scored/passed stay bit-identical when risk is absent. The five
    # weights are rescaled x0.9 in config, so with risk absent the scalar cancels in
    # num/den and the composite equals the pre-change scorer. See the design spec §3.
    risk_on = ("risk" in w) and ("realized_vol" in t) and ("max_drawdown" in t)
    ri = risk_score(m, t) if risk_on else None

    # Composite: unchanged math over present components, weight redistributed,
    # plus the risk tilt when present.
    parts = [(s, weight) for _, s, weight, _ in components if s is not None]
    if ri is not None:
        parts.append((ri, w["risk"]))
    num = sum(s * weight for s, weight in parts)
    den = sum(weight for _, weight in parts)
    composite = round(num / den, 1) if den else 0.0
```

Then update the `ScoreCard(...)` return to pass `risk=`. The return currently is:

```python
    return ScoreCard(
        ticker=m.ticker,
        composite=composite,
        quality=_round(q), moat=_round(mo), growth=_round(gr), momentum=_round(mom),
        value=_round(val), opportunity=_round(opp), insider=_round(ins),
        gates=check_gates(m, config["gates"], bucket, config),
        flags=check_flags(m, config.get("flags") or {}),
        metrics=m,
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
    )
```

Add `risk=_round(ri),` as the final keyword argument:

```python
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
        risk=_round(ri),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py -k "composite_shifts or composite_invariant or confidence_and_scored_invariant or no_keyerror_on_config" -v`
Expected: PASS (all four)

- [ ] **Step 5: Run the whole scoring + sector suite (regression)**

Run: `uv run pytest tests/test_scoring.py tests/test_scoring_abstention.py tests/test_sectors.py tests/test_coverage_abstention.py -q`
Expected: PASS, no regressions. (The integration test against the real `config.yaml` has not changed yet — `config.yaml` still has no risk keys, so risk stays off there until Task 4.)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): blend risk as composite-only tilt (config-gated, confidence-neutral)"
```

---

## Task 4: Activate risk in the shipped `config.yaml`

**Files:**
- Modify: `config.yaml` — `thresholds` block (after the Insider section, ~line 35) and `weights` block (lines 37-44)
- Test: `tests/test_scoring.py` (the integration test that loads `config.yaml`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py` (loads the real shipped config and asserts risk is active and weights sum to 1.0):

```python
def test_shipped_config_activates_risk():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    w = cfg["weights"]
    assert w["risk"] == 0.10
    assert abs(sum(w.values()) - 1.0) < 1e-9
    t = cfg["thresholds"]
    assert t["realized_vol"] == [0.45, 0.15]
    assert t["max_drawdown"] == [-0.50, -0.10]
    import dataclasses
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10)
    card = score(m, cfg)
    assert card.risk == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring.py::test_shipped_config_activates_risk -v`
Expected: FAIL — `KeyError: 'risk'` (config has no risk weight yet).

- [ ] **Step 3: Edit `config.yaml`**

In the `thresholds:` block, after the Insider lines (`insider_net_ratio: ...`, ~line 35), add:

```yaml
  # Risk (inverted: safer -> higher score). Unfitted prior — backtest before trusting.
  realized_vol:   [0.45, 0.15]    # annualized stdev of daily returns
  max_drawdown:   [-0.50, -0.10]  # trailing ~1y peak-to-trough (negative)
```

Replace the `weights:` block (lines 37-44) with the proportionally-rescaled vector:

```yaml
weights:
  quality:      0.18   # was 0.20 (x0.9 on risk-axis introduction)
  moat:         0.18   # was 0.20
  growth:       0.135  # was 0.15 — fundamental compounding (revenue/FCF/EPS CAGR + persistence)
  opportunity:  0.27   # was 0.30 — max(momentum, value), qualifies on either axis
  insider:      0.135  # was 0.15
  risk:         0.10   # NEW — vol/drawdown tilt; composite-only, excluded from confidence
  # NOTE: a defensible prior, not a fitted result. The risk weight especially:
  # trailing vol/drawdown peak at the bottom and can be anti-predictive at turning
  # points — backtest its standalone rank IC (docs/ASSESSMENT_GAPS.md §2.1, §2.9)
  # before trusting these weights.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring.py::test_shipped_config_activates_risk -v`
Expected: PASS

- [ ] **Step 5: Run the full scoring suite + any config-dependent integration tests**

Run: `uv run pytest tests/test_scoring.py tests/test_harness.py tests/test_bridge.py -q`
Expected: PASS. The shipped-config integration test (`test_score_runs_against_shipped_config_and_mock_data`, line 242) only asserts `0.0 <= composite <= 100.0` and the `opportunity == max(momentum, value)` identity — it has **no** frozen composite value, and `MockProvider`'s sample data sets no `realized_vol`/`max_drawdown`, so risk stays `None` there and the composite is unchanged. No edit to that test is needed.

- [ ] **Step 6: Commit**

```bash
git add config.yaml tests/test_scoring.py
git commit -m "feat(config): activate risk axis (weight 0.10, proportional rescale)"
```

---

## Task 5: Output surfaces (JSON, CSV, tables, scout report)

**Files:**
- Modify: `src/shortlist/screen.py` — `_print_table` (lines 88-115), `_print_plain` (120-125), `_card_dict` (276-286), `_write_csv` (312-320)
- Modify: `src/shortlist/scout/report.py` (line 18)
- Test: `tests/test_card_dict_abstention.py` (JSON), `tests/test_scoring.py` (CSV alignment)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_card_dict_abstention.py`:

```python
def test_card_dict_includes_risk():
    from shortlist.models import ScoreCard
    from shortlist.screen import _card_dict
    card = ScoreCard(
        ticker="T", composite=55.0, quality=50.0, moat=50.0, growth=50.0,
        momentum=50.0, value=None, opportunity=50.0, insider=50.0, risk=100.0,
    )
    d = _card_dict(card)
    assert d["risk"] == 100.0
```

Add to `tests/test_scoring.py` (CSV header/row alignment):

```python
def test_csv_has_aligned_risk_column(tmp_path):
    import csv, dataclasses
    from shortlist.screen import _write_csv
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10,
                            sic=None)
    card = score(m, rc)
    path = tmp_path / "out.csv"
    _write_csv([card], str(path))
    rows = list(csv.reader(path.open()))
    header, row = rows[0], rows[1]
    assert "risk" in header
    assert row[header.index("risk")] == str(card.risk)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_card_dict_abstention.py::test_card_dict_includes_risk tests/test_scoring.py::test_csv_has_aligned_risk_column -v`
Expected: FAIL — `KeyError: 'risk'` in both.

- [ ] **Step 3: Add risk to `_card_dict`**

In `src/shortlist/screen.py`, in `_card_dict`, extend the base dict to include risk alongside the other sub-scores:

```python
        "opportunity": c.opportunity, "insider": c.insider,
        "risk": c.risk,
        "upside_to_target": round(up, 3) if up is not None else None,
```

- [ ] **Step 4: Add risk to `_write_csv`**

In `_write_csv`, add `"risk"` to the header after `"insider"`, and `d["risk"]` to the row in the same position:

```python
        w.writerow(["rank", "ticker", "composite", "quality", "moat", "growth",
                    "momentum", "value", "opportunity", "insider", "risk",
                    "upside_to_target", "gates", "scored", "sic_bucket"])
        for i, c in enumerate(cards, 1):
            d = _card_dict(c)
            w.writerow([i, d["ticker"], d["composite"], d["quality"], d["moat"],
                        d["growth"], d["momentum"], d["value"], d["opportunity"],
                        d["insider"], d["risk"], d["upside_to_target"],
                        "|".join(d["gates"]), d["scored"], d["sic_bucket"]])
```

- [ ] **Step 5: Add risk to the Rich + plain tables**

In `_print_table`, add a `("Risk", "right", 5)` column after `("Insdr", ...)` in `_cols`, and `_f(c.risk)` after `_f(c.insider)` in `table.add_row`:

```python
        ("Insdr",   "right", 5),
        ("Risk",    "right", 5),
        ("Upside",  "right", 6),
```

```python
            _f(c.quality), _f(c.moat), _f(c.growth), _f(c.momentum), _f(c.value), _f(c.insider),
            _f(c.risk),
            f"{up*100:.0f}%" if up is not None else "-",
```

In `_print_plain`, add `RISK` to the header and `_f(c.risk)` to the row:

```python
    print(f"{'#':>2} {'TICK':<6} {'COMP':>5} {'QUAL':>5} {'MOAT':>5} {'GRW':>5} "
          f"{'MOM':>5} {'VAL':>5} {'INSD':>5} {'RISK':>5}  FLAGS")
    for i, c in enumerate(cards, 1):
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.growth):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5} {_f(c.risk):>5}  {_flags_cell(c)}")
```

- [ ] **Step 6: Add risk to the scout report**

In `src/shortlist/scout/report.py`, line 18-19, add risk to the axis line:

```python
        lines.append(f"   Q{_n(c.quality)} M{_n(c.moat)} G{_n(c.growth)} "
                     f"Opp{_n(c.opportunity)} Ins{_n(c.insider)} Rsk{_n(c.risk)}")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_card_dict_abstention.py::test_card_dict_includes_risk tests/test_scoring.py::test_csv_has_aligned_risk_column -v`
Expected: PASS

- [ ] **Step 8: Verify coverage still excludes risk (no new gap reported)**

Run: `uv run pytest tests/test_coverage.py tests/test_coverage_abstention.py -q`
Expected: PASS. `coverage._SUBSCORE_FIELDS` is intentionally unchanged (risk stays out), so a null risk is never reported as a coverage gap.

- [ ] **Step 9: Commit**

```bash
git add src/shortlist/screen.py src/shortlist/scout/report.py tests/
git commit -m "feat(output): surface risk sub-score in json, csv, tables, scout report"
```

---

## Task 6: Full regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all pass (was 404 passed, 3 skipped before; now higher pass count with the new tests, still 3 skipped, 0 failed).

- [ ] **Step 2: Manual smoke — demo run shows a Risk column**

Run: `uv run shortlist --demo`
Expected: the table renders with a `Risk` column; no crash. (Demo/mock metrics may leave risk blank if they carry no vol/drawdown — that's fine.)

- [ ] **Step 3: Manual smoke — JSON contains risk**

Run: `uv run shortlist --demo --json | head -40`
Expected: each card object contains a `"risk"` key.

- [ ] **Step 4: Final commit if any smoke fixes were needed** (otherwise skip)

```bash
git add -A
git commit -m "test: risk axis full-suite green + demo smoke"
```

---

## Self-review notes (coverage vs spec)

- Spec §4.1 (legs/bands) → Task 2 + Task 4.
- Spec §4.2 (composite-only, config-gated, no abstention) → Task 3.
- Spec §3 / §4.3 (composite + confidence/scored invariants) → Task 3 Steps 1 (`test_composite_invariant_when_risk_absent`, `test_confidence_and_scored_invariant_when_risk_absent`).
- Spec §4.4 (all output surfaces; risk OUT of `_SUBSCORE_FIELDS`) → Task 5 (incl. Step 8 coverage check).
- Spec §4.5 (config) → Task 4.
- Spec §5 test plan items 1-9 → Tasks 2-5 tests (direction/clamp #1, composite shift #2, composite invariant #3, confidence/scored invariant #4, sector-neutral+no-abstention #5/§4.2 test, config-gate #6, coverage #7, output surfaces #8, full regression #9 → Task 6).
- Spec §6 (beta, backtest signal wiring) → explicitly out of scope; `backtest/signals.py` left unchanged, `backtest/fit.py` already guards `if k in sub` (no change needed).
