# Risk Sub-Score (7th Axis) — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorm checkpoint); pending spec + plan review
**Addresses:** `docs/ASSESSMENT_GAPS.md` §2.9 (risk metrics computed but never scored)

## 1. Problem

The harness computes two backward-looking risk metrics per ticker — `realized_vol`
(annualized stdev of daily returns) and `max_drawdown` (trailing ~1y peak-to-trough,
a negative fraction) — and bridges them onto `StockMetrics` (`data/bridge.py:91-92`).
**Neither is scored.** The composite blends six sub-scores (quality, moat, growth,
opportunity, insider) and is blind to how violently a name moves or how deep its
recent drawdown is. Two businesses with identical fundamentals but radically
different volatility rank identically.

`ASSESSMENT_GAPS.md` §2.9 names this gap and proposes "add a risk axis **or** a
volatility/drawdown soft gate." This deliverable takes the **axis** path: a 7th
weighted sub-score.

`beta` exists on the harness `Profile` (`data/models.py:25`) but is **not** a
`StockMetrics` field and **not** bridged. It is **out of scope** here (see §6).

## 2. Goal & non-goals

**Goal:** turn the two already-computed risk metrics into a 7th weighted sub-score
that tilts the composite toward lower-volatility, shallower-drawdown names, **without
disturbing the plain-screener path or the existing back-compat guarantees.**

**Decided during brainstorming:**

- Composite role = **7th weighted component** (not a penalty multiplier, not a
  soft flag), but a **composite-only tilt**: risk feeds the composite **and is
  deliberately excluded from the `confidence` / `scored` / coverage / abstention
  accounting** (see §4.3 — this is the fix for the confidence regression the
  adversarial review caught, and the reason risk is *not* routed through the
  `components`/`_eval_subscore` confidence path the other axes use).
- Legs = **`realized_vol` + `max_drawdown`**. Both already bridged; **no new data
  plumbing**.
- Weight = **0.10**, sourced by **proportional rescale** of the other five (each
  × 0.9). New vector: quality 0.18 / moat 0.18 / growth 0.135 / opportunity 0.27 /
  insider 0.135 / risk 0.10 (sum 1.0).
- Risk is **sector-neutral** — applicable to every bucket including
  financials/REITs/insurers (vol and drawdown are well-defined for all of them).
  It is **never** added to `sectors.masked_legs` / `masked_gates`.
