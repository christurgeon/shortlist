# Evaluator correctness pack — design

**Status:** design signed off; implementation pending.
**Date:** 2026-08-02.
**Scope:** three defects in the signal-validation evaluator (`scout/validate.py`,
`scout/preregister.py`) and its wiring (`scout/daily.py`).
**Closes:** `TODO.md` items **0c** (spread CI bootstrap) and **0g** (double-sort floor check),
plus the 2026-07-11 entry's item **4** (path-based pre-registration tamper check).

This document is **tracked on purpose.** The conventional `docs/superpowers/specs/` is
gitignored (`.gitignore:37`) and CLAUDE.md records that two enablement artifacts already
evaporated there. Same precedent as `docs/FORM4_INSIDER.md`.

---

## 0. What is NOT in scope

Stated first, because the temptation to widen is the main risk to this change.

- **No verdict-rule changes.** KILL / HOLD / INSUFFICIENT logic in `decide()` is untouched.
  No threshold moves, no new triggers.
- **No cohort re-runs and no audit rewrites.** Those are compute plus their own decisions.
  §5 records exactly which committed numbers this change (and one that already shipped)
  makes stale.
- **`decide()`'s parent-cohort CI path is already correct** (`event_bootstrap_alpha`,
  #151) and stays as-is.
- **No new signal, no new data source, no scoring change.** `scoring.score()` is
  byte-identical.

---

## 1. Item A — the pre-registration tamper gate does not gate

### 1.1 Defect A-1 (NEW, previously unrecorded, severe)

`load_prereg` reads the **working tree** (`preregister.py:26`, `path.read_text()`).
`verify_untampered` checks only the *path's* last commit time (`preregister.py:38-52`).
Nothing ever compares working-tree content to committed content.

Demonstrated live on 2026-08-02 against the real repo: editing
`min_measurable_frac: 0.90` → `0.10` in `edgar_8k.yaml` **without committing** gives

```
load_prereg sees min_measurable_frac = 0.1
verify_untampered  = (True, 'ok')
```

So the pre-registered inference floor can be loosened on disk and the verdict prints with
no `NOT PRE-REGISTERED` note. This is the exact leak the module docstring says it closes
("the 'edit thresholds after seeing the plot' leak, spec §7"), and CLAUDE.md treats the
guarantee as load-bearing.

**Severity note.** This is more serious than the recorded A-2 below, and it was found only
because the design pass ran the tamper check adversarially instead of reading it. It is
consistent with the repo's own standing lesson: *a committed guard outranks your reading of
the numbers* — but only if the guard actually reads the committed bytes.

### 1.2 Defect A-2 (TODO 2026-07-11 item 4, recorded)

`git log -1 --format=%cI -- <path>` has no `--follow`, so a pure `git mv` resets the
machine-visible registration date. Confirmed on the real file that suffered it:

| file | path-based (today) | content-based (fix) | file's own `as_of:` |
|---|---|---|---|
| `edgar_buyback_auth.yaml` | 2026-07-12 (the rename) | **2026-07-09** | **2026-07-09** |

The content-based answer independently reproduces the file's self-declared `as_of`.

### 1.3 Fix

`verify_untampered` keeps its signature — `(signal_slug, *, repo_root, run_as_of) ->
tuple[bool, str]` — and gains two checks. Both must pass.

**A1 — committed and clean.**

1. `git rev-parse HEAD:<path>` must resolve → else `(False, "not committed to git")`.
   Covers untracked and never-committed files.
2. `git status --porcelain -- <path>` must be empty → else
   `(False, "uncommitted local modification — the file read is not the registered one")`.

Using `git status` (not `git hash-object`) makes the comparison filter- and EOL-safe: it is
git's own notion of "differs from HEAD", so no `--path`/`--no-filters` subtleties.

**A2 — content age.**

Walk `git log --follow --format='COMMIT %H %cI' --name-only -- <path>` newest → oldest. At
each commit resolve the blob at that commit's **historical** path (`--name-only` under
`--follow` emits the pre-rename path, verified). While the blob equals HEAD's blob, keep
walking; the **oldest contiguous match's committer date** is the registration date. Compare
that to `run_as_of`.

Both primitives verified against the real repo:

```
git log --follow --format='COMMIT %H %cI' --name-only -- <path>   # -> historical paths
git rev-parse 7a950c1:src/shortlist/scout/preregister/edgar_buyback.yaml
  -> c4ea8810…  == the blob at HEAD under the NEW name
```

### 1.4 Two decisions on the record

