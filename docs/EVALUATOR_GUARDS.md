# Making the evaluator's guards unbypassable — design (revision 2)

> **RETIRED 2026-08-11.** The signal-validation evaluator it describes has been deleted. Kept for the reasoning and measured facts it
> records, not as a description of code that exists. See
> `docs/audits/2026-08-11-scout-retirement.md`.


**Status:** IMPLEMENTED 2026-08-04. Suite **2293 green**, ruff clean.
`642e264` (C1′+C2″) · `fda7c15` (C4′) · `2afa0a9` (C5) · this commit (C3′ core).
**C2′ was CUT** as disproportionate — see §4. **C3′ shipped as its load-bearing slice** — §5.
**Scope:** `scout/validate.py`, its `daily.py` call site, one `report/sections.py` render fix.
No verdict-rule changes.

> **Revision 2 dropped the original C1 as a BLOCKER** — it suppressed the diagnostic and kept
> the statistic that depends on it. Revision 1's C3 predicate was also **inverted**. §7 logs
> both. The single highest-value item (C5) did not exist in revision 1.

## 1. The problem, from observed failures

On 2026-08-03 a claimed **12pp per-bucket measurability asymmetry on `8k-neg`** was published
in four artifacts and retracted the next day; it was an artifact of that cohort being half
price-covered (`docs/audits/2026-08-03-evaluator-rederivation.md` §4).

**A guard already existed and fired correctly** — `8k-neg`'s measurable fraction was 0.548
against a 0.90 floor, and `_suppress_level` blanked the parent alpha. The number was quoted
anyway. Verified causes:

- **P1** The suppressed set is incomplete: `_ABSOLUTE_DOUBLE_SORT_LEGS = ("high_ir","low_ir")`
  (`validate.py:555`); `high_frac`/`low_frac` survive.
- **P2** The guard is a convention. Suppression lives in `attach_double_sort`, whose docstring
  says every assignment must route through it. The re-derivation script called `double_sort()`
  directly and skipped it — **the convention was violated by its own author within hours.**
- **P3** The floor's reason is misattributed: the note says "measurable fraction < floor"
  (`validate.py:671`) and `_SUPPRESSION_NOTE` attributes it wholly to acquisition/delisting
  attrition. `8k-neg`'s shortfall was overwhelmingly *absent inputs*, which nothing cancels —
  so the coverage failure inherited attrition's spread exemption.
- **P4** A bootstrap reports an interval but not the interval's own Monte-Carlo error; a
  1.20×/0.94×/1.39× comparison was quoted off **B=60**, where the differences sat inside noise.
- **P5 (found in review)** The *reason* the unguarded script existed: `validate` has **no
  `--as-of`**. `daily.py:1274` hardcodes `datetime.now(...).date()`. An audit needing a pinned
  `as_of` and a pinned price snapshot therefore *had* to be a scratchpad script — which is by
  construction outside every guard.

**Non-goal, reworded after review:** the fix is not *unstructured re-reading*. Two reviewers
ran on the prior change and neither caught this — but they were reviewing a design, and the
coverage table did not exist in any artifact for them to check. That does not license "review
never helps." It licenses: **prefer guards that are mechanical, and prefer process rules that
are grep-checkable over ones that require someone to notice.** C5 is such a rule.

## 2. C5 — `validate --as-of` (fixes P5) ← highest value, do first

The failure happened in an ad-hoc script. No guard inside `validate.py` covers that path.
Removing the *need* for the script is the most direct prevention.

- Add `validate --as-of ISO`, pinning both the measurement `as_of` **and** the price-cache day
  key (`fetch_history` already day-caches by the `today` string, `prices.py:159`, so a pinned
  run reuses the existing snapshot at no cost).
- `_persist_validate_latest` labels the artifact `source="replay:<iso>"`.
- **`_load_validation_digest` must reject non-live sources.** Without this a backdated replay
  satisfies the 14-day staleness gate and lands in the Telegram digest as a current verdict —
  a live safety hole this change would otherwise open.
- Emit **price coverage as a field** (cohort tickers with no usable series). The coverage table
  that adjudicated the retraction exists nowhere in the evaluator's output today.

Consequence: the sanctioned way to produce an audit number becomes a committed entry point
emitting a persisted artifact — making "every evaluator number in a committed doc traces to a
`validate --json` artifact" a checkable rule rather than a habit.

