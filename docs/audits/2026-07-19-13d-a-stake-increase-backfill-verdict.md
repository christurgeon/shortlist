# 13D/A stake-increase originator (`edgar:13d_stake_increase`) — backfill verdict

> **⚠ SUPERSEDED IN PART — 2026-08-03.** The within-cohort double-sort claim below
> (**spread +1.61%/mo, CI [+0.11%, +2.93%]**, read as "ranking carries some info") is
> **RETRACTED**. Re-derived under the current evaluator the spread is **+0.07%/mo, CI
> [−3.40%, +4.58%]** — the point estimate collapses to ~zero, not merely a wider interval.
> Caveat both ways: this cohort is only 79% price-covered in the replay snapshot, so read it
> as *unmeasured-to-weak*, not a demonstrated null. The INSUFFICIENT verdict and the signal's
> disabled status are UNCHANGED.
> Full re-derivation: `docs/audits/2026-08-03-evaluator-rederivation.md`.

**Date:** 2026-07-19 (validate completed 2026-07-20) · **Signal:**
`edgar:13d_stake_increase` (scout `edgar_13d_stake_increase`) ·
**Verdict: `INSUFFICIENT` on both cohorts — KILL-shaped, stays `enabled: false`.**

Committed evidence of record, mirroring the `edgar_buyback_auth` / `edgar_8k` precedent
(`docs/audits/2026-07-11-buyback-backfill-kill.md`), because the raw run artifacts
(`scout/backfill/*.jsonl`, `scout/backfill/*.log`, `scout/validate-latest.json`) live under
the gitignored root `/scout/` and are not recoverable from a fresh clone or the
`/opt/shortlist` deploy. `scout/validate-latest.json` is additionally **overwritten by the
next `validate` run of any signal**.

## What was run

```bash
uv run --extra edgar shortlist-scout backfill --signal 13d-a \
    --start 2022-01-01 --end 2025-12-31
uv run --extra edgar shortlist-scout validate \
    --backfill scout/backfill/13d-a-2022-01-01-2025-12-31.jsonl --json
```

