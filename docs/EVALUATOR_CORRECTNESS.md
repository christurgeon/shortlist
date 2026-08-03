# Evaluator correctness pack — design (revision 2)

**Status:** design signed off after two adversarial reviews; implementation pending.
**Date:** 2026-08-02, revised 2026-08-03 after review.
**Scope:** defects in the signal-validation evaluator (`scout/validate.py`,
`scout/preregister.py`) and its wiring (`scout/daily.py`).
**Closes:** `TODO.md` items **0c** and **0g**, plus the 2026-07-11 entry's item **4**.

Tracked on purpose: `docs/superpowers/specs/` is gitignored (`.gitignore:37`) and CLAUDE.md
records two enablement artifacts already evaporating there. Precedent: `docs/FORM4_INSIDER.md`.

> **Revision 2 changed this design substantially.** Two review blockers were accepted, and
> **three claims made in revision 1 — all three of them mine — were measured and refuted.**
> §7 is the log. Read it before trusting any reasoning quoted from revision 1.

---

## 0. What is NOT in scope

- **No verdict-*rule* changes.** No threshold moves, no new triggers, no edits to the
  KILL/HOLD/INSUFFICIENT branch logic. (Verdict *outputs* may move — see §2.5 — because a CI
  input is corrected. That is measured before it lands, not assumed.)
- **No cohort re-runs, no audit rewrites.** §6 records what goes stale.
- **No RNG change.** See §7 M6.
- **No new signal, no new data source.** `scoring.score()` stays byte-identical.

---

## 1. Item A — the pre-registration tamper gate does not gate

### 1.1 Defect A-1 (new, unrecorded, severe)

`load_prereg` reads the **working tree** (`preregister.py:29`). `verify_untampered` checks
only the path's last commit time. Nothing compares the two. Demonstrated live:

```
# edit min_measurable_frac 0.90 -> 0.10 on disk, do not commit
load_prereg sees min_measurable_frac = 0.1
verify_untampered  = (True, 'ok')
```

### 1.2 Defect A-2 (`TODO.md` 2026-07-11 item 4)