## 3. C1′ — fix the per-bucket fractions; suppress the SPREAD, not them (replaces C1)

Revision 1 proposed blanking `high_frac`/`low_frac`. **Rejected as a BLOCKER**, for three
reasons that hold up:

1. `_suppress_level`'s rationale is that attrition biases **returns**. A measurable fraction is
   not biased by attrition — it *is* the measurement of it. The cohort's pooled
   `measurable_fraction` is deliberately **not** suppressed and prints in the digest's FRAC
   column; blanking the per-bucket version of a number the same code prints pooled is not a
   principled line.
2. It removes the diagnostic exactly where it matters while **keeping the spread**, whose
   validity that diagnostic exists to test. On `8k-neg` the spread
   (−0.97%/mo, CI [−5.50%, +2.57%]) is fully quotable and renders in the digest —
   `report/sections.py` never reads `level_suppressed` (verified: grep returns nothing).
3. It blanks the calibration data for the pre-registered asymmetry tolerance that `TODO.md`
   already tracks as follow-up work.

Instead:

- **Fix the denominator (F1).** `_frac` (`validate.py:451`) does not filter `immature`, while
  `measurable_fraction()` is mature-only (`validate.py:55`). They are incomparable today —
  the exact trap `backfill.py:509` already carries a `fraction_note` to prevent. Filter
  `immature` from both pools.
- **Emit `n_high_pool`/`n_low_pool`** so denominators are reconstructable (`n_high`/`n_low`
  count a *different*, measurable-only population).
- **Never suppress the fractions.** They are coverage metadata.
- **Apply the already-registered `min_measurable_frac` per bucket.** If either bucket falls
  below it, the "identically-measured buckets" premise is untestable, so
  `spread_alpha_monthly`/`spread_ci` become non-quotable and `level_suppressed` is set. This
  reuses a registered parameter on a new population — the identical adjudication already made
  and documented for the ds floor (`EVALUATOR_CORRECTNESS.md` §3.3). It is **not** the
  post-hoc `|high−low|` tolerance §3.5 correctly refused.
- **`report/sections.py` must read `level_suppressed`** and render a suppressed spread as `—`.
  Otherwise the guard blanks a JSON field the digest still prints.

## 4. C2′ — mandatory `prereg` only; shared blanking helper (fixes P2)

Answer to revision 1's open question: **mandatory.** The back-compat cost is 13 call sites, 12
of them in one test file; `double_sort` is module-internal. An *optional* guard parameter is
strictly worse than none for this failure mode — the ad-hoc caller who forgets
`attach_double_sort` is the same one who omits an optional kwarg, and then gets an unguarded
dict that *looks* adjudicated because it carries `level_suppressed: False`.

**Take `prereg` only — not `measurement`.** Passing a separate `CohortMeasurement` creates a
brand-new bypass: nothing would check `measurement.events is measured`, so a caller could hand
in a floor-passing measurement and a floor-failing event list. Derive the floor internally from
`measured` (identical arithmetic to `_floor_failures`).

`attach_double_sort` survives — the *parent*-suppression bit genuinely cannot be known inside
`double_sort`. Both must call one shared `_blank_absolute_legs(ds)` helper, or C2 silently
splits the "single choke point" invariant that helper's docstring asserts.

## 5. C3′ — reuse the existing reason taxonomy (replaces C3)

> **SHIPPED AS A SLICE.** The load-bearing half landed: `MeasuredEvent.no_price_series` uses
> the CORRECTED predicate (`hist is None or not hist.dates`), `CohortMeasurement` carries
> `n_no_price_series`, and the floor note now names the split — *"of the N unmeasured, X had
> NO price series (COVERAGE, nothing cancels it) and Y had a series but no return at the
> horizon (attrition). A coverage shortfall does NOT inherit attrition's double-sort
> exemption."* That is the sentence whose absence let the 12pp claim through.
> **Deferred:** extracting `backfill.py`'s full eight-reason classifier into a shared leaf and
> reporting per-bucket reason counts. Reporting-only refinement; the mechanical guard is
> already carried by the per-bucket floor (§3).

