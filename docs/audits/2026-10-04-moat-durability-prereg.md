# PRE-REGISTRATION — does a top-quintile return on capital persist, and can that be predicted? (2026-10-04)

**Written and committed BEFORE any predictor was measured.** Design and its three-lens review:
`docs/superpowers/specs/2026-10-04-moat-durability-design.md` (gitignored; this note is the
committed record and is complete without it).

## What has already been seen, and what has not

Seen (`scripts/probe_durability_frames.py`, committed with this note; it reads SEC `frames`,
which returns restated values): the **unconditional** persistence of a top-quintile ROIC — 66.7%
at 1 year, 45.7% at 3 years, 38.2% at 5 years, with 17.1% not observed at 3 years (55.2% among
firms still observed) — the effect of an invested-capital floor on that number (none: 45.7% →
46.1%, while the largest top-quintile ROIC falls from 6,190% to 190%), the per-year universe
sizes used by the reproduction gate, and that companyfacts keeps nine named filers that stopped
filing. **Those figures are not carried into the verdict; §Gates rebuilds them.**

Not seen: any predictor, any conditional rate, any regression. The code that computes them
(`shortlist.backtest.durability_study`) has run on synthetic data only.

## Why this question

The repo's `moat` leg scores a **level** and has shown no edge here
(`2026-09-25-risk-tilt-disable.md`). The claim under test is narrower and is about fundamentals,
not returns: among names earning a top-quintile ROIC, whether it **persists** is predictable from
free SEC data. Nothing below measures a return, and a pass is not evidence that a forecast pays.

## Definitions (shared with any later live surface: `shortlist/durability.py`)

- **ROIC** = `OperatingIncomeLoss` × 0.79 / invested capital. Flat rate: the ranking equals the
  pre-tax ranking.
- **Invested capital (IC)** = `StockholdersEquity` + debt. Debt = `LongTermDebtNoncurrent` +
  (`LongTermDebtCurrent`, else `DebtCurrent`) when the non-current tag exists; else
  `LongTermDebt` alone; else the current tag alone; else 0.
- **IC floor:** IC ≥ 10% of `Assets`. Under it, or IC ≤ 0, ROIC is undefined (`low_ic`).
- **Fiscal-year bucket:** `t` = calendar year of (fiscal year end − 182 days). One end per firm
  per bucket; the later wins.
- **Snapshot `t`:** the firm's facts filed on or before (its bucket-`t` fiscal year end + 120
  days), through `_xbrl_facts.annual_series`. History years are read from the same snapshot.
- **Forms:** 10-K and 10-K/A only.
- **Universe at `t`:** in snapshot `t`, ROIC defined, revenue ≥ $100M nominal, and
  `sectors.resolve_bucket(SIC) == "unknown"`. SIC is the **current** SIC from SEC submissions.
  The sector ranges are `config.yaml: sectors.buckets` at this commit; `gates.json` records their hash.
- **Cohort at `t`:** the top universe-wide ROIC quintile (ties at the floor are in).
- **Data:** `companyfacts.zip` and `submissions.zip` from sec.gov, as downloaded on the run date.
  `gates.json` records the SHA-256 of the compacted file.

## Outcomes at `t+3`

| id | outcome |
|---|---|
| **A `held`** | ROIC in snapshot `t+3` ≥ the top-quintile floor of the `t+3` universe. The firm need not meet the revenue floor. |
| **B `compounded`** | `held`, and revenue(`t+3`) / revenue(`t`) ≥ the cohort median of that ratio |

| state at `t+3` | rule | primary | bounds run |
|---|---|---|---|
| observed | ROIC defined | in | in |
| `low_ic`, operating income > 0 | IC ≤ 0 or under the floor | in, **held** | in |
| `low_ic`, operating income ≤ 0 | | in, not held | in |
| `gap` | no ROIC, but an annual fact with an end in bucket ≥ `t+3` exists | out | out |
| `exit` | no annual fact with an end in bucket ≥ `t+3` | out | all held, then all not held |

No substitution of `t+4` for `t+3`. CIK successors are not merged.

## Controls and predictors

Every covariate is a percentile rank within its cohort-year; ties share the average rank.

Controls: **C0** ROIC rank and its square · **C1** SIC-2 sector mean of the outcome, leaving out
the row's own start year and own firm, within the window; sectors under 20 rows pool into
`other` · **C2** revenue rank.

| id | predictor (code name) | definition, from snapshot `t` | registered sign | tested on |
|---|---|---|---|---|
| P1 | `track` | share of buckets `t-3..t` in the top quintile; ≥ 3 of 4 observed | more → more | A, B |
| P2 | `stability` | SD of the universe percentile rank over `t-3..t`; ≥ 3 of 4 observed | lower → more | A, B |
| P3 | `investment` | IC(`t`) / IC(`t-1`) − 1 | higher → **less** | A |
| P4 | `share_stability` | abs. change in share of SIC-3 revenue, `t-3` to `t`; peers = all filers with revenue in both years; ≥ 5 peers | smaller → more | A, B |
| P5 | `gross_margin` | gross profit / revenue at `t` | higher → more | A, B |
| P6 | `incremental_roic` | ΔNOPAT / ΔIC over `t-3..t`; only when IC grew > 5% | higher → more | A, B |