**Contiguous-from-HEAD, not earliest-ever.** With history A → B → A, an earliest-ever rule
would credit the pre-B date, so a tamper could be laundered by reverting. Contiguous credits
only the current uninterrupted registration.

**This is not a cryptographic guarantee, and the docs will stop implying it is.** `%cI` is
forgeable via `GIT_COMMITTER_DATE` — the existing tests in `test_scout_preregister.py` do
exactly that to control commit times. The threat model is the operator casually editing
their own thresholds after seeing a plot; against that it is effective. Against a determined
forger it is not, and no git-only scheme would be. Say so plainly rather than overclaim.

Related doc drift to fix in the same change: `TODO.md` and the module docstring both call
this a "git-blob-hash tamper gate". There is no blob hash in the current implementation.
After this change there is.

### 1.5 Production safety — verified, not assumed

A new `False` return makes every affected signal print `NOT PRE-REGISTERED` nightly, so this
was checked on the box before designing:

| check | result |
|---|---|
| `/opt/shortlist` ownership | `chris:chris`; `shortlist-scout.service` runs `User=chris` — no index-refresh permission problem |
| shallow clone? | `false` — the `--follow` walk has full history |
| all 6 prereg files dirty? | clean (`git status --porcelain` empty) |
| other tree drift | untracked `home/`, `state/` only — invisible to a path-scoped check |

All six files were run through the prototype: five unchanged, one corrected (§1.2). **No
false alarm is expected in production.**

Incidental, not fixed here: `edgar_form4.yaml` declares `as_of: 2026-07-26` but was
committed 2026-07-29. Live runs compare against *today* and pass; a historical reproduction
dated inside that 3-day gap would correctly flag it.

### 1.6 Failure modes

| condition | result |
|---|---|
| git missing / not a repo / subprocess error | `(False, reason)` — never silently trusts (existing contract preserved) |
| file untracked or never committed | `(False, "not committed to git")` |
| working tree dirty for that path | `(False, "uncommitted local modification…")` |
| shallow clone truncates the walk | oldest *visible* commit wins → a **later**, more conservative date → fails safe |
| detached HEAD | `HEAD:` resolves normally |

---

## 2. Item B — the spread CI uses a different uncertainty model than the verdict

### 2.1 Defect

`double_sort` (`validate.py:426`) computes `spread_ci` with
`stationary_block_bootstrap_alpha` — which resamples **months** of an already-aggregated CTP
series. The parent verdict was migrated to `event_bootstrap_alpha` in #151, which resamples
**events**. One `SignalVerdict` therefore ships two mutually inconsistent uncertainty
models: `alpha_ci` answers "which events did this cohort happen to catch?" while
`spread_ci`, one key down, answers "how smooth is this monthly series?".

For an event-study cohort with ~500–2000 events but only ~50 months, the dominant
uncertainty is cross-sectional — which is precisely the argument `event_bootstrap_alpha`'s
own docstring makes for the parent.

### 2.2 The justification that was tested and REJECTED

The first draft of this design claimed the current CIs are artificially tight and would
widen. **That was measured and is false.** Real committed cohorts, offline replay from
cached prices + cached FF3 (`.cache/famafrench`, 2026-07-26):

| cohort | K | month-CI width | event-CI width | ratio | replicates fitted |
|---|---|---|---|---|---|
| 13d | 12 | 0.08503 | 0.10188 | 1.20× | 60/60 |
| 8k | 3 | 0.05994 | 0.05628 | **0.94× — narrower** | 60/60 |
| buyback | 3 | 0.02472 | 0.03440 | 1.39× | 60/60 |

The effect is **non-monotone**; the 8-K CI gets *narrower*. Any framing of this change as
"widening conservative error bars" is wrong and must not appear in the code comments or the
audit trail. The defensible justification is **model consistency** (§2.1), which holds
regardless of direction.

This matters beyond wording: had the change shipped on the "widens" rationale, the 8-K
result would later have read as evidence the fix mis-fired.

### 2.3 Viability — measured, because it could have been a no-op

`event_bootstrap_alpha` returns `None` unless ≥ half its replicates fit. The spread version
is strictly harder: each replicate needs two CTPs to overlap on common months **and** a
well-conditioned FF3 fit. If it degenerated it would always fall back and the fix would be a
silent no-op.

**It does not degenerate: 60/60 replicates fitted on all three cohorts.**

### 2.4 Fix

New pure function in `validate.py`:

```
event_bootstrap_spread_alpha(eligible, k_months, ff3, *, min_bucket_events,
                             n_boot=500, min_obs=6, seed=12345, weighting="equal")
    -> tuple[float, float] | None
```