- Risk is **config-gated**: the component activates only when `weights.risk` **and**
  both risk thresholds are present in config. Absent any of them (minimal unit-test
  configs, the backtest's threshold-only dict) the risk component is skipped
  entirely — no `KeyError`, no composite change.

**Non-goals (explicitly deferred):**

- **No `beta` leg.** Adding it needs a new `StockMetrics` field + a `bridge.py`
  mapping from `Profile.beta`. Clean follow-up; the leg machinery already
  redistributes weight when a leg is absent, so beta drops in later without
  reshaping anything.
- **No weight fitting.** 0.10 and the two bands are **unfitted priors**, in the
  same spirit as the existing `weights:` block (`config.yaml:43-44`). They are
  flagged for the backtest harness, not asserted as validated.
- **No risk gate.** §2.9 offered "axis *or* soft gate"; we ship the axis only. A
  vol/drawdown soft gate or `high_risk` flag remains available as a separate future
  item.
- **No change to the screener (sync) data path.** `realized_vol`/`max_drawdown`
  come from the harness Yahoo OHLCV source; the plain screener leaves them `None`.
  That asymmetry is intended and preserved (see §4).

## 3. The proportional-rescale invariant (why sourcing matters)

The other five weights are multiplied by a single scalar `k = 0.9`. The composite
is a weight-redistributing average (`scoring.py:275-278`):

```
composite = sum(s * w for present components) / sum(w for present components)
```

When risk is **absent** (every plain-screener name, plus any harness name missing
the OHLCV series), risk is excluded from both sums. The remaining five weights are
all scaled by the same `k`, so `k` cancels in numerator and denominator:

```
composite_after  = (k·Σ sᵢwᵢ) / (k·Σ wᵢ)  =  (Σ sᵢwᵢ) / (Σ wᵢ)  =  composite_before
```

**Therefore: screener-path composites are bit-identical to the pre-change scorer.**
Only harness names that actually have a risk sub-score move. This mirrors the
sector-aware work's "bit-identical no-op for the back-compat case" guarantee and is
the decisive reason for proportional rescale over carving the weight out of
opportunity. (Carving would also have been defensible on double-counting grounds —
risk's legs share the price series with the momentum leg — but that is an editorial
bet better resolved by the backtest than baked into the prior.)

**The invariant must also cover `confidence`/`scored`/`passed`, not just the
composite.** This is the blocker the adversarial review found. `confidence =
pres_w / appl_w` over the *applicable* components (`scoring.py:281-284`); `scored =
confidence >= min_scored_weight` for non-`unknown` buckets, and `passed = not gates
and scored`. Risk is sector-neutral → always *applicable* → if it were added to the
`components` list it would always sit in `appl_w` (the denominator), but on the
screener path it is *missing* so it never reaches `pres_w` (the numerator).
Confidence would therefore drop for every partially-covered name, and for
known-bucket names sitting just above the `0.34` floor it would flip
`scored` **True → False** — silently dropping real broker/insurer/REIT names from
the shortlist (worked example: a financials-bucket name with only `opportunity`
present goes `0.30/0.80 = 0.375` → `0.27/0.82 = 0.329`, crossing the floor). That is
the opposite of the back-compat guarantee. **Resolution: risk is kept out of the
`components` list entirely** — it is computed and blended into the composite
separately (§4.2), and `confidence`/`scored`/coverage/abstention are computed over
the original five components exactly as before. Both the composite invariant and the
`confidence`/`scored` invariant are **required regression tests** (§5).

## 4. Design

### 4.1 Legs and direction

Both legs score **higher when safer**, via the existing inverted-band convention
(`_norm` with `lo > hi`, same mechanism as `debt_to_equity: [3.0, 0.0]`):

| Leg | `StockMetrics` field | Band `[lo, hi]` | Reading |
|-----|----------------------|-----------------|---------|
| `realized_vol` | `m.realized_vol` (annualized, ≥0) | `[0.45, 0.15]` | 15% vol → 100; 45% vol → 0 |
| `max_drawdown` | `m.max_drawdown` (negative fraction) | `[-0.50, -0.10]` | −10% → 100; −50% → 0 |

`_norm` clamps outside the band, so a −70% drawdown floors at 0 and a 5% vol caps
at 100. Bands are config-driven (`config.yaml: thresholds`) and labelled unfitted.

### 4.2 Scoring path

Risk follows the **`insider_score` standalone precedent** (a sector-neutral helper),
**not** the `sub()`/`components` confidence path. Add a `risk_score(m, t)` helper that
`_norm`s the two legs and `_avg`s them — directly analogous to `insider_score`:

```python
def risk_score(m, t):
    return _avg([
        _norm(m.realized_vol, *t["realized_vol"]),   # band inverted: low vol -> high
        _norm(m.max_drawdown, *t["max_drawdown"]),   # band inverted: shallow -> high
    ])
```

In `score()`, compute risk **only when config-gated** and blend it into the composite
*after* the five-component `num/den`, without touching the confidence sums:

```python
risk_on = ("risk" in w) and ("realized_vol" in t) and ("max_drawdown" in t)
ri = risk_score(m, t) if risk_on else None

# composite over the five components (unchanged), then add risk as a tilt:
parts = [(s, weight) for _, s, weight, _ in components if s is not None]
if ri is not None:
    parts.append((ri, w["risk"]))
num = sum(s * weight for s, weight in parts)
den = sum(weight for _, weight in parts)
composite = round(num / den, 1) if den else 0.0
```

Properties:

- **Sector-neutral, never masked:** `risk_score` does not consult `leg_applicable`,
  so it is computed identically for every bucket (incl. financials/REITs/insurers),
  matching `insider_score`. (Vol/drawdown are well-defined for all sectors.)
- **Composite-only:** the `components` list (used for `confidence`/`scored`/`applic`)
  is **unchanged** — still the original five. Risk never enters `appl_w`/`pres_w`, so
  `confidence`/`scored`/`passed` are bit-identical to the pre-change scorer whether or
  not risk has data. This is the §3 confidence invariant.
- **No abstention entry:** risk is excluded from the `abstentions` list. Its absence
  on the screener path is an *engine-level* structural fact (no OHLCV source), not a
  per-name data gap, so recording a `missing` abstention on every screener name would
  be noise that contradicts coverage (§4.4). Risk simply doesn't contribute to the
  composite when absent — the §3 invariant makes that a no-op.
- **Config-gated:** when `risk_on` is `False` (minimal configs), risk is skipped and
  nothing references `w["risk"]`/`t["realized_vol"]` — no `KeyError`.

### 4.3 Back-compat: both invariants when risk is absent

The sector-aware contract says the `unknown` bucket must be bit-identical to the
pre-sector scorer, and adding a 7th axis must not regress any existing name. With the
composite-only design (§4.2), both hold for the **risk-absent** case (all screener
names + harness names lacking OHLCV):

- **Composite** — risk is excluded from `parts`, the five rescaled weights all carry
  the same `×0.9` scalar, and it cancels in `num/den` (§3) → identical composite.
- **`confidence`/`scored`/`passed`** — risk is not in `components`, so `appl_w`,
  `pres_w`, `applic`, and the `abstentions` list are untouched → identical confidence,
  identical `scored`, identical `passed`, identical sort order.

For a name **with** risk data (harness path), the composite *does* move (it now
carries a 0.10 risk tilt) — the intended new behavior — while `confidence`/`scored`
still reflect only the original five components. The existing sector and scoring
regression suites stay green unchanged; two new tests assert each invariant
explicitly (§5).

### 4.4 `ScoreCard` and output surfaces

The adversarial review found ~7 axis enumerators, not the 3 I first listed. Full set:

- **`ScoreCard`** (`models.py`) gains `risk: Optional[float] = None`, **appended after
  `abstentions`** (the last field), so positional construction through the existing
  fields is unaffected — the same pattern `sic_bucket`/`confidence`/`scored`/
  `abstentions` followed. (Verified: no test constructs `ScoreCard` positionally past
  `insider`.) `score()` passes `risk=_round(ri)`.
- **`coverage._SUBSCORE_FIELDS`** (`coverage.py:26`): risk **stays OUT**. It is
  engine-dependent (the screener has no OHLCV), so its absence is not a per-name
  coverage gap; including it would flag a "gap" on every screener name and contradict
  the intended asymmetry (§2). This is consistent with risk emitting **no** abstention
  either (§4.2) — coverage and abstentions agree: neither reports risk-absence.
- **JSON card dict** (`screen.py:277-279`): add a `risk` key alongside the other
  sub-scores.
- **CSV** (`screen.py:312-319`): add a `risk` column. Header list and the positional
  row list are hand-maintained and **must be edited in lockstep**; a test asserts
  header/row alignment (§5).
- **Rich table + plain table** (`screen.py:112`, `:123-125`): add a `risk` column to
  both the `_cols` spec and the row formatting.
- **Scout report** (`scout/report.py`): add risk to the human-facing axis line.
- **Backtest** — `backtest/signals.py:87` (axis list for `SnapshotSignalSource`) is
  **left unchanged**: emitting risk as a backtestable signal is §6 future work, not
  this deliverable. `backtest/fit.py:29` (`_composite`) already guards `if k in sub`,
  so adding a `risk` weight does **not** break fitting (risk-absent snapshots skip the
  key in both numerator and denominator) — verified, no change needed.

### 4.5 Config

```yaml
thresholds:
  # Risk (inverted: safer -> higher score). Unfitted prior — backtest before trusting.
  realized_vol:   [0.45, 0.15]   # annualized stdev of daily returns
  max_drawdown:   [-0.50, -0.10] # trailing ~1y peak-to-trough (negative)

weights:
  quality:      0.18   # was 0.20 (× 0.9 on risk-axis introduction)
  moat:         0.18   # was 0.20
  growth:       0.135  # was 0.15
  opportunity:  0.27   # was 0.30
  insider:      0.135  # was 0.15
  risk:         0.10   # NEW — vol/drawdown; unfitted prior, proportional rescale
```

The existing "defensible prior, not fitted" NOTE is extended to cover risk.

## 5. Testing

1. **Direction:** low vol + shallow drawdown → high risk sub-score; high vol +
   deep drawdown → low. `_norm` clamping at both band ends (incl. drawdown beyond
   −50% flooring at 0, vol below 15% capping at 100).
2. **Composite shift (harness):** a name **with** risk data has a composite that
   reflects the 0.10 risk tilt (differs from the same metrics with risk `None`).
3. **Composite invariant (key test #1):** for metrics with
   `realized_vol = max_drawdown = None`, `score()` composite equals the pre-change
   composite (frozen expected value / old weight math). Proves the ×0.9 cancellation.
4. **Confidence/scored invariant (key test #2 — the blocker regression):** for a
   **known-bucket** (e.g. financials) name with partial coverage that was
   `scored=True`/`passed=True` before, adding the risk axis with risk `None` leaves
   `confidence`, `scored`, and `passed` **unchanged**. Explicitly covers the
   `0.375 → 0.329` flip the review identified, asserting it does **not** happen.
5. **Sector neutrality:** `risk_score` is computed (non-`None`) for a financials/
   REIT/insurer name that has vol+drawdown data — risk is never masked, unlike
   gross_margin/roic/etc. And risk never appears in `abstentions` for any bucket.
6. **Config gate:** `score()` on a minimal config lacking `weights.risk` /
   `thresholds.realized_vol` does **not** raise `KeyError` and produces no risk
   sub-score (regression for `test_scoring.py`'s inline config + the backtest
   threshold-only dict).
7. **Coverage:** risk-absent does **not** appear in `coverage.unavailable` (risk is
   out of `_SUBSCORE_FIELDS`).
8. **Output surfaces:** `risk` present in JSON card dict; CSV header and row aligned
   with risk in the same position (header/row lockstep assertion).
9. **Existing sector + scoring + backtest regression suites stay green** (incl.
   `backtest/fit.py` with a `risk` weight present but absent from snapshot subs).

## 6. Future work (out of scope here)

- **`beta` leg:** add `StockMetrics.beta`, map `Profile.beta` in `bridge.py`, add a
  third inverted risk leg. Weight redistribution already handles its absence.
- **Backtest the weight and bands (elevated priority — known caveat):** the risk
  legs are backward-looking and mean-reverting — trailing vol/drawdown **peak at the
  bottom**, exactly when forward risk/reward is often best — so a naive *positive*
  risk weight can be **anti-predictive at turning points**. We ship the naive version
  at 0.10 as an explicit unfitted prior (consistent with the existing `weights:`
  NOTE), but unlike beta/flags this prior **actively moves harness rankings** (test
  §5.2). Validating the risk sub-score's standalone rank IC and the sign/magnitude of
  the weight is therefore the **first** thing to do once snapshot history activates
  the guarded weight-fitting path (`ASSESSMENT_GAPS.md` §2.1); also wire risk into
  `backtest/signals.py` so it is fittable. If the IC is flat or negative, consider
  scoring drawdown as *recovery off the low* rather than raw trough depth, or demote
  risk to a soft gate. This caveat is the main reason the weight is a modest 0.10 and
  the sourcing is reversible proportional rescale rather than a baked-in carve.
- **Risk soft gate / `high_risk` flag:** the alternative §2.9 path, still open.
