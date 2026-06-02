# Value-tilted scoring: replace `opportunity` with independent value + momentum

**Date:** 2026-06-02
**Status:** Approved design (post consultation review)
**Scope:** `scoring.py`, `config.yaml`, `backtest/signals.py`, tests, docs

## 1. Goal

Tilt the screener's **ranking** toward undervalued companies. The universe and
the hard gates stay the same; cheaper names should rise, and momentum names
still appear — just lower. This is a ranking tilt, not a filter.

## 2. Problem

Today value and momentum are collapsed into a single `opportunity` component
(`scoring.py:299-300`, weight `0.27`) via `opportunity = max(momentum, value)`.
The `max()` exists to let a name "qualify on either axis." That symmetry is
exactly what blocks a value tilt: raising the opportunity weight rewards a
momentum darling *identically* to a cheap stock. To privilege value we must give
value its own weight, which means breaking the `max()`.

## 3. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Mechanism | **Hard-replace** `opportunity` with two independent components, `value` and `momentum` | Cleanest, directly interpretable weights; user wants the tilt to be the default behavior, not opt-in |
| Weights | quality 0.18 · moat 0.18 · growth 0.135 · **value 0.22** · **momentum 0.08** · insider 0.135 · risk 0.10 | Value pulls ~3× momentum; keeps momentum (the only backtest-validated, never-gated axis) a meaningful tiebreaker |
| `ScoreCard.opportunity` | **Retain as display-only** `= max(momentum, value)` | Keeps JSON/CSV schema, scout report, and most test constructors stable; no longer feeds composite |
| Confidence floors | **Lower** `min_scored_weight` 0.34 → 0.30, `thin_below` 0.5 → 0.40 | Splitting the component drops confidence for FMP-gated names; without this, value-tilted names can fall below the `scored` floor and drop out of the ranking — the opposite of the goal (review C2) |
| Value-trap guard | **Include** a soft advisory flag now | Value is now load-bearing; with FMP gated it collapses to a 2-of-4-leg average prone to flagging falling knives (review S1) |

### 3.1 Weights — note on the bloc

The six non-risk components sum to **0.93** (was 1.0). This is **cosmetic**: the
composite is a normalized weighted average (`num/den` over present parts,
`scoring.py:331-336`), confidence is `pres_w/appl_w` (`scoring.py:339-342`), and
`scored`/`thin`/backtest `_normalize` are all ratio-based — verified in review
that no code path reads the absolute weight sum.

One honest caveat: the price/value bloc is now `value + momentum = 0.30` vs the
old `opportunity = 0.27`. That is a deliberate, mild (~0.03) increase in the
price/value bloc's share *relative to the fundamental axes* — chosen over the
strict-envelope alternative (value 0.22 + momentum 0.05 = 0.27) to keep momentum
non-inert. This is the user's explicit choice, recorded here so it isn't
mistaken for an accident.

## 4. Composite changes (`scoring.py`)

1. **Delete** the `max()` collapse (`scoring.py:299-300`). Compute `mom` and
   `val` as today, but do **not** derive an `opp` for the composite.
2. The `components` list (`scoring.py:313-319`) replaces the single
   `("opportunity", opp, w["opportunity"], ("momentum","value"))` entry with two:
   - `("momentum", mom, w["momentum"], ("momentum",))`
   - `("value", val, w["value"], ("value",))`
   Both feed composite (`parts`) and confidence (`appl_w`/`pres_w`) independently.
3. Momentum legs are never masked → `momentum` is always applicable. Value legs:
   only `fcf_yield` is masked (financials); the other three value legs are not,
   so `value` stays applicable for every v1 bucket (it can still abstain
   "missing" if too few legs are present — the desired honest-confidence signal).
4. `ScoreCard.opportunity` is still populated, as a **display-only** convenience:
   `opportunity = max(p for p in (mom, val) if p is not None)` (None if both
   None). Update the field comment in `models.py:118` to say "display-only;
   does not feed the composite."

## 5. Confidence floors (`config.yaml`)

- `validity.min_scored_weight`: `0.34` → `0.30`
- `ranking.thin_below`: `0.5` → `0.40`

Rationale: with six components, `value` (the most FMP-gated sub-score: PEG and
analyst-target upside both require FMP) counts toward confidence independently.
Under the old `max()`, `opportunity` was "present" whenever momentum was present
(momentum is never gated), so a value-gated name kept full confidence. After the
split, the same name loses ~0.28 confidence purely from the schema change. With
the floors left untouched this can push value-tilted names — **especially
financials, where `moat` is already masked** — below `scored = confidence ≥
0.34`, and `passed = not gates and scored` plus `rank_key = (scored, composite,
confidence)` means a not-scored name is excluded from passing and sorted below
every scored name. Lowering the floors restores headroom. The new values are
priors, not fitted; revisit once snapshot history enables backtesting.

