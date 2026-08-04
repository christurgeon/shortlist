# Audit re-derivation under the corrected evaluator (2026-08-03)

**What this is.** All five committed backfill cohorts replayed through the full evaluator
path — `measure_cohort` → calendar-time portfolio → delisting sensitivity band → `decide` →
`double_sort` — under **both** the pre-change evaluator (`92f3f6d`) and the corrected one
(`8866093`), on identical data. Closes the "re-derive the audits" follow-up carried since
2026-07-26.

**Two committed claims are RETRACTED** (§2). **No verdict changed** (§3). **One claim I
published on 2026-08-03 is itself corrected here** (§4).

Code change: `docs/EVALUATOR_CORRECTNESS.md`. Repro: `git worktree` at each commit, replay
script in the session scratchpad.

---

## 0. Method, and its one real limitation

Prices are **pinned to the cached 2026-07-26 snapshot**, and `as_of = 2026-07-26`.

That is deliberate on two grounds. It isolates the variable under audit — same data, two code
vintages, so any difference is the code. And `backtest/prices.py:fetch_history` is an
**unthrottled serial loop**; a fresh run would fire ~6,000 Yahoo requests from the same IP the
nightly scout depends on for prices and momentum, with the module's own comment warning about
"baiting the WAF". Re-fetching was judged not worth risking production for.

**The limitation this creates, stated up front:** the cached snapshot does not cover every
ticker, and coverage is *not uniform across cohorts*. Anything read off a low-coverage cohort
here is an artifact of missing prices, not a finding. Coverage of the double-sort population:

| cohort | ds-subset price coverage | usable here? |
|---|---|---|
| buyback | 99.7% | yes |
| 8k | 99.6% | yes |
| 13d | 99.2% | yes |
| 13d-a | 79.4% | **levels only, cautiously** |
| 8k-neg | **49.9%** | **NO** |

`8k-neg`'s pooled scored fraction reads 0.548 here against **0.883** in its own committed
audit — a 33pp gap that is entirely missing price data. Every `8k-neg` number below is printed
for completeness and should be read as *unmeasured*, not as evidence.

A second, smaller caveat: `as_of` is a week stale, so a handful of events that have since
matured are still counted immature. This depresses nothing that is compared like-for-like
(both code vintages see the same `as_of`) but means absolute fractions here are marginally
conservative versus a fresh run.

---

## 1. The headline: the point estimates did not move at all

| cohort | spread α/mo, OLD code | spread α/mo, NEW code |
|---|---|---|
| 13d | +2.42% | +2.42% |
| 8k | +4.71% | +4.71% |
| buyback | +0.41% | +0.41% |
| 13d-a | +0.07% | +0.07% |
| 8k-neg | −0.97% | −0.97% |

Bit-identical, as designed — the correction changed how uncertainty is estimated, never the
statistic. This is the sanity check that the change did what it claimed and nothing else.

---

## 2. RETRACTIONS — two committed spread claims no longer exclude zero

| cohort | committed audit | re-derived α | OLD-code CI | NEW-code CI | spans 0? |
|---|---|---|---|---|---|
| **13d** | **+2.97%/mo, CI [+2.73%, +3.17%]** | +2.42% | [−1.89%, +6.61%] | **[−1.93%, +8.06%]** | **YES** |
| **13d-a** | **+1.61%/mo, CI [+0.11%, +2.93%]** | **+0.07%** | [−4.21%, +4.07%] | **[−3.40%, +4.58%]** | **YES** |
| 8k | +6.26%/mo | +4.71% | [+1.72%, +7.66%] | [+1.89%, +7.97%] | no |
| buyback | +0.99%/mo | +0.41% | [−0.81%, +1.66%] | [−0.95%, +1.87%] | YES |
| 8k-neg | — | −0.97% | [−6.21%, +2.46%] | [−5.50%, +2.57%] | (unmeasured) |

**2.1 — 13D: "the one survivor" does not survive.**
`docs/audits/2026-07-26-funnel-composition-audit.md` calls the double-sort spread the one
claim left standing after the level-based verdicts were voided, and `TODO.md` called it "the
strongest evidence in either direction so far". At **CI [−1.93%, +8.06%]** it is
**inconclusive**. The committed interval's width was 0.0044 — the artifact audit §3a diagnosed
(implied monthly tracking error 0.32%).

**Attribution matters: this was NOT caused by the 2026-08-03 change.** The OLD-code column
already spans zero ([−1.89%, +6.61%]). The cause is **#151 (`7398ef2`)**, which gave the
calendar-time portfolio real per-month paths (`monthly_rets`) instead of a flattened constant
rate; a bootstrap over a genuinely varying series produces an honest interval. The 2026-08-03
change widened it a further 1.2×.