Executed on the `/opt/shortlist` deploy at HEAD `2257646` (#141). Backfill 2026-07-19
13:45:01Z → 18:44:09Z (**4h59m**, `rc=0`, 1422 events written); validate `rc=0`.

Pre-registration: `src/shortlist/scout/preregister/edgar_13d_stake_increase.yaml`,
committed 2026-07-19T01:03:19Z in #141 — **before** the run began, so the
`verify_untampered` check passes at face value (no rename complication, unlike the buyback
audit). Expected sign **POSITIVE** (Bebchuk-Brav-Jiang 2015 campaign-drift family), K=3m,
window 2022–2025, FF3 / equal-weight, `min_measurable_frac` 0.90,
`min_independent_blocks` 8, `delisting_return` −0.55. Signal-definition constants frozen in
code: `stake.MIN_INCREASE_PP = 2.0` (absolute pp), max-of-coverpages aggregation,
first-sighting amendments seed-and-never-emit, parse abstention = selection exclusion.

## Result

1422 events assembled; 1029 measurable. Non-measurable (393): 327 `no_price_series`,
41 `unresolved_ticker`, 25 `no_entry_price`.

| cohort | verdict | FF3 alpha (monthly) | 90% CI | IR | blocks | measurable frac |
|---|---|---|---|---|---|---|
| raw | **INSUFFICIENT** | **−1.99%** | **[−2.95%, −0.86%]** (entirely negative) | −1.92 | 17 | **0.72** (< 0.90 floor) |
| scored/gated (decision-relevant) | **INSUFFICIENT** | **−4.39%** | **[−5.90%, −2.79%]** (entirely negative) | −3.22 | 16 | 0.938 ✓ overall |

`n_immature: 0` — every window event fully matured, so this **first verdict is canonical**,
never immaturity-INTERIM (as the prereg anticipated: `window_end + K = 2026-03-31 < as_of`).
`sensitivity_flip: false` in both cohorts. Both tagged SYNTHETIC (rank/KILL-only, M1 —
survivorship precludes a PROMOTE from backfill).

**Why each cohort was gated `INSUFFICIENT`** — note these are *measurability* failures, not
ambiguous alpha:

- **raw:** overall measurable fraction 0.72 < 0.90 floor. Matches the raw initial-13D
  cohort's 0.70 — small-cap/delisted Yahoo coverage, not parse quality.
- **scored/gated:** overall 0.938 clears the floor, but the **vintage-stratified** check
  fails on 2023: 0.85 (39/46).

Measurable fraction improves monotonically with vintage (older = worse coverage):
2022 149/227 (0.66) · 2023 235/381 (0.62) · 2024 308/413 (0.75) · 2025 333/397 (0.84).

Supporting counts: `n_scored` 932 (`scored_fraction` 0.655), `n_sic_missing` 42,
`low_confidence` 98.

**Within-cohort double sort** (scored/gated, high vs low signal strength): spread alpha
**+1.61%/mo**, 90% CI [+0.11%, +2.93%] (excludes zero), n_high 456 / n_low 453 over 50
months, 16 blocks. But **both legs are negative** — high IR −1.24, low IR −1.86. The
strength ranking carries some information; the level is bad regardless.

## Interpretation

The activist campaign-drift prior did **not** survive this funnel's universe/horizon
(K=3m, 2022–2025). Both cohorts return negative FF3 alpha with 90% CIs entirely below zero,
against a pre-registered **POSITIVE** expected sign — the same family of outcome as
`edgar_8k` and `edgar_buyback_auth`. The originator **stays disabled** (weight 0.5, OFF).

**The defensible claim is "no evidence to enable," NOT "proven value-destructive."** Caveat
1 below is material enough to block the stronger reading, and the formal verdict is
`INSUFFICIENT` — the evaluator declined to certify. Do not enable without a fresh
pre-registered cohort; do not cite this as a clean KILL without addressing caveat 1.

This closes the "widen 13D?" question with committed evidence, which is the measure-first
bar doing its job: the literature justified building the signal; the cohort declined to
support it.

## Caveats (material — do not drop these when citing the numbers)

1. **`delisting_by_reason` came back EMPTY — the prereg's `delisting_return: -0.55` was
   never applied.** The 393 unmeasurable events (327 `no_price_series`) were **dropped, not
   imputed**. **197 distinct tickers** failed price fetch during validate. Those drops are
   **non-random and skew toward ACQUISITIONS** — spot-checking them surfaces NLSN (Nielsen,
   taken private; 3 events in cohort), MYOV (Myovant, acquired), MTTR (Matterport, acquired
   by CoStar) — all confirmed present in the cohort and all unmeasurable, i.e. exactly the
   *successful* activist outcome.
   Excluding takeouts removes the right tail, so the measured alpha is plausibly biased
   **DOWNWARD**. A delisting-imputation sensitivity re-run is required before treating
   −4.39%/mo as the true level. (Note the prereg's −0.55 was designed for *failure*
   delistings and would itself be wrong for takeouts — the imputation needs an
   acquisition/failure split, not a flat constant.)
2. **4 out-of-window events** dated 2026-01-02 (SCOR ×3, TTSH) past `window_end`
   2025-12-31 — 0.28% of the cohort, immaterial to the verdict, but a **chunk-boundary
   overshoot worth a bug note** in the month-chunk assembler. Maturity is unaffected
   (2026-01-02 + 3m < `as_of`).
3. **64 excess records / 48 duplicate `(ticker, event_date)` keys** (e.g. CRVW ×4 on
   2023-02-02; 1422 records vs 1358 distinct). Zero exact-duplicate lines, so these are
   most likely several reporting persons/filers per subject per day rather than a
   re-emission bug — but they are **near-identical return observations**, which
   double-counts and understates standard errors (the block bootstrap only partly
   mitigates). **`meta.adsh` is `None` on backfill emissions** (unlike live), which blocks
   accession-level dedup auditing — worth populating.
4. **Population-scope caveat** (as designed, restated): the backfill cohort's population is
   slightly broader than live emissions — backfill resolves tickers point-in-time at
   emission, while the live walker drops unresolvable-ticker rows before baselining.
5. Scored-cohort composites were **reconstructed keylessly** (no analyst/insider fields,
   SIC best-effort) and are **not comparable to live** composites — the evaluator says so
   in its own notes.

## Reproduction

The `.jsonl` cohort reproduces the alpha figures directly. Re-running the backfill from
scratch requires **≥8 GB free disk** (`min_free_disk_gb`, `backfill.py`); the deploy box
finished this run at ~7.5 GB, i.e. **below the preflight floor** — free space before any
re-run. This box also had **no warm `.cache/sec_xbrl`**, which the 8 GB floor assumes;
budget for it being rebuilt.
