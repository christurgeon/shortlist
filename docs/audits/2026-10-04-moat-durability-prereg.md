# PRE-REGISTRATION — does a top-quintile return on capital persist, and can that be predicted? (2026-10-04)

**Written and committed BEFORE any predictor was measured.** Design and its three-lens review:
`docs/superpowers/specs/2026-10-04-moat-durability-design.md` (gitignored; this note is the
committed record and is complete without it).

**Amended once, 2026-10-05 (the sector control), also before any SEC bulk data was read.** The
change, the original wording and the evidence are in §Amendments at the end.

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

Controls: **C0** ROIC rank and its square · **C1** a fixed effect for every SIC-2 sector ×
start-year cell: each variable is demeaned within its cell, small sectors are **not** pooled,
and a firm with no SIC code is left out of the regressions (amended 2026-10-05, §Amendments) ·
**C2** revenue rank.

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

Per test, a linear probability model on the pooled cohort-years where the predictor is defined
and the firm has a SIC code, every variable demeaned within its SIC-2 sector × start-year cell:

`outcome ~ C0 + C0² + C2 + P`, within cell

**β** = the coefficient on P: the change in the probability of the outcome from the worst to the
best rank of the predictor, among firms of the same sector and start year, oriented so the
registered sign is positive. A firm alone in its cell adds nothing to β.

Standard error: bootstrap over **CIKs** (each carries all its cohort-years), 2,000 replications,
seed 20261004, percentile interval. Ranks are fixed from the full sample; the cell means are
recomputed in every resample.

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

Under the null the per-test false-pass rate is between about 0.1% and 3.5%, so one marginal pass
among eleven is weak evidence and the verdict must say so. The upper figure is the discovery arm
alone on synthetic rows under the amended control (2.5% to 3.5% of nulls over 200 worlds, ±1.2
points). The range assumes a predictor with no industry structure finer than SIC-2; a
sub-industry label is not such a null (Limitations).

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
SIC-2 10, 12, 13, 14, 29 · exit rate by predictor tercile · state shares · `investment` on outcome B ·
β with SIC-3 × start-year cells (`beta_sic3_cells`, a point estimate with no SE; the verdict
states it beside every pass, and no pass is withdrawn because of it) · per test, the rows left
out for a missing SIC code (`n_no_sic`, and `n_no_sic_exit` for the bounds runs) and the rows
alone in their cell (`n_alone_in_cell`).

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
- **SIC-2 cells do not hold a sub-industry fixed.** A predictor that is a SIC-3 or SIC-4 trait
  can still earn β from differences in hold rates between sub-industries (synthetic worst case:
  β +0.20 with no effect inside any sub-industry). `gross_margin` and `share_stability` are the
  most exposed by construction — both are industry-level traits, and the second is computed on
  SIC-3 peers — but no predictor was shown to be safe. The SIC-3 cut is reported for this reason
  and decides nothing.
- **β is per unit of the cohort-wide rank, and the 10 pp arm is not the same hurdle for every
  predictor.** Firms in one cell span only part of the rank range when a predictor varies
  mostly between sectors, so the same within-sector difference reads as a larger β (synthetic:
  a planted 0.20 reads +0.194 for an all-within predictor and +0.252 for a half-between one).
  The `2 × SE` arm scales with it; the 10 pp arm does not. The original control read +0.143 on
  the half-between row, so the hurdle differed there too, in the other direction.
- The sector control costs power where a predictor varies between sectors: on synthetic rows
  the spread of β is 38% to 46% wider than under the original control for a predictor that is
  half between-sector (tables 3 and 9), and about the same (0.035 against 0.034, table 8) for one
  that is all within-sector.
- A firm alone in its cell carries no weight in β, and nothing here sets a floor on how many
  rows do carry weight. Read `n_alone_in_cell` before any β.
- The top-quintile floor is about 14–16% ROIC: above the cost of capital, not "dominant".
- Current SIC, not point-in-time. Nominal revenue floor.
- CIK successors read as exits (Google → Alphabet).
- Late filers (more than 120 days) are outside the universe in that year.
- Six predictors and two outcomes were chosen by one author in one sitting from the literature;
  the holdout is the only protection against that.

## Amendments

### 1 — the sector control (2026-10-05)

**State of knowledge when made.** No SEC bulk archive had been downloaded. `fetch`, `gates`,
`discovery` and `holdout` had never run. No predictor, conditional rate or regression had been
computed on real data. All evidence below is **synthetic**:
`scripts/probe_durability_sector_control.py`, committed with this amendment. Tables 1-6, 8 and 9
are each 200 seeded worlds of about 3,000 firm-years from 1,000 firms over 7 start years, in 65
sectors of very unequal size, with a sector hold-rate standard deviation of 0.10. Table 7 is
600 firms over 4 start years (about 1,770 firm-years); table 10 is 20 worlds. Those parameters
are invented; the size of every leak below depends on them. "Fully aligned" is a worst case, not
an estimate of real data.