`git log -1 --format=%cI -- <path>` lacks `--follow`, so a `git mv` resets the registration
clock. Real instance: `edgar_buyback_auth.yaml` reads 2026-07-12 (the rename) instead of
2026-07-09 (the content, matching the file's own `as_of:`).

### 1.3 Fix — read the registered bytes, don't detect divergence from them

**Revision 1's A1 check was defeated in review** (§7 B2): `git update-index
--assume-unchanged` makes `git status` report clean while `load_prereg` still reads the
tampered worktree. A detector for a divergence that should be structurally impossible is the
wrong shape — CLAUDE.md: *"prefer making the guard mechanical … a rule can be read past, a
suppressed field cannot."*

**`load_prereg` now parses `git show HEAD:<path>`, never the working tree.** There is then no
worktree-vs-HEAD gap to detect, no filter/EOL subtlety, and no `git status` index refresh
(which also removes an `index.lock` contention mode on the box, where the scout timer and the
bot run concurrently).

Fallback, deliberately narrow: if git is unavailable or `repo_root` is not a repo,
`load_prereg` falls back to the worktree read **and** `verify_untampered` independently
returns `(False, …)`, so any verdict built that way is labelled `NOT PRE-REGISTERED`. A
pip-installed non-git deployment therefore still runs, and still tells the truth about what
it could verify.

**`verify_untampered` then reduces to the age question (A2):**

Walk `git log --first-parent --follow --format='COMMIT %H %cI' --name-only -- <path>`
newest → oldest. At each commit resolve the blob at that commit's historical path and compare
**`yaml.safe_load(blob)` equality**, not blob equality. The oldest contiguous match's
committer date is the registration date; compare to `run_as_of`.

Three review-driven corrections are baked into that sentence:

- **`--first-parent`** — without it the walk enters merged side branches. Constructed
  counter-example: a prereg registered 2026-01-01 and never changed on the mainline, but
  edited *and reverted* on a feature branch, returns 2026-02-02. This repo squash-merges, so
  first-parent *is* the mainline.
- **Parsed-YAML equality, not blob equality** — a comment-only or whitespace edit changes the
  blob and would reset registration with no inference parameter touched. YAML equality is
  immune to cosmetic churn while still catching an A→B→A parameter revert.
- **Merge commits** — `--name-only` emits no path for a merge. Explicitly *skip* such
  entries (neither a match nor a mismatch); with `--first-parent` they are rare, and treating
  "no path" as a mismatch would silently truncate the walk.

**Contiguous-from-first-parent-HEAD, not earliest-ever:** with history A→B→A, earliest-ever
would credit the pre-B date, letting a tamper be laundered by reverting.

### 1.4 What this is and is not

`%cI` is forgeable via `GIT_COMMITTER_DATE` — the existing tests do exactly that. This is not
a cryptographic guarantee and the docstring will stop implying one. The threat model is the
operator editing their own thresholds after seeing a plot; against that it is effective.

Doc drift fixed in the same change: `TODO.md` and the module docstring call this a
"git-blob-hash tamper gate". There is no blob hash today. After this change there is.

### 1.5 Production safety — verified

| check | result |
|---|---|
| `/opt/shortlist` ownership | `chris:chris`; `shortlist-scout.service` runs `User=chris` |
| shallow clone? | `false` — full `--follow` history |
| all 6 prereg files dirty? | clean |
| prototype over all 6 files | 5 unchanged, 1 corrected (§1.2) |

Incidental: `edgar_form4.yaml` declares `as_of: 2026-07-26`, committed 2026-07-29. Live runs
compare against *today* and pass. Under YAML-equality this class of noise shrinks further.

### 1.6 Failure modes

| condition | result |
|---|---|
| git missing / not a repo | worktree fallback + `(False, "cannot verify…")` → labelled |
| untracked / never committed | `(False, "not committed to git")` |
| shallow clone truncates walk | oldest visible commit → later, more conservative date → fails safe |
| merge commit in the walk | skipped (§1.3) |

---

## 2. Item B — the spread CI, and the estimator the bootstrap actually resamples

### 2.1 Defect

`double_sort:426` computes `spread_ci` with `stationary_block_bootstrap_alpha`, which
resamples **months** of an aggregated CTP series. The parent verdict moved to
`event_bootstrap_alpha` in #151. One `SignalVerdict` therefore ships two inconsistent
uncertainty models: `alpha_ci` answers *"which events did this cohort catch?"*, while
`spread_ci` one key down answers *"how smooth is this monthly series?"*. With ~500–2000
events and ~50 months, the dominant uncertainty is cross-sectional.

### 2.2 Defect, second and larger — the bootstrap resamples a different estimator

Found in review. `event_bootstrap_alpha:694` relabels each drawn event with the **draw
index** `j`, making every event unique. That does not merely un-dedup repeat *draws* — it
disables `calendar_time_portfolio:232-238`'s same-ticker dedup for genuinely distinct events
of the same issuer, which exists deliberately ("otherwise the independent-block accounting
double-weights a repeat firer"). Held-set inflation, measured on the real cohorts: **+19.6%
(13d), +23.7% (8k-neg), +8.1% (8k), +0.6% (buyback)**. So θ\* is computed by a materially
different function than θ̂ — a bootstrap applying `f' ≠ f` is not consistent for `f`.

This defect is in the **shipped, verdict-bearing** `event_bootstrap_alpha`, not only the
display-only spread.

Issuer clustering is real and is why this matters. Measured on eligible events:

| cohort | events on multi-event issuers | within-issuer composite sd | cohort composite sd |
|---|---|---|---|
| 13d | 51.0% | 4.24 | 17.31 |
| 8k | 56.5% | 4.41 | 18.43 |
| buyback | 47.6% | 5.09 | 16.40 |

Within-issuer dispersion is ~¼ of cross-sectional — the composite is largely a **firm-level**
attribute, so a firm's events share a bucket and a firm-level return shock.

### 2.3 Fix — one corrected bootstrap, used in both places

```
_bootstrap_alpha_by_issuer(events, k_months, ff3, *, statistic, n_boot, seed, …)
```

Per replicate: resample **issuers** with replacement (n_issuers preserved), take all of a
drawn issuer's events, relabel per **issuer-copy** (`f"{ticker}#{copy}"`, not per draw index).
This (a) preserves `f` exactly — dedup still operates within an issuer-copy, so the estimator
bootstrapped is the estimator reported; (b) propagates within-firm dependence; (c) still gives
a multiply-drawn issuer its bootstrap weight.

Used for **both** `event_bootstrap_alpha` (parent) and the new spread CI, per the operator's
scope decision of 2026-08-03.

**Spread statistic:** resample as above, **re-split at the replicate's own median**, rebuild
both CTPs, spread on common months, refit FF3 alpha.

**Month grid is anchored, not re-derived.** `calendar_time_portfolio:221-222` derives its
month grid from `min`/`max` of the *drawn* events, so ~37% of replicates lose the earliest
event and the window can only contract. Pass an explicit grid anchored to the original
cohort's `[lo, hi]`.

**Abstain, never substitute a different model.** If the bootstrap cannot compute, set the CI
to `None` and the method to `"unavailable"` — do **not** fall back to the month bootstrap.
Revision 1 proposed that fallback; review showed it fires precisely on **thin** cohorts,
where the month bootstrap is most artificially tight, i.e. it substitutes the known-too-tight
estimator exactly where the data is weakest. Abstention matches the module's existing
discipline (`ols` raises rather than ridge; `_monthly_path` returns `None` rather than
half-impute; `measure_cohort` abstains).

**Bias-corrected percentile.** Re-splitting at the replicate median introduces a small
known-signed upward bias (the spread is minimised at the median, so `E[S(m*)] > S(m)` by
Jensen). A naive percentile interval shifts *toward* that bias. Report
`z0 = Φ⁻¹(#{θ* < θ̂}/B)` — zero extra compute, no jackknife — and BC-adjust the endpoints.
Expose `z0` in the dict as the diagnostic.

**Replicate gating is explicit:** a replicate whose re-split violates `min_bucket_events` is
skipped and **counted**; the discard count ships in the dict. `min_independent_blocks` gates
the point estimate only (unchanged), and that asymmetry is documented rather than silent.

**Separate `n_boot_event` / `n_boot_month` parameters** so changing one default cannot
silently move the other's output.

### 2.4 Direction of the effect — measured properly

Revision 1 claimed the effect is non-monotone and that the 8-K CI gets *narrower*, and
**forbade** describing this change as widening. That was measured at **B=60**, where the 90%
interval width carries ~11% relative Monte-Carlo error; the 8-K result was **z ≈ −0.5**.
Re-measured at **B=1000, three seeds**:

| cohort | month width | event width | cluster width | event/month |
|---|---|---|---|---|
| 13d | 0.08422 | 0.10254 | 0.09929 | **1.22×** |
| 8k | 0.05656 | 0.05477 | 0.05908 | **0.97×** |
| buyback | 0.02483 | 0.02939 | 0.02746 | **1.18×** |

Buyback moved 1.39× → 1.18×, confirming the B=60 run was noise. **Honest summary: the event
bootstrap widens the interval on two cohorts and is indistinguishable on the third.** The
revision-1 prohibition is deleted.

**The review's own headline claim also did not survive.** Its simulation predicted an
issuer-cluster bootstrap ~21% wider than i.i.d.-event. Measured on real cohorts:
**0.97×, 1.08×, 0.93×** — within ±8%, sometimes narrower. The simulation did not model the
interaction with relabelling: per-draw relabelling inflates the held set (§2.2), inflating
variance on its own and partly offsetting the clustering understatement.

**So the cluster bootstrap is adopted for estimator correctness (§2.2), not because it moves
numbers.** Any commit message or audit line claiming it materially widens intervals would be
unsupported.

### 2.5 Verdict-impact gate (mandatory, before the parent change lands)

Because `decide():583` feeds `ci` to the KILL rule (`ci[1] < 0`), the parent change is gated:
replay all four committed cohorts under old and new bootstraps and diff the verdicts. **If any
verdict flips, stop and escalate** rather than silently re-issue. Expected: no flips (the
spread analogue moved ≤8%), but expectation is not measurement.

Note one genuine behaviour change from §2.3's abstention: a cohort whose bootstrap cannot
compute now yields `ci=None` → `decide()` routes to INSUFFICIENT ("could not compute … CI")
instead of quoting a month-bootstrap interval. That is the honest outcome, and it is covered
by the same gate.

### 2.6 Cost — and where it actually lands

Revision 1 said "~1–2 min added to the nightly digest". **Wrong.** The nightly `run()` calls
`_load_validation_digest` (`daily.py:658`), which reads a static `scout/validate-latest.json`
with *"NO network call at digest time"*. `run_validate` is reachable only via the CLI
(`daily.py:1272`); `grep -rn validate deploy/` is empty — no unit runs it. The cost lands on
the **manual operator CLI**: measured ~40s (13d), ~19s (8k), ~8s (buyback) per cohort per
bootstrap at B=1000.

---

## 3. Item C — the double-sort cohort is never floor-checked

### 3.1 Defect

`daily.py:1025` builds a full `ds_measurement` and discards everything but `.events`. Its
measurable fraction is never tested against `min_measurable_frac`; `_floor_failures` only ever
sees the parent, and R-0f blanks `high_ir`/`low_ir` only when the **parent** is suppressed.

### 3.2 The hypothesis, refuted on both branches

Revision 1 argued ds ⊇ scored, ds re-admits gated names, gated names skew to shells, so ds
measures worse. Replayed through the real `measure_cohort` under H2 (not the old-pooled proxy
revision 1 used — see §7 M1):

| cohort | scored pooled | scored bad vintage | ds pooled | ds bad vintage |
|---|---|---|---|---|
| 13d | 0.9192 | **2025: 0.868 ✗** | 0.9399 | none (min 0.911) |
| 8k | 0.9318 | **2023: 0.893 ✗** | 0.9576 | none (min 0.944) |
| 8k-neg | 0.5480 ✗ | — | 0.5802 ✗ | — |
| buyback | 0.9593 | none | 0.9701 | none |

The ds cohort is measured **better** than the scored cohort on *both* the pooled and the
vintage branch. The review's worry that ds's larger size means more vintage buckets tested and
therefore flapping is also not borne out — ds has *fewer* failures.

**This guard is PREVENTIVE, not corrective.** It must not be described anywhere as fixing an
active bias. It ships because a floor added only after it has bitten is worth nothing.

Residual knife-edge: **13d ds 2025 = 0.911**, 1.1pp above the floor. If it flaps, the
consequence is blanking two display-only fields — bounded, not verdict-bearing.

### 3.3 The guard

`_floor_failures(ds_measurement, prereg)` computed in `daily.py`, threaded into the existing
single choke point as `attach_double_sort(verdict, ds, *, ds_floor_failed=False)`. Blank
`high_ir`/`low_ir` when the parent is suppressed **or** the ds cohort fails its own floor; set
`level_suppressed` and a note naming which cohort failed. All blanking stays inside
`attach_double_sort`, preserving the invariant its docstring already states.

**On registering the ds floor.** Review objected that reusing the parent's registered
`min_measurable_frac` for a different population is an unregistered threshold, adopted after
seeing it does not bind. The objection is sound in form. Adjudication: **reuse the parent
floor, document it, do not add a prereg key.** Registration exists to stop a threshold being
tuned to change an *inference*; this one changes only whether two display-only fields are
blanked, and no verdict reads them. Adding a key to all six YAMLs would also reset all six
content-registration clocks under §1.3's rule — a real cost for a display-only guard. Recorded
as an explicit, reversible choice, not an oversight.

### 3.4 The disclosure — and the trap in it

Review caught that revision 1's `high_frac`/`low_frac` would have shipped as a **tautological
1.0**: `double_sort:395` filters `eligible` on `m.measurable` *before* the median split, so
both buckets are 100% measurable by construction. The disclosure Item C was justified on would
have been a constant.

Correct computation: take the median from `eligible` (so the split is unchanged), then
partition **all** composite-defined events in `measured` — including the non-measurable ones —
by that median, and report measurable/total per side.

Added dict keys, all additive: `high_frac`, `low_frac`, `ds_n_selected`, `ds_n_measurable`,
`ds_measurable_fraction`, `level_suppressed`, `spread_ci_method`, `z0`, `n_discarded`.

### 3.5 What is deliberately NOT done — spread suppression on bucket asymmetry

Review argued the "attrition cancels in the spread" claim is overstated (the audit says
"**largely** cancels"; `validate.py:512` dropped the hedge), and that cancellation fails under
unequal attrition rates, unequal missing-outcome gaps, or — its strongest point — because the
spread is an FF3 **intercept fitted on a data-dependent common-month subset**, not a
difference of means. It recommended mechanically suppressing the spread when
`|high_frac − low_frac|` exceeds a tolerance.

**Not done in v1**, on the review's own §7-M7 logic: that tolerance would be invented now,
after measurement, and it would gate a number that *is* inference-relevant. Inventing an
unregistered threshold to guard against unregistered thresholds is the wrong order.

v1 therefore **discloses** (§3.4) and **restores the hedge** — `validate.py:512` and
`_SUPPRESSION_NOTE` regain "largely", citing audit §4.4 (where the cancellation claim lives)
rather than §5.4. Enforcing a tolerance is a follow-up requiring pre-registration first.

---

## 4. Testing

Pure and unit-testable; no network.

**Item A** — extend `tests/test_scout_preregister.py` (reusing `_init_repo_with_prereg`):
- worktree tampered → `load_prereg` returns the **committed** values ← A-1 red test
- `--assume-unchanged` tamper → still returns committed values ← the review's bypass
- `git mv` + commit → date follows content, not the rename ← A-2 red test
- A→B→revert-to-A → date from the revert (contiguity)
- comment-only edit → registration date **unchanged** (YAML equality)
- merged side branch that edits and reverts → mainline date (`--first-parent`)
- non-git `repo_root` → worktree fallback **and** `verify_untampered` False

**Item B** — `tests/test_scout_validate_double_sort.py`:
- issuer-copy relabelling: a cohort of repeated same-issuer events keeps dedup active in
  replicates (θ\* uses the same `f` as θ̂) ← the §2.2 red test
- abstention: too-thin cohort → `spread_ci is None`, `spread_ci_method == "unavailable"`,
  **never** a month-bootstrap number
- **seed-sensitivity**, not a determinism tautology: three seeds, endpoint spread within a
  stated tolerance (a same-seed-same-output test proves only that an LCG is an LCG)
- anchored month grid: replicates do not shrink the calendar window

**Item C**:
- ds below floor while parent clears → `high_ir`/`low_ir` blanked, `level_suppressed` true,
  **spread untouched**
- parent suppressed → existing 0f behaviour unchanged (regression)
- `high_frac`/`low_frac` are **not** 1.0 on a cohort with non-measurable composite events
  ← the §3.4 trap test

**Existing test to amend, not silently break.**
`test_double_sort_excludes_months_where_only_one_side_holds:181` asserts
`full_result["spread_ci"] == control_result["spread_ci"]` between a 13-event and a 12-event
cohort. That equality is an artifact of month-resampling a common-months-only series; under
event/issuer resampling the two cohorts are genuinely different populations and the CIs
*should* differ. Keep the `months` / `effective_blocks` / `spread_alpha_monthly` equalities
(the real invariant: a high-only month contributes nothing to the spread) and replace the CI
assertion with a comment explaining why it no longer holds. Do **not** narrow the resample
population to preserve it — that would condition the bootstrap on the observed common-month
structure, reintroducing the conditioning error being fixed.

**Gate — the exact CI commands** (`.github/workflows/ci.yml`), not an approximation:

```
uv sync --extra edgar --extra scout
uv run ruff check src tests
uv run pytest -q
```

Baseline before any change: **2268 passed, 6 skipped, 19 deselected**; ruff clean.

---

## 5. Rejected review findings

Recorded so they are not re-raised as oversights.

- **"Issuer clustering makes intervals 21% too narrow."** Simulation-based; measured at ±8%
  on real cohorts (§2.4). The cluster bootstrap is adopted anyway, for estimator correctness.
- **"Register the ds floor in the prereg YAMLs."** Sound in form; declined for a display-only
  guard at the cost of resetting six registration clocks (§3.3).
- **"Suppress the spread on bucket-fraction asymmetry."** Correct diagnosis, premature
  remedy — the tolerance would be invented post-measurement (§3.5).
- **"Replace the hand-rolled LCG with `random.Random`."** Real, but cross-cutting to every
  seeded path and would churn existing fixtures for no measured benefit at these B. §7 M6;
  follow-up.

---

## 6. Consequences — what goes stale

1. **HEADLINE: the 13D double-sort spread no longer excludes zero, and this predates the
   change.** The committed audits quote **+2.97%/mo, CI [+2.73%, +3.17%]**. On current `main`
   the same cohort gives **α +2.41%/mo, CI [−1.51%, +6.74%]** — the interval **spans zero**.
   The old tightness is the artifact audit §3a diagnosed, and **#151 already invalidated it**
   (verified: `7398ef2` contains both `monthly_rets` and `event_bootstrap_alpha`). The audit
   calls the double-sort spread "the one survivor" and `TODO.md` calls it "the strongest
   evidence in either direction so far" — for **13d that claim must be struck, not merely
   re-derived.** Scope of the retraction: **8-K still excludes zero** (CI [+0.021, +0.075]),
   so the composite's sorting power is not retracted in general — only its flagship instance.
2. This change moves the spread intervals again (§2.4).
3. **No verdict-rule change.** Whether any verdict *output* moves is gated by §2.5.
4. Re-deriving the audits is compute and out of scope. Until it runs, **no double-sort spread
   CI from a committed audit should be quoted.**

---

## 7. Revision log — claims made and then refuted

Kept because §0's own premise is that a confidently-argued claim is not evidence.

| # | claim (revision 1, mine unless noted) | outcome |
|---|---|---|
| B1 | "Spread CIs will widen; the effect is non-monotone; 8-K gets narrower" | **REFUTED** — measured at B=60 (≈11% MC error); at B=1000 it widens on 2 of 3 and is flat on the third (§2.4) |
| B2 | "`git status --porcelain` proves the file read is the registered one" | **REFUTED** — `--assume-unchanged` bypasses it; fixed by reading `HEAD:<path>` (§1.3) |
| B3 | "ds cohort measures worse than scored" | **REFUTED** on the pooled branch, then again on the vintage branch (§3.2) |
| R1 | *(review)* "Issuer clustering ⇒ intervals 21% too narrow" | **NOT REPRODUCED** on real cohorts (±8%, §2.4) |
| M1 | Revision 1 measured §3.2 with a backfill-time `meta.measurable` proxy | Proxy's old-pooled vs H2 convention gap (~10pp) exceeds the effects it was resolving; replaced with real `measure_cohort` replay (§3.2) |
| M6 | — | LCG lattice concern acknowledged, deferred (§5) |

**The pattern worth keeping:** every one of B1–B3 was a *directional* claim argued from
plausible mechanism and then contradicted by measurement. Two survived into a committed spec
before being caught. This is the same failure the 2026-07-26 retractions record, and the
mitigation is the same — measure the thing, then write the sentence.
