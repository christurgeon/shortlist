# Making the evaluator's guards unbypassable — design

**Status:** DRAFT, pending adversarial review.
**Date:** 2026-08-04.
**Scope:** `scout/validate.py` only (plus its `daily.py` call site). No verdict-rule changes.

## 1. The problem, from four observed failures

On 2026-08-03 a number was published in four artifacts (`docs/EVALUATOR_CORRECTNESS.md`,
`CLAUDE.md`, `TODO.md`, commit `35389ac`) and retracted the next day: a claimed **12pp
per-bucket measurability asymmetry on `8k-neg`**, which was an artifact of that cohort being
half price-covered. Post-mortem in `docs/audits/2026-08-03-evaluator-rederivation.md` §4.

The interesting part is that **a guard already existed and fired correctly** — `8k-neg`'s
measurable fraction was 0.548 against a 0.90 floor, and `_suppress_level` blanked the parent
alpha. The number got quoted anyway. Three distinct reasons, each mechanically fixable:

**(P1) The suppressed set is incomplete.** `_ABSOLUTE_DOUBLE_SORT_LEGS = ("high_ir", "low_ir")`.
`high_frac`/`low_frac` are equally cohort-level statistics of a rejected cohort, and they
survived suppression. They were the numbers quoted.

**(P2) The guard is a convention, not a mechanism.** Suppression lives in
`attach_double_sort`, whose docstring says every assignment must go through it. The
re-derivation script (`rederive.py`) called `double_sort()` directly and never called the
helper — so on a cohort whose parent verdict *was* suppressed, the ds dict still carried
`high_ir 0.364`, `low_ir 0.185`, `level_suppressed False`. The convention was violated by its
own author within hours of writing it. Anything reachable only by remembering to call a
helper will eventually be reached without it.

**(P3) The floor's reason is misattributed.** The note reads "measurable fraction 0.55 <
floor", which reads as *survivorship* — the failure mode the repo has documented at length,
and the one the double-sort spread is explicitly claimed to survive ("attrition cancels
between two identically-measured buckets"). So a low fraction gets filed as "the known
attrition problem, which the spread is immune to." For `8k-neg` the fraction was dominated by
**events whose ticker had no price series at all**, which is not attrition and which nothing
cancels. The guard fired; its label pointed at the wrong cause; the exemption was applied
where it did not hold.

A fourth, separate failure the same week: a CI comparison (1.20× / 0.94× / 1.39×) was quoted
off **B=60** bootstrap replicates, where the 90% interval width carries ~11% Monte-Carlo
error. The differences were inside their own noise. **(P4) A bootstrap reports an interval
but not the interval's own uncertainty.**

**Non-goal, stated because it is the tempting answer:** more review. Two adversarial reviewers
ran on this work; each was wrong about something itself; neither caught the coverage problem.
What caught it was checking a precondition before quoting a number. Scrutiny was not the
missing ingredient, so adding scrutiny is not the fix.

## 2. Proposed changes

### C1 — `high_frac`/`low_frac` join the suppressed set (fixes P1)

Add them to `_ABSOLUTE_DOUBLE_SORT_LEGS`. They are per-bucket levels of a cohort the floor
rejected, exactly like `high_ir`/`low_ir`. The **spread** keeps its exemption (§3).

### C2 — `double_sort` self-suppresses; the helper stops being load-bearing (fixes P2)

`double_sort` gains optional `measurement` + `prereg` parameters. When given, it runs
`_floor_failures` itself and returns an already-suppressed dict — so there is no window in
which an unsuppressed object exists for a caller to mishandle. `attach_double_sort` keeps
handling only the *parent*-suppressed case (a fact `double_sort` cannot know).

When `measurement`/`prereg` are omitted the behaviour is today's, so existing tests and any
caller that only wants the raw statistic are unaffected — but `daily.py` passes them, and the
docstring states plainly that omitting them yields an **unguarded** result that must not be
quoted.

*Deliberate limitation, stated rather than hidden:* this narrows the bypass, it does not
close it, because the parameters are optional. Making them mandatory would be the stronger
fix; §4 records why that is not proposed yet.

### C3 — separate "no series at all" from attrition, and say which (fixes P3)

`measure_cohort` already sees the distinction at `validate.py:158`
(`hist_by_ticker.get(tk)` → `None`) and discards it. The backfill JSONLs already record it
(`meta.non_measurable_reason: no_price_series`, 3876 of 11612 rows for `8k-neg`).

- `MeasuredEvent` gains `absent_series: bool` (no `PriceHistory` for that ticker at all).
- `CohortMeasurement` gains `n_absent_series`.
- `_SUPPRESSION_NOTE` and the floor note name the split: *"measurable fraction 0.55 < floor —
  of the shortfall, N events had NO price series (coverage) and M had a series but no return
  at the horizon (attrition/immaturity)."*

This is **reporting only**; no threshold, no new suppression trigger, so it adds no
unregistered inference parameter. Its whole job is to stop a coverage failure being read as
an attrition failure and thereby inheriting attrition's exemption.

### C4 — the bootstrap reports its own Monte-Carlo error (fixes P4)

Compute each CI from the first and second half of the replicates as well as the whole, and
report `ci_mc_error` = the larger endpoint discrepancy between halves. Costs nothing (the
replicates already exist). A reader comparing two intervals can then see when the difference
is inside the noise. **Reporting only** — no automatic refusal, since the threshold for "too
noisy to compare" is context-dependent and inventing one post-hoc is the error this whole
document exists to prevent.

## 3. What is deliberately NOT changed

- **The spread keeps its suppression exemption.** The 2026-08-03 re-derivation measured
  per-bucket fractions on every ≥95%-covered cohort at ≤3.3pp apart, which supports the
  cancellation argument. Removing the exemption would need evidence against it, and there is
  none.
- **No verdict rule changes.** No threshold moves. No new pre-registered parameter.
- **No RNG change** (still deferred, see `EVALUATOR_CORRECTNESS.md` §5).

## 4. Open question for the reviewer

C2 makes `measurement`/`prereg` optional for back-compat. **Should they be mandatory?** That
would truly close P2 rather than narrowing it, at the cost of touching every existing
`double_sort` call site and test. The argument for optional is back-compat; the argument
against is that this document's own thesis is that opt-in guards do not guard, and an
optional parameter is an opt-in guard. I lean mandatory and want the reviewer to push on it.

## 5. Testing

- C1: a floor-rejected cohort returns `high_frac`/`low_frac` as `None`.
- C2: calling `double_sort(measurement=…, prereg=…)` on a below-floor cohort returns an
  already-suppressed dict **without** `attach_double_sort` being called — i.e. the exact
  `rederive.py` bypass, pinned as a test.
- C3: a cohort with absent-series events reports `n_absent_series` correctly and the note
  names both components; a cohort with full coverage reports zero.
- C4: `ci_mc_error` is larger at small `n_boot` than at large `n_boot` on the same cohort.
- Gate: exact CI commands (`uv sync --extra edgar --extra scout`, `uv run ruff check src tests`,
  `uv run pytest -q`). Baseline 2286 passed / 3 skipped.