Revision 1's `absent_series = (hist is None)` predicate is **inverted**. Verified at
`prices.py:178-196`: a genuinely delisted/unknown symbol gets a real `PriceHistory` with
`dates == []` (and is deliberately day-cached). `hist is None` therefore means the fetch
**raised** — a transport failure — so revision 1 would have labelled true survivorship as
attrition, the precise inversion it existed to prevent. `CIK:*` sentinels are a third class
again (excluded from the fetch list at `daily.py:947`).

`backfill.py:395-430` already classifies correctly into eight reasons — including
`no_price_series` defined properly as `hist is None or not hist.dates` — and aggregates them
(`backfill.py:502`). Extract that predicate chain into one shared pure leaf (the
`_form4.py`/`_edgar_facts.py` pattern), then:

- `MeasuredEvent.non_measurable_reason: str | None`
- `CohortMeasurement.reason_counts: dict[str, int]`
- the same counts per double-sort bucket
- the floor note and `_SUPPRESSION_NOTE` name the split instead of asserting attrition.

Reporting-only, no new threshold — the *mechanical* guard lives on the registered floor (§3).

## 6. C4′ — closed-form Monte-Carlo error, not split-half (fixes P4)

Revision 1's split-half estimator is biased and can falsely reassure. With `σ_B` the MC SD of
one endpoint, two independent halves give a difference with SD ≈ `2σ_B`, so
`E|diff| ≈ 1.6·σ_B` and `SD(|diff|)/E|diff| ≈ 0.76` — it returns a small value by luck roughly
one time in three, actively reassuring the reader that a comparison was safe. Taking the max
over endpoints compounds it. A sequential split is also the most lattice-exposed partition of
the LCG whose structure `TODO.md` already tracks.

Use the order-statistic standard error instead — deterministic, zero extra compute, exactly
testable, computed at the **effective BC percentiles** from `_bias_corrected_interval`
(`validate.py:815`), not at 0.05/0.95:

```
SD(q̂_p) ≈ (alphas[i+h] − alphas[i−h]) · √(p(1−p)/B) / (2h)
```

Report as `ci_mc_error`. Reporting-only: no automatic refusal, since a "too noisy to compare"
threshold would be invented post-hoc — the error this document exists to prevent.

## 7. Revision log

| # | revision-1 claim | outcome |
|---|---|---|
| C1 | blank `high_frac`/`low_frac` on a rejected cohort | **BLOCKED — inverted.** Suppresses the diagnostic, keeps the spread that depends on it (§3) |
| C3 | `hist is None` ⇒ absent input | **INVERTED.** A dead symbol yields empty-`dates`, not `None`; `None` means the fetch raised (§5) |
| C4 | split-half MC error | **Biased ~1.6×, falsely reassures ~1/3 of the time.** Replaced with the closed form (§6) |
| §4 | optional vs mandatory `prereg` | **Mandatory**, and `prereg` only — a separate `measurement` would open a new bypass (§4) |
| §1 | "more review is not the fix" | Conclusion right, argument was a non sequitur; reworded (§1) |
| — | *(missing)* | **C5 `--as-of`** — removes the reason the unguarded script existed (§2) |

## 8. Not changed

No verdict rules, no thresholds moved, no new pre-registered parameter, no RNG change.

## 9. Testing

- **C5:** `--as-of` pins both `as_of` and the price-cache key; a `replay:` artifact is
  **rejected** by `_load_validation_digest` (the live-safety test).
- **C1′:** below-floor bucket ⇒ `spread_ci is None` + `level_suppressed`, fractions still
  present; `high_frac` computed mature-only matches a hand-built expectation; a suppressed
  spread renders `—` in both report renderers.
- **C2′:** `double_sort` without `prereg` is a TypeError; with it, a below-floor cohort is
  already suppressed **without** `attach_double_sort` — the exact `rederive.py` bypass, pinned.
- **C3′:** the shared classifier returns `no_price_series` for empty-`dates` **and** for
  `None`, `unresolved_ticker` for a `CIK:` sentinel; one test asserts a dead-symbol
  `PriceHistory(dates=[])` is NOT labelled attrition (the revision-1 inversion, pinned).
- **C4′:** `ci_mc_error` scales as `1/√B` — deterministic given the sorted replicate array.
- Gate: `uv sync --extra edgar --extra bot`, `uv run ruff check src tests`, `uv run pytest -q`.
  Baseline 2286 passed / 3 skipped.
