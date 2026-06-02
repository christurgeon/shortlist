# Confidence Surfacing + Safe Tiebreak — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorm checkpoint); pending spec + plan review
**Addresses:** `docs/ASSESSMENT_GAPS.md` §2.4 (coverage isn't folded into ranking confidence)

## 1. Problem

The composite is a coverage-weighted average: when a sub-score has no inputs its
weight is redistributed across the present components (`scoring.py:292-299`). A
`confidence` figure already exists — `pres_w / appl_w`, the fraction of *applicable*
factor-weight that actually had data (`scoring.py:302-305`) — but the ranking sort key
is `(scored, composite)` (`screen.py:48`, `:70`), so confidence never reaches the
ranking. A momentum-only name (confidence ≈ 0.30) scoring 80 on its single present
axis ranks **above** a fully-covered name scoring 78. The human running the screen
cannot tell the sparse 80 from the complete 78 at a glance.

A latent bug compounds this: research/scout selection re-sorts by `composite` **alone**
(`research/__init__.py:63`), discarding even the `scored` ordering the screen
established.

## 2. Goal & non-goals

**Goal:** make coverage **visible** in the ranking surface so a thin high score reads
differently from a complete one, and give research/scout a single consistent ranking
key — **without distorting `composite` or burying genuinely-strong-but-thin
candidates.**

**Decided during brainstorming (expert-reviewed):** three independent expert reviews
converged that the *continuous* tilt originally sketched
(`composite × (1 − α(1−confidence))`) is the wrong tool, for three reasons that this
design treats as binding constraints:

1. **It double-counts absence.** The composite already redistributes weight for missing
   axes, so it already means "the score given what we know." Multiplying by confidence
   penalizes thinness a second time.
2. **The FMP-gating confound.** Low confidence here is frequently FMP's free tier
   402-gating a large-cap (GEV, AXON, ISRG, TMO…), not a company weakness. A continuous
   penalty would demote good blue-chips as a function of API subscription tier — the
   ranking would re-order if you upgraded FMP. That is an artifact, not a signal.
3. **The false-negative asymmetry.** This is a pre-screen feeding a human deep-dive.
   Burying a strong-but-thin deep-value name is invisible and unrecoverable; over-ranking
   a thin name costs the human ~30 seconds. And `opportunity = max(momentum, value)` was
   built specifically to rescue thin deep-value names — a confidence tilt reintroduces
   the averaging-down the repo deliberately rejected.

**This deliverable therefore:**

- **Surfaces** confidence (column) + a `thin` advisory marker.
- Adds confidence as a **tiebreaker only** — composite always dominates; confidence
  decides exact ties.
- Fixes the `enrich()` re-sort to use the same ranking key.

**Non-goals (explicitly deferred):**

- **No continuous multiplicative tilt** and **no demote-only confidence floor.** Both
  reorder names on coverage, which needs a coverage-vs-forward-returns backtest to
  justify and is confounded by FMP-402 gating today. Deferred until the backtest exists
  (`ASSESSMENT_GAPS.md` §2.1).
- **No change to `passed` / `scored` / gates.** Confidence changes *where a name sorts
  among ties* and *what is displayed*, never *whether it qualifies*. The validity floor
  (`scored = confidence ≥ min_scored_weight`) already governs eligibility and is
  untouched.
- **No 402-aware confidence.** Distinguishing "missing because FMP gated" from "missing
  because the data doesn't exist" is a component-vs-provider layer mismatch and would
  diverge the two stacks; out of scope (and unnecessary, since we no longer penalize
  low confidence in the sort).

## 3. The no-bury guarantee (the core invariant)

`rank_key = (scored, composite, confidence)`, compared descending. Because tuples
compare left-to-right and **`composite` is rounded to 0.1** (`scoring.py:298`):

- A higher composite **always** outranks a lower one regardless of confidence — so a
  thin 80 still ranks above a complete 78. We never bury a strong-but-thin candidate.
- Confidence only decides when two composites are **exactly equal** (post-rounding),
  where today's order is already arbitrary (stable sort preserves input order). There
  it breaks the tie toward the better-covered name.

This is transitive (a real total order on the triple), unlike an epsilon-band
tiebreaker. It is the back-compat guarantee: ranking is identical to today except that
previously-arbitrary exact ties become deterministic and coverage-aware. A regression
test asserts the thin-80-above-complete-78 property explicitly.

## 4. Design

### 4.1 `ScoreCard.rank_key` — one source of truth

Add a property:

```python
@property
def rank_key(self) -> tuple:
    """Ranking order, descending: scored first, then composite, then confidence as
    a tiebreaker (composite is rounded to 0.1, so confidence only decides exact ties).
    Single source of truth for every sort site (screen, research, scout)."""
    return (self.scored, self.composite, self.confidence)
```

Replace the inline keys at both sort sites and the research re-sort:

- `screen.py:48` (`run`): `cards.sort(key=lambda c: c.rank_key, reverse=True)`
- `screen.py:70` (`run_harness`): same
- `research/__init__.py:63`: `ranked = sorted(cards, key=lambda c: c.rank_key, reverse=True)`

Scout inherits automatically: it renders `run_harness`'s sorted list (`report.py:14`)
and selects via `enrich` (`daily.py`), both now keyed on `rank_key`.

### 4.2 `ScoreCard.thin` — display advisory

Add a field (appended last, mirroring how `risk` and the sector fields were appended,
so positional construction is unaffected):

```python
    thin: bool = False
```

Computed in `score()` after `confidence`, config-gated:

```python
    thin_below = (config.get("ranking") or {}).get("thin_below")
    thin = thin_below is not None and confidence < thin_below
```

`thin` is **display-only**: it is derived from `confidence`, never feeds `rank_key`,
`passed`, `composite`, or `scored`. When `ranking.thin_below` is absent (minimal
configs, backtest dicts), `thin` is always `False` — a pure no-op.

### 4.3 Surfacing

- **JSON** (`_card_dict`, `screen.py`): `confidence` is already emitted; add `"thin": c.thin`.
- **CSV** (`_write_csv`): add a `confidence` column (header + row, in lockstep).
- **Rich + plain tables** (`_print_table`, `_print_plain`): add a `Conf` column showing
  `confidence` (formatted to 2 decimals).
- **`_flags_cell`** (`screen.py:74-76`): append `"thin"` after gates+flags when
  `c.thin`, so the existing Flags column carries the marker without polluting the
  `flags` list (which stays metric-advisory-only).
- **Scout report** (`scout/report.py`): append confidence (and a `thin` marker) to the
  axis line.

### 4.4 Config

```yaml
ranking:
  thin_below: 0.5   # mark a card "thin" when confidence < this (display advisory only).
                    # Omit/null to disable. Does NOT affect sort, passed, or composite.
```

`0.5` ≈ "less than half the applicable factor weight is present"; a one-axis name
(~0.30) and a weak two-axis name are marked thin, a solid multi-axis name is not.
Tunable; display-only so miscalibration is harmless.

## 5. Testing

1. **No-bury / tiebreak (core):** a card with composite 80, confidence 0.30 ranks
   **above** a card with composite 78, confidence 1.0 (composite dominates). Two cards
   with **equal** composite sort by confidence (higher first). A not-`scored` card ranks
   below a `scored` card regardless of composite.
2. **`rank_key` is a pure total order:** unit-test the property returns
   `(scored, composite, confidence)`.
3. **`thin` threshold:** `confidence < thin_below` → `thin True`; `≥` → `False`;
   `ranking` block absent → `thin` always `False` (no-op).
4. **`thin` is inert:** a thin card with the same composite/confidence has identical
   `rank_key`, `passed`, and `composite` to a non-thin one — thin never changes order or
   eligibility.
5. **Surfacing:** `thin` in JSON; `confidence` column present and value-aligned in CSV;
   `thin` token appears in `_flags_cell` when set.
6. **`enrich` uses `rank_key`:** research selection orders by `(scored, composite,
   confidence)`, not `composite` alone (regression for the `research/__init__.py:63`
   bug).
7. **Back-compat:** full existing suite stays green; the two screener sort sites produce
   the same order as today except for previously-arbitrary exact ties.

## 6. Future work (out of scope here)

- **Continuous confidence tilt / demote-only floor:** revisit only with a
  coverage-vs-forward-returns backtest (`ASSESSMENT_GAPS.md` §2.1) and a 402-aware
  confidence that excludes data-access gaps from the penalty, so we don't demote
  FMP-gated large-caps. Until then, surface-don't-bake-in is the rigorous stance.
- **402-aware confidence** for the displayed figure (distinguish gated from genuinely
  absent), if the surfaced number proves misleading on gated large-caps.