Per replicate: resample `eligible` with replacement (n unchanged), **re-split at that
replicate's own median**, rebuild both CTPs, spread on common months, refit FF3 alpha.
Return the 5th/95th percentiles.

**Joint resample + re-split, not stratified within-bucket.** The median split is part of the
estimator, so the bootstrap must repeat it. Conditioning on a split that itself carries
sampling error understates the interval — the same class of error being fixed. *This is the
single design call most worth attacking in review.*

**Ticker relabelling is mandatory.** Each drawn event is relabelled
`replace(src, ticker=f"{t}#{j}")`, exactly as `event_bootstrap_alpha` does. Without it,
`calendar_time_portfolio`'s same-ticker dedup silently discards duplicate draws and the
resample's reweighting is thrown away.

**Never silently drop a CI.** If the event bootstrap returns `None`, fall back to
`stationary_block_bootstrap_alpha`, mirroring `decide():583-585`.

**Disclose which was used.** The returned dict gains
`spread_ci_method: "event" | "month"`. `_double_sort_line` renders it only when it is
`"month"` (the fallback), so the normal line does not grow.

**`n_boot` default 2000 → 500.** Each replicate now rebuilds two CTPs. 500 matches
`event_bootstrap_alpha`. Measured cost at 500: **~4s (buyback) to ~21s (13d)** per cohort,
against 0.2s today. Production already pays a comparable cost for the parent CI, and
`double_sort` runs once per signal, so worst case is ~1–2 min added to the nightly digest.
Recorded here so it is a known cost, not a surprise regression.

---

## 3. Item C — the double-sort cohort is never floor-checked

### 3.1 Defect

`daily.py:1025` builds a full `ds_measurement = measure_cohort(ds_evs, …)` and then discards
everything except `.events`. Its measurable fraction is never compared to
`min_measurable_frac`. `_floor_failures` only ever sees the *parent* measurement, and R-0f
blanks `high_ir`/`low_ir` only when the **parent** is suppressed. So a ds cohort could fail a
floor its parent passes and nothing would say so.

`ds_evs` is composite-defined and **gate-agnostic**, a strict superset of the gate-filtered
`scored_evs` the parent verdict measures — genuinely a different population.

### 3.2 The hypothesis that was tested and REFUTED

The first draft argued: ds ⊇ scored, ds re-admits gated names, gated names skew to
shells/microcaps, therefore ds measures worse. **Measured on all four committed cohorts, ds
measures better than or equal to scored:**

| cohort | scored n | scored frac | ds n | ds frac | ds worse? |
|---|---|---|---|---|---|
| 13d | 808 | 0.833 | 2400 | 0.828 | no (−0.005) |
| 8k | 425 | 0.941 | 1344 | **0.971** | no |
| 8k-neg | 1918 | 0.882 | 7546 | **0.958** | no |
| buyback | 246 | 0.959 | 535 | **0.979** | no |

Method caveat, stated so the numbers are not over-read: these use the backfill-time
`meta.measurable` flag, which counts immature events as non-measurable — i.e. *old-pooled*
fractions, not the H2 mature-only ones `_floor_failures` actually tests. The proxy is sound:
the 13d scored figure (0.833) reproduces the value recorded in `TODO.md` (0.835). Replaying
13d through the real `measure_cohort` with H2 gives ds frac **0.9399**, which clears the 0.90
floor — consistent with the same conclusion.

**Consequence: this guard is PREVENTIVE, not corrective.** It has never fired on any
measured cohort. It must not be described in the code, the commit message, or `TODO.md` as
fixing an active bias. The reason to add it anyway is the repo's own standing principle: a
floor that is only added once it has already bitten is worth nothing.

### 3.3 Fix — the guard

Compute `_floor_failures(ds_measurement, prereg)` in `daily.py` and thread the boolean into
the existing single choke point:

```
attach_double_sort(verdict, ds, *, ds_floor_failed: bool = False)
```

Blank `high_ir`/`low_ir` when the parent is suppressed **or** the ds cohort fails its own
floor; set `ds["level_suppressed"] = True` and append a note naming which cohort failed.
Keeping all blanking inside `attach_double_sort` preserves the invariant its docstring
already states — every assignment goes through the helper.

The **spread survives** either way, per the repo's §5.4 reasoning: it is a difference between
two identically-measured buckets, so a common attrition bias cancels.

### 3.4 Fix — the disclosure (the part that carries the real value)