**2.2 — 13D/A: the point estimate collapses too.**
`docs/audits/2026-07-19-13d-a-stake-increase-backfill-verdict.md` records "+1.61%/mo, CI
[+0.11%, +2.93%]" and reads it as ranking information surviving a bad level. Re-derived, the
spread is **+0.07%/mo** — not merely wider, essentially zero — with CI [−3.40%, +4.58%].
Caveat honestly: at 79.4% coverage this cohort is the weakest of the three usable ones, so
treat "no ranking signal" as *unmeasured-to-weak*, not as a demonstrated null.

**2.3 — What is NOT retracted.**
**8-K's spread still excludes zero** at +4.71%/mo, CI [+1.89%, +7.97%], on 99.6% coverage.
So the general claim — *the composite orders winners inside a cohort even when the cohort's
level is bad* — retains a well-measured supporting instance. What is retracted is its two
headline instances, not the proposition.

---

## 3. No verdict changed

All ten cohort-verdicts (5 cohorts × raw + scored/gated) are `INSUFFICIENT` under both code
vintages, and `sensitivity_flip` is `False` throughout. This re-derivation therefore changes
**no** enable/disable decision: `edgar_8k`, `edgar_buyback_auth`, `edgar_13d_stake_increase`
stay disabled; `edgar_activist_13d` stays as it was.

Measurable fractions (new code, scored/gated): 13d 0.921, 8k 0.932, buyback 0.959,
13d-a 0.708, 8k-neg 0.548 (unmeasured, §0).

---

## 4. CORRECTION to a claim published earlier the same day

`docs/EVALUATOR_CORRECTNESS.md` §8.3, `CLAUDE.md`, `TODO.md` and commit `35389ac` reported the
new per-bucket disclosure as having immediately found a **12pp measurability asymmetry on
8k-neg (high 0.527 vs low 0.647)**, framed as direct evidence that "attrition cancels in the
spread" fails in practice.

**That was wrong, for the reason §0 gives:** `8k-neg`'s double-sort population is only **49.9%
price-covered in this snapshot**. The gap measures which tickers happen to be cached, not which
bucket lost names. Corrected picture, restricted to cohorts with ≥95% coverage:

| cohort | coverage | high_frac | low_frac | gap |
|---|---|---|---|---|
| 13d | 99.2% | 0.805 | 0.838 | 3.3pp |
| 8k | 99.6% | 0.964 | 0.954 | 1.0pp |
| buyback | 99.7% | 0.971 | 0.970 | 0.1pp |

> **CORRECTED 2026-08-04.** The fractions above use a POOLED denominator (immature events
> counted as unmeasurable), while the floor they are compared against is MATURE-ONLY (the H2
> fix) — they were not comparable. Recomputed mature-only, the gaps are **tighter**, and the
> conclusion is unchanged and strengthened:
>
> | cohort | high_frac | low_frac | gap |
> |---|---|---|---|
> | 13d | 0.938 | 0.942 | **0.4pp** |
> | 8k | 0.961 | 0.954 | **0.7pp** |
> | buyback | 0.971 | 0.970 | **0.1pp** |
>
> Fixed in `docs/EVALUATOR_GUARDS.md` §3. `8k-neg`'s spread is now suppressed outright by the
> per-bucket floor (0.527 / 0.646 against a registered 0.90), so it can no longer be quoted.

**On every well-covered cohort the two buckets are measured alike (≤3.3pp).** That *supports*
the cancellation assumption rather than undermining it — the opposite of what was published.
The disclosure is still worth having (it is what made this checkable at all, and it is what
caught the error), but it has **not** yet found a real asymmetry.

The mechanism of the error is worth naming, because it is the same one this project keeps
hitting: a number was read off a cohort without first checking whether that cohort's inputs
were present. The measurable-fraction floor exists precisely to stop that, and `8k-neg` was
already failing it at 0.548 — the guard was firing correctly and the number got quoted anyway.
That is the 2026-07-26 pattern, repeated, three commits after writing a spec section about it.

---

## 5. Consequences

1. **Struck:** the 13D "+2.97%/mo, CI [+2.73%, +3.17%]" and 13D/A "+1.61%/mo, CI [+0.11%,
   +2.93%]" spread claims. Superseded headers added to both audits.
2. **Retained:** the 8-K spread as the surviving well-measured instance of composite ordering.
3. **No signal changes.** Nothing enabled, nothing disabled, no config touched.
4. **Still owed:** a fresh-price re-run for `8k-neg` (and ideally `13d-a`). That needs either a
   throttle on `fetch_history` or an off-hours run that cannot collide with the 22:30 UTC
   scout — a small piece of work, tracked in `TODO.md`.