## 6. Value-trap advisory flag (`scoring.py`, `config.yaml`)

A **soft, non-disqualifying** flag (parallel to `crowded_short`) — it never
affects `passed`/`composite`/`scored`. It marks names that look cheap but carry
weak fundamentals.

### 6.1 Definition

Level-based proxy (we have sub-score *levels*, not a deterioration *trend* —
without snapshot history there is no time-series; this is labeled honestly as a
prior):

```
value_trap = (value ≥ min_value_score)
             AND ( (quality is not None AND quality < max_quality_score)
                   OR (growth is not None AND growth < max_growth_score) )
```

None-safe: if `value` is None the flag never fires. The quality/growth legs are
each guarded so a missing leg cannot trip it alone.

### 6.2 Config (ships ON — advisory-only, cannot harm the composite)

```yaml
flags:
  value_trap:
    min_value_score:   60   # "cheap" floor on the value sub-score (0-100)
    max_quality_score: 40   # below this, quality is "weak"
    max_growth_score:  40   # below this, growth is "weak"
```

All three are **unfitted priors** — backtest before trusting.

### 6.3 Wiring

`check_flags(m, f)` operates on `StockMetrics` and has no access to sub-scores,
so `value_trap` is computed inside `score()` after `val`, `q`, `gr` are
available, then appended to the `flags` list. Keep `check_flags`'s signature
unchanged; do not thread sub-scores into it.

## 7. Backtest alignment (`backtest/signals.py`)

`SnapshotSignalSource` axis list (`signals.py:87`) changes from
`("quality","moat","growth","opportunity","insider")` to
`("quality","moat","growth","value","momentum","insider")` so the replayed axes
match the live composite. The `fit.py` prior flows from `config["weights"]`, so
it stays consistent automatically once config carries `value`/`momentum`; the
only stale reference is the hard-coded test prior (§9).

## 8. Out of scope (deferred)

- Value-band threshold tuning (`fcf_yield`/`pe_vs_history`/`peg`/`upside`).
- Recalibrating the value-trap thresholds against forward returns (needs
  snapshot history; same guard as the rest of the backtest fundamental path).
- Any change to the hard gates.

## 9. Tests

**Update (will break, by design):**
- `tests/test_scoring.py` `test_opportunity_takes_the_stronger_axis` — rewrite:
  the composite no longer uses `max()`; assert value and momentum enter the
  composite with their own weights. Keep an assertion that the *display field*
  `opportunity == max(momentum, value)`.
- `tests/test_scoring.py:333-346` (`_risk_config`, the `55.0`/`50.0` arithmetic)
  — update to the six-component schema. The x0.9 risk-absent invariant is
  intentionally retired by the split; re-pin the new composite arithmetic.
- `tests/test_backtest_signals.py:69` and `tests/test_backtest_fit.py:5` —
  replace `"opportunity"` in the weight dicts with `"value"`/`"momentum"`.

**Add:**
- Composite arithmetic test: a name with distinct momentum/value scores produces
  the expected weighted composite (value weighted > momentum).
- **C2 regression:** a value-gated financials name (moat masked, value missing)
  still `scored` / `passed` under the new floors. This is the guardrail the
  floor change exists for.
- Value-trap flag: fires when cheap+weak; does not fire when cheap+strong or
  when value is None; never changes `passed`/`composite`.

## 10. Docs

- `CLAUDE.md` — "Screener data flow": replace the `opportunity = max(...)`
  description with the split; update the default-weights line; note the new
  `value_trap` advisory and the floor changes.
- `README.md` — any mention of the opportunity axis / weights.
- `config.yaml` — weight-block comments; new `flags.value_trap` block;
  `validity`/`ranking` comments for the floor changes.

## 11. Output-surface relabeling (review N1)

`opportunity` is now decorative but still rendered. To avoid the display
contradicting the ranking:
- `screen.py:89` table title "Moat + opportunity screen" → "Moat + value screen".
- `scout/report.py:22` "Opp" — leave as-is; it now reflects max(mom,val), which
  is still a fair one-glance summary, and value/momentum are available in the
  full card. No change this pass.

(These are cosmetic; they must not change any score, gate, or sort.)