§3.2 is the argument for this. "Attrition cancels in the spread" holds only if the two
buckets are similarly measured — and the ds cohort's population is **invisible in `--json`
today**, so that assumption has never been checkable. A confidently-argued directional
hypothesis about this cohort was just shown to be backwards; the fix for that is disclosure,
not a better hypothesis.

The `double_sort` dict gains:

| key | meaning |
|---|---|
| `high_frac`, `low_frac` | per-bucket measurable fraction — makes the cancellation assumption checkable |
| `ds_n_selected`, `ds_n_measurable`, `ds_measurable_fraction` | the ds cohort's own population, currently invisible |
| `level_suppressed` | whether the absolute legs were blanked (§3.3) |
| `spread_ci_method` | `"event"` or `"month"` (§2.4) |

All additive dict keys. `report/sections.py:_double_sort_line` reads via `.get()`, and
`asdict()` at the persistence boundary passes dicts through, so old consumers and old
persisted `scout/validate-latest.json` files are unaffected.

---

## 4. Testing

All three are pure and unit-testable; no network in any test.

**Item A** — extend `tests/test_scout_preregister.py`, reusing its `_init_repo_with_prereg`
scratch-repo pattern (which already controls commit dates via `GIT_AUTHOR_DATE` /
`GIT_COMMITTER_DATE`):
- dirty working tree → `(False, "uncommitted…")` ← the A-1 red test; **fails on today's code**
- `git mv` then commit → date follows **content**, not the rename ← the A-2 red test;
  **fails on today's code**
- content A → B → revert-to-A → dates from the *revert*, not the original (contiguity)
- never-committed, non-repo, and the two existing before/after-`run_as_of` cases still pass

**Item B** — `tests/test_scout_validate_double_sort.py`:
- event and month bootstraps produce **different** CIs on the same cohort (the substantive
  red test — deliberately *not* asserting a direction, per §2.2)
- a cohort too thin for the event bootstrap falls back and reports
  `spread_ci_method == "month"`
- determinism: same seed → identical CI
- duplicate draws are not deduped away (relabelling works) — assert against a cohort of
  identical-ticker events

**Item C** — `tests/test_scout_validate_double_sort.py` + the daily wiring test:
- ds cohort below floor while the parent clears it → `high_ir`/`low_ir` blanked,
  `level_suppressed` true, **spread untouched** ← the invisible-hole test
- parent suppressed → existing 0f behaviour unchanged (regression)
- `high_frac`/`low_frac` populated and correct on an asymmetric cohort

**Whole-change gate:** `uv run ruff check src tests` clean, then `uv run --extra edgar pytest`
green (the edgar extra is required — bare `pytest` errors on edgar tests importing pandas).

---

## 5. Consequences to record — what this makes stale

Nothing here changes a verdict. `spread_ci` is display-only; no rule reads it. But numbers in
committed audits move, and one is already stale independent of this change.

1. **Committed double-sort spread CIs are stale ALREADY, pre-dating this change.**
   `docs/audits/2026-07-19-13d-a-stake-increase-backfill-verdict.md` and the 2026-07-06 entry
   quote the 13D spread as **+2.97%/mo, CI [+2.73%, +3.17%]** — a width of 0.0044. Replaying
   that cohort on current `main` gives **α +2.41%/mo, CI [−1.9%, +6.6%]**, ~19× wider. The
   old tightness is exactly the artifact audit §3a diagnosed (implied tracking error 0.32%),
   and **#151's `monthly_rets` fix already invalidated it** — before this change. `TODO.md`
   still calls that figure "the strongest evidence in either direction so far." It is not.
2. **This change moves them again**, by 0.94×–1.39× (§2.2).
3. **No verdict flips.** No KILL / HOLD / INSUFFICIENT trigger reads `spread_ci`,
   `high_ir`/`low_ir`, or any new key.
4. Re-deriving the audits is compute and out of scope (§0). It should be a follow-up
   `TODO.md` entry, and until it runs, **no double-sort spread CI from a committed audit
   should be quoted.**

---

## 6. Risks

| risk | mitigation |
|---|---|
| A1 fires in production and every signal reads `NOT PRE-REGISTERED` | Verified: box clean, non-shallow, right owner, all 6 files pass the prototype (§1.5) |
| Joint-resample-and-re-split is the wrong bootstrap structure | Flagged as the top review target (§2.4); stratified alternative is a one-function swap if review disagrees |
| Nightly digest slows | Measured 4–21s/cohort (§2.4); bounded and recorded |
| Scope creep into verdict rules | §0 is explicit; tests pin `decide()` behaviour unchanged |
| Someone later reads Item C as having fixed a live bias | §3.2 records the refutation in the tracked tree, not just in a commit message |