Eleven tests. A significant result with the opposite sign is a finding and **does not pass**.
P6 may be wrong-signed mechanically (a NOPAT jump is also what a transient peak looks like); the
registered sign stays positive.

## The deciding metric

Per test, a linear probability model on the pooled cohort-years where the predictor is defined,
every variable demeaned within start year:

`outcome ~ C0 + C0² + C1 + C2 + P`

**β** = the coefficient on P: the change in the probability of the outcome from the worst to the
best rank of the predictor, oriented so the registered sign is positive.

Standard error: bootstrap over **CIKs** (each carries all its cohort-years), 2,000 replications,
seed 20261004, percentile interval. Ranks and C1 are fixed from the full sample.

**The wrong metric, labelled wrong:** the raw difference in hold rate between the best and worst
third of a predictor. It is large for anything that tracks level or sector. It is computed and
printed beside every β (`raw_tercile_spread`) so the gap is visible, and it decides nothing.

## Windows

- **Discovery:** start years 2011–2017. `track` and `stability` are defined from 2012.
- **Holdout:** start years 2018–2021. **Not computed until the discovery section of the verdict
  note and `discovery.json` are committed** — the script checks git and has no override.
- Start year 2022 is excluded: its outcome bucket is censored for June fiscal year ends.
- The holdout is a temporal replication with overlapping firms, not an independent sample.

## Gates — run before any β is read

| gate | rule | on failure |
|---|---|---|
| completeness | nine named dead filers are in the compacted file through their last year (list in `probe_durability.py: DEAD_FILERS`) | stop |
| reproduction | universe size per year 2011–2024 within ±15% of: 2114 · 2102 · 2052 · 2066 · 1995 · 2161 · 2254 · 2214 · 2190 · 2207 · 2359 · 2309 · 2245 · 2179 | stop and diagnose |
| instrument | slope of `held` on the ROIC rank alone, within year, on discovery, > 0 | stop |
| tag artefact | no outcome year's `gap` rate is more than 5 points above both neighbours | widen the tag list, re-run the gates, record it here |

## Pass rule — all four, per test

1. Discovery: β ≥ max(10 pp, 2 × SE).
2. Holdout: β ≥ max(6 pp, 1.64 × SE).
3. Bounds, on discovery: with `exit` coded all-held and then all-not-held, β ≥ 3 pp in both.
4. Continuous check, on discovery: the same model with the outcome replaced by the universe
   percentile rank of ROIC at `t+3` (observed firms only) has β > 0.

Under the null the per-test false-pass rate is between about 0.1% and 2.5%, so one marginal pass
among eleven is weak evidence and the verdict must say so.

## Decision rule

| result | action |
|---|---|
| ≥ 1 test passes | Plan Phase 1 (a `/deep` context line) with the passing predictors only. |
| only `track` and/or `stability` pass | Phase 1 is a calibrated track-record line, labelled as that. |
| nothing passes | Phase 1 is a sector-conditional base-rate line. Record the null in the register. |
| a gate fails | No verdict. |

In every case: no scoring leg, no gate, no flag, no discovery list. `scoring.score()` is not touched.

## Reported, not decision-bearing

β by start year · the holdout restricted to CIKs absent from every discovery cohort · β excluding
SIC-2 10, 12, 13, 14, 29 · exit rate by predictor tercile · state shares · `investment` on outcome B.

**Deliberately not run**, because each is a parameter change that invites a search: a top-decile
cohort, a 5-year horizon, an inflation-indexed revenue floor, cohorts formed within SIC-2.

## Excluded before registration

- **A 10-K competition-word count** (Li, Lundholm, Minnis 2013). Instrument check on 18 names
  (`scripts/probe_pctcomp_instrument.py`): it spreads 5× but the cross-industry order has no
  face validity (Ford and Delta read as low competition, Microsoft as high) and it moves with
  extraction. It may return only as a within-firm year-over-year change, under its own note.
- **Margin-versus-turnover mix.** The sign would be an extrapolation from papers about *changes*
  (Soliman 2008; Fairfield & Yohn 2001), and the measure is close to a sector label.
- **Hoberg-Phillips product-market fluidity.** The free data ends at 2023, so it cannot run live.
- **Market-share growth as a positive signal.** Chowdhury, Sonaer, Celiker (2018) find it
  predicts *lower* later returns. P4 registers share *stability*, not growth.
- **R&D and SG&A intensity.** The tags are not in the extraction panel.

## Limitations, stated before the fact

- **Predictability of persistence is not profit.**
- **Power is weakest where it matters:** a predictor that tracks ROIC level has a large SE once
  level is held fixed, so the `2 × SE` arm binds. A null means "not shown".
- Accounting ROIC flatters intangible-heavy firms; IC includes cash.
- Buyback compounders have no ROIC at `t` (`low_ic`) and are never in a cohort.
- Cyclical peaks look like moats; energy and mining are a reported cut, not an exclusion.
- The top-quintile floor is about 14–16% ROIC: above the cost of capital, not "dominant".
- Current SIC, not point-in-time. Nominal revenue floor.
- CIK successors read as exits (Google → Alphabet).
- Late filers (more than 120 days) are outside the universe in that year.
- Six predictors and two outcomes were chosen by one author in one sitting from the literature;
  the holdout is the only protection against that.