**What changed.** In place: a new two-sentence paragraph under the header; the C1 definition; the
model sentence, which now also requires a SIC code; the model line; the β sentence (the words
"among firms of the same sector and start year" and "A firm alone in its cell adds nothing to
β"); the bootstrap sentence; the false-pass sentence under the pass rule (its upper figure, and
two sentences added after it); two reported items; four limitations. The name C1 is kept for
the new control. The text first registered on 2026-10-04 was:

> **C1** SIC-2 sector mean of the outcome, leaving out the row's own start year and own firm,
> within the window; sectors under 20 rows pool into `other`
>
> Per test, a linear probability model on the pooled cohort-years where the predictor is
> defined, every variable demeaned within start year: `outcome ~ C0 + C0² + C1 + C2 + P`
>
> **β** = the coefficient on P: the change in the probability of the outcome from the worst to
> the best rank of the predictor, oriented so the registered sign is positive.
>
> Ranks and C1 are fixed from the full sample.
>
> Under the null the per-test false-pass rate is between about 0.1% and 2.5%, so one marginal
> pass among eleven is weak evidence and the verdict must say so.

Nothing else changed. The predictors, their signs, the outcomes, the windows, the four pass
rules and their bars, the gates, the bootstrap unit, count and seed are as first registered. The
instrument gate is still the slope within start year only. No threshold or free constant was
added: the 20-row pooling line was removed and nothing replaced it.

**Why.** C1 existed so that no predictor could pass by being a sector label. It did not do that.
A leave-out sector mean is a noisy estimate entered as a covariate, and a pooled sector is
controlled only for the pooled mean, so part of the sector effect stays in β. With a **true
within-sector effect of zero** and a predictor whose sector component copies the sector's hold
rate (table 1 of the probe):

| control | mean β | share of worlds with β ≥ max(10 pp, 2 × sd) |
|---|---|---|
| original C1 | +0.127 | 71.5% |
| sector + start-year fixed effects, sectors under 20 rows pooled | +0.045 | 12.0% |
| sector + start-year fixed effects, no pooling | +0.000 | 2.5% |
| **SIC-2 × start-year cells (adopted)** | +0.000 | 3.0% |

`sd` is the spread of β across the 200 worlds, standing in for the bootstrap SE. With no
alignment every control gives a mean β within 0.001 of zero (table 3). A sector label and its
sector's hold rate are both persistent, so the holdout would repeat the bias rather than catch
it: at holdout scale, with the alignment present there too, the original C1 gives +0.153
(table 7).

**Why cells, and not sector plus start-year effects.** A sector cycle — a sector whose hold rate
moves in one start year while the predictor's sector mean moves the same way — passes through
additive sector and year effects untouched: mean β +0.198, against −0.000 with cells (table 4).
This note already names cyclical peaks as a known confound. Where both are unbiased the spread
is nearly the same: 0.049 with cells against 0.048 (table 1).

**Why a firm with no SIC code is left out of the regressions.** Kept as one group, such firms
are a pooled sector: with 5% of firms uncoded, mean β is +0.022 with them in and +0.001 with
them out (table 6). They are left out of every regression sample, the two bounds runs included;
`n_no_sic` and `n_no_sic_exit` report how many rows that removes. They stay in the universe, the
quintile floors, the cohort, the base rates and the instrument gate.

**What still works.** A planted within-sector effect of 0.20 is recovered: mean β +0.194 for a
predictor that varies only within sectors (table 8) and +0.252 for one that is half
between-sector (table 9) — β is per unit of the cohort-wide rank, which is now a registered
limitation. The firm bootstrap, with the cell means recomputed in every resample, is within
about 5% of the true spread (ratio 1.00 over 20 worlds at 200 resamples; the registered count is
2,000).

**What it does not fix.** A sub-industry label. With the predictor copying a SIC-3 hold-rate
shift, the adopted control gives mean β +0.199; SIC-3 × start-year cells give −0.001 (table 5).
SIC-3 cells were **not** adopted as the registered control. That is a judgement, not a
measurement: SIC-2 is the sector level this note registered, moving it is a parameter change
beyond the defect, and the cost in power at the real SIC-3 grain is unknown (16% wider spread on
synthetic rows with four sub-industries per sector). The SIC-3 cut is reported beside every β
instead, and the verdict must state it for every pass.

**Considered and not adopted** (reasoning, not in the probe): repairing C1 by shrinkage or an
errors-in-variables correction adds a free constant; adding the sector mean of the predictor
rank as a covariate is the additive sector effect by another route, and inherits both the
sector-cycle leak and the pooling leak.

**A side effect.** β by start year is now computable. Under the original C1 a one-year sample
left C1 constant after the leave-out, and every per-year fit was singular.
