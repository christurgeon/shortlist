# PRE-REGISTRATION — does a top-quintile return on capital persist, and can that be predicted? (2026-10-04)

**Written and committed BEFORE any predictor was measured.** Design and its three-lens review:
`docs/superpowers/specs/2026-10-04-moat-durability-design.md` (gitignored; this note is the
committed record and is complete without it).

**Amended four times (2026-10-05: the sector control; a fifth pass rule; the reproduction gate
and the points the first text left open. 2026-10-07: the share-stability predictor and a last
review before the data), all before any SEC bulk data was read.** Each change, the wording it
replaced and the evidence are in §Amendments at the end.

## What has already been seen, and what has not

Seen (`scripts/probe_durability_frames.py`, committed with this note; it reads SEC `frames`,
which returns restated values): the **unconditional** persistence of a top-quintile ROIC — 66.7%
at 1 year, 45.7% at 3 years, 38.2% at 5 years, with 17.1% not observed at 3 years (55.2% among
firms still observed) — the effect of an invested-capital floor on that number (none: 45.7% →
46.1%, while the largest top-quintile ROIC falls from 6,190% to 190%), the per-year universe
sizes used by the reproduction gate, the share of each of those with no `US-` address (8.3% to
10.6%, amendment 3), and that companyfacts keeps nine named filers that stopped filing.
**Those figures are not carried into the verdict; §Gates rebuilds them.**

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
  per bucket; the later wins. A firm's year ends, its snapshot dates and its last bucket are
  located from every annual revenue or operating-income fact, whenever filed; no value is read
  that way.
- **A fact filed before its own period ended is never read**, as a value or as a year end. It
  is a period typed with the wrong year (amendment 4).
- **Snapshot `t`:** the firm's facts filed on or before (its bucket-`t` fiscal year end + 120
  days), through `_xbrl_facts.annual_series`. History years are read from the same snapshot.
- **Forms:** 10-K and 10-K/A only.
- **Universe at `t`:** in snapshot `t`, ROIC defined, revenue ≥ $100M nominal, and
  `sectors.resolve_bucket(SIC) == "unknown"`. SIC is the **current** SIC from SEC submissions.
  The sector ranges are `config.yaml: sectors.buckets` at this commit; `gates.json` records their hash.
- **Cohort at `t`:** the top universe-wide ROIC quintile (ties at the floor are in).
- **Data:** `companyfacts.zip` and `submissions.zip` from sec.gov, as downloaded on the run date.
  Every output records the SHA-256 of the compacted file and of the SIC map, the commit, the
  Python version, and a digest of the code that decides a number (`probe_durability.py: CODE`).

## Outcomes at `t+3`

| id | outcome |
|---|---|
| **A `held`** | ROIC in snapshot `t+3` ≥ the top-quintile floor of the `t+3` universe. The firm need not meet the revenue floor. |
| **B `compounded`** | `held`, and revenue(`t+3`) / revenue(`t`) ≥ the median of that ratio in the cohort-year, over the rows with a determined `held` and a revenue ratio. The ratio exists when revenue(`t+3`) is tagged and above zero. A row with no ratio is out of outcome B, whether it held or not. |

| state at `t+3` | rule | primary | bounds run |
|---|---|---|---|
| observed | ROIC defined | in | in |
| `low_ic`, operating income > 0 | IC ≤ 0 or under the floor | in, **held** | in |
| `low_ic`, operating income ≤ 0 | | in, not held | in |
| `gap` | no ROIC, but an annual fact with an end in bucket ≥ `t+3` exists | out | out |
| `exit` | no annual fact with an end in bucket ≥ `t+3` | out | all held, then all not held |

For outcome B the two bounds runs code `exit` as all compounded, then all not compounded.
A cohort firm whose `t+3` 10-K is filed later than 120 days is a `gap`, like one with an
untagged input: it is out of the primary sample and the bounds runs do not bracket it.
No substitution of `t+4` for `t+3`. CIK successors are not merged.

## Controls and predictors

Every covariate is a percentile rank within its cohort-year; ties share the average rank.

Controls: **C0** ROIC rank and its square · **C1** a fixed effect for every SIC-2 sector ×
start-year cell: each variable is demeaned within its cell, small sectors are **not** pooled,
and a firm with no SIC code is left out of the regressions (amended 2026-10-05, §Amendments) ·
**C2** revenue rank.

| id | predictor (code name) | definition, from snapshot `t` | registered sign | tested on |
|---|---|---|---|---|
| P1 | `track` | share of buckets `t-3..t` in the top quintile; ≥ 3 of 4 observed; start years from 2012 | more → more | A, B |
| P2 | `stability` | SD of the universe percentile rank over `t-3..t`; ≥ 3 of 4 observed; start years from 2012 | lower → more | A, B |
| P3 | `investment` | IC(`t`) / IC(`t-1`) − 1 | higher → **less** | A |
| P4 | `share_stability` | abs. **relative** change in share of SIC-3 revenue, `t-3` to `t`: \|ln(share at `t` / share at `t-3`)\|; peers = all filers with revenue in both years; ≥ 5 peers, the firm included | smaller → more | A, B |
| P5 | `gross_margin` | gross profit / revenue at `t` | higher → more | A, B |
| P6 | `incremental_roic` | ΔNOPAT / ΔIC over `t-3..t`; only when IC grew > 5% | higher → more | A, B |

For P1 and P2 a history bucket is observed when the firm's ROIC is defined in it and the
universe of that bucket has a quintile floor. The firm's own history years need not meet the
revenue floor; the universe it is ranked against does. That universe, and the peer totals of
P4, are read from snapshot `t`: for a bucket before `t` they hold the firms that still have a
year end in bucket `t`, on the values each knew at its own snapshot date.

Conventions the code fixes: a universe percentile rank is the share of the universe at or
below the value; the quintile floor of n values is the value in place ⌊n/5⌋ from the top;
`stability` and the bootstrap SE are population standard deviations.

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
recomputed in every resample. All 2,000 must be fitted: a test with a replication that cannot be
fitted is recorded as an error and does not pass (amendment 3).

**The wrong metric, labelled wrong:** the raw difference in hold rate between the best and worst
third of a predictor. It is large for anything that tracks level or sector. It is computed and
printed beside every β (`raw_tercile_spread`) so the gap is visible, and it decides nothing. It
has no value when a third of the predictor is empty (`track` takes at most seven values).

## Windows

- **Discovery:** start years 2011–2017. `track` and `stability` are defined from 2012: the code
  leaves both undefined for start year 2011 (amendment 3).
- **Holdout:** start years 2018–2021. **Not computed until the discovery section of the verdict
  note and `discovery.json` are committed** — the script checks git and has no override.
- Start year 2022 is excluded: its outcome bucket is censored for June fiscal year ends.
- The holdout is a temporal replication with overlapping firms, not an independent sample.

## Gates — run before any β is read

| gate | rule | on failure |
|---|---|---|
| completeness | nine named dead filers are in the compacted file through their last year (list in `probe_durability.py: DEAD_FILERS`) | stop |
| reproduction | the **comparison count** per year 2011–2024 within ±15% of: 2114 · 2102 · 2052 · 2066 · 1995 · 2161 · 2254 · 2214 · 2190 · 2207 · 2359 · 2309 · 2245 · 2179 | stop and diagnose |
| instrument | slope of `held` on the ROIC rank alone, within year, on discovery, > 0 | stop |
| tag artefact | no outcome year's `gap` rate is more than 5 points above both neighbours (the first and the last outcome year have one neighbour and cannot be flagged) | widen the tag list, re-run the gates, record it here |

The **comparison count** is not the study's universe. It is the number of filers with ROIC
defined and revenue ≥ $100M in the bucket, read from each filer's latest values (whenever
filed), with no sector mask, on forms 10-K and 10-K/A and on the foreign annual forms 20-F and
40-F and their amendments. The targets were counted on SEC `frames`, which has no sector mask,
no filing date and no form filter and returns restated values, so this is the count that can
be compared with them (amendment 3). The foreign forms are read for this count and for nothing
else. `gates.json` also writes the study's universe size and the same size without the sector
mask, for every year. No bar is set on either.

**If the reproduction gate fails, one thing can settle it.** A place is found where the code
does not do what this note says. It is fixed under a dated amendment that quotes the failed
counts, and everything is run again from `fetch`, on the kept archives. Where the note is
silent, the reading is registered by that amendment before the run. An archive that is itself
damaged may be downloaded again, with the failed counts quoted the same way. No definition,
band or target is changed to make the gate pass. With no such fault there is no verdict.

The tag-artefact gate keeps its own remedy, and that remedy is not one for this gate: a wider
tag list is judged by the `gap` rate alone, its effect on the comparison count is recorded, and
if the reproduction gate also failed it stays failed. A wider list is defined for the study
(`_xbrl_facts.py` is not edited), it applies to the study and to the comparison count alike,
and every gate is judged again on the new run: a list that takes the comparison count out of
its band ends in no verdict.

`gates` builds the holdout cohorts as well as the discovery ones, for the `gap` rate of the
outcome years 2021–2024 and for nothing else. It writes no outcome and no predictor of them.

## Pass rule — all five, per test

1. Discovery: β ≥ max(10 pp, 2 × SE).
2. Holdout: β ≥ max(6 pp, 1.64 × SE).
3. Bounds, on discovery: with `exit` coded all-held and then all-not-held, β ≥ 3 pp in both.
4. Continuous check, on discovery: the same model with the outcome replaced by the universe
   percentile rank of ROIC at `t+3` (observed firms only) has β > 0.
5. Sub-industry check, on discovery **and** on holdout: on the primary outcome sample, the same
   model with SIC-3 × start-year cells in place of SIC-2 cells has a β at least **half** of that
   window's β. A cut that cannot be computed fails. The SIC-3 β (`beta_sic3_cells`) is printed
   for every test, and the verdict states it beside every pass (amendment 2).

Under the null the per-test false-pass rate is between about 0.1% and 3.5%, so one marginal pass
among eleven is weak evidence and the verdict must say so. The upper figure is the discovery arm
alone on synthetic rows under the amended control (2.5% to 3.5% of nulls over 200 worlds, ±1.2
points). The range assumes a predictor with no industry structure finer than SIC-2; a
sub-industry label is not such a null, and rule 5 cuts its pass rate without removing it
(Limitations).

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
SIC-2 10, 12, 13, 14, 29 · exit, `gap` and `low_ic` rate by predictor tercile · state shares ·
`investment` on outcome B ·
per test, the rows left out for a missing SIC code (`n_no_sic`, and `n_no_sic_exit` for the
bounds runs) and the rows alone in their cell (`n_alone_in_cell`, `n_alone_in_sic3_cell`) · in
`gates.json`, the share of discovery cohort rows alone in their SIC-2 and SIC-3 cell
(`discovery_share_alone_in_cell`), to be read before any β · in `gates.json`, three counts per
year (`universe_sizes`, `universe_sizes_unmasked`, `comparison_counts`) and the share of the
universe and of each discovery cohort with zero debt (`zero_debt_share`) · in `fetch.json`, the
archive members that could not be read (`unreadable_members`) and the filers on foreign forms
only (`foreign_only_filers`) · per test, the sample size by start year (`n_by_year`) · in
`gates.json`, the firms per year with a ROIC and no revenue value (`universe_no_revenue_tag`,
and `…_assets_500m` for those with assets of $500M or more) and the 10-K rows filed before
their own period ended (`facts_filed_before_period_end`).

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
  SIC-3 peers — but no predictor was shown to be safe. Rule 5 is the guard, and it is a filter,
  not a proof: on synthetic rows a fully aligned sub-industry label passes both windows in 0% to
  2% of worlds with it and in 89% to 100% without it, **provided SIC-3 holds the label fixed**. A
  label at a grain finer than SIC-3 is not controlled at all.
- **Rule 5 costs power where sub-industries are thin, and it fails a mixed predictor.** With 57%
  of discovery rows alone in their SIC-3 cell, a real within-sector effect of 0.12 passes 44.0%
  of synthetic worlds against 56.0% without the rule; at 15% alone it costs nothing. A predictor
  with a real effect of 0.12 **and** a fully aligned sub-industry component passes 40.5%: most
  of its β is the sub-industry, and the rule is built to say so. The real share of rows alone
  is unknown until the gates run, and no rule here adjusts for it.
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
- **A firm with no debt tag in the panel has debt zero.** So has a firm that reports its debt
  under another tag (a combined debt-and-lease tag, for example). Its invested capital is then
  too small and its ROIC too high, which favours entry to the cohort. `zero_debt_share` in
  `gates.json` is an upper bound on the firm-years this can touch: it also counts a firm that
  has no debt and one that tags a debt of zero, and it cannot tell the cases apart.
- **A year end on or after 2 July (1 July in a leap year) is in the bucket of that calendar
  year; an earlier one is in the bucket of the year before.** A 52/53-week filer whose year
  ends near 30 June can therefore have an empty bucket, and later two year ends in one bucket
  (year ends 2015-06-28 and 2016-07-03 are buckets 2014 and 2016). In the empty bucket the firm
  is outside the universe, and as an outcome year it is a `gap`. A few filers; not corrected.
- **The reproduction gate tests the data path, not the study's universe.** It reads restated,
  unmasked values on more forms than the study, and it counts membership: a wrong ROIC value
  that leaves a firm on the same side of the floors would not fail it, and neither would an
  error in the 120-day snapshot or in the sector mask. The two other counts in `gates.json` are
  the only check on those, and they have no bar.
- **Revenue is read from the first tag, in a fixed order, that has the year end; the ASC 606
  tag is first.** A filer that reports both that tag and a `Revenues` total has the ASC 606
  figure from about 2018, and it is typically the narrower one. A start year before the change
  and an outcome year after it then understate growth (outcome B), and the revenue floor,
  `gross_margin` and `share_stability` read that figure too. Not measured.
- **Early start years are thin for two predictors.** `share_stability` and `incremental_roic`
  both read the year `t-3`. For start years 2011 and 2012 that year can be before a filer's
  first XBRL filing (the phase-in ran from 2009 to 2011, the largest filers first), so each is
  expected to be defined for fewer, larger filers there. `n_by_year` shows it; nothing adjusts
  for it.
- **About one larger filer in six is outside the universe for a revenue tag in 2011–2015.**
  The universe needs revenue for its $100M floor, and revenue is read from four tags. On SEC
  `frames`, of the filers with operating income, equity, and assets of $500M or more, 17.2% to
  17.8% have none of the four in 2011–2015 (322 to 350 filers a year), 4.6% in 2016 and 1.1% to
  4.0% after (`scripts/probe_durability_frames.py`, section 5; over filers of every size the
  shares are 22% to 24%, 16%, and 10% to 18%, and many of the small ones have no revenue to
  report). Many of the larger ones report revenue under other tags; the cache cannot say
  which. The reproduction targets read the same four tags, so that gate cannot see this. Two
  consequences: the discovery cohorts are drawn from the filers that used one of the four tags,
  and a later universe is wider than an earlier one. On restated values the change is at 2016,
  so the `t+3` universe is the wider one from start year 2013. As first reported it is
  probably later, with the new revenue standard of 2018, so from start year 2015; that is an
  expectation, and `universe_no_revenue_tag_assets_500m` in `gates.json` shows the year. The
  tag list was not widened: the other tags are often a part of revenue, not the total.
- **`track` and `stability` are longer measures of the same level.** C0 holds one year's ROIC
  rank fixed. A firm's ROIC is a level plus a year's noise, so four years say more about the
  level than one does. A pass for either therefore shows that a track record adds to one
  year's figure. It does not show a separate trait of durability, and the decision rule labels
  it as a track-record line for that reason. `stability` is also bounded: a rank near the top
  has little room to vary.
- **`gap` is not bracketed.** A firm that files its `t+3` 10-K late, or leaves an input
  untagged, is a `gap`, and a firm in distress files late. The bounds runs bracket `exit` only.
  The `gap` rate by predictor tercile is reported so that an uneven one can be seen.
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
instead, and the verdict must state it for every pass. *(Superseded the same day by amendment 2:
the SIC-3 cut now feeds pass rule 5.)*

**Considered and not adopted** (reasoning, not in the probe): repairing C1 by shrinkage or an
errors-in-variables correction adds a free constant; adding the sector mean of the predictor
rank as a covariate is the additive sector effect by another route, and inherits both the
sector-cycle leak and the pooling leak.

**A side effect.** β by start year is now computable. Under the original C1 a one-year sample
left C1 constant after the leave-out, and every per-year fit was singular.

### 2 — a fifth pass rule: the sub-industry check (2026-10-05)

**State of knowledge when made.** As for amendment 1: nothing had run in between. No SEC bulk
archive had been downloaded and no predictor had been measured on real data. The evidence is
**synthetic**, tables 11-26 of the same probe. Each table is 200 seeded worlds of 1,600 firms
over 11 start years (about 3,050 discovery and 1,750 holdout firm-years). One world spans both
windows, so a label and its group's hold rate persist from discovery into the holdout, as they
would in real data. The other parameters are those of amendment 1, and are as invented.

**What changed.** In place: the two-sentence paragraph under the header; the pass-rule heading ("all four"
became "all five") and the new rule 5; one clause of the false-pass sentence; in the reported
list, the SIC-3 item moved into rule 5, the alone-in-cell item gained `n_alone_in_sic3_cell`, and
the `gates.json` share was added; in the limitations, the end of the sub-industry bullet was
replaced and one bullet was added; one sentence of amendment 1 is marked superseded. The two
passages of amendment 1 that this replaces were:

> … (`beta_sic3_cells`, a point estimate with no SE; the verdict states it beside every pass,
> and no pass is withdrawn because of it)
>
> The SIC-3 cut is reported for this reason and decides nothing.

Nothing else changed. Rules 1 to 4 and their bars are as first registered.

**Why a rule at all.** Amendment 1 left a hole it named: SIC-2 cells do not hold a sub-industry
fixed. A reported number with no rule would have had its rule chosen after the number was seen,
which is the choice a pre-registration exists to make first.

**The evidence.** "Joint pass" is rule 1 and rule 2 together, with the spread of β across worlds
standing in for the bootstrap SE. Rule 3 is not simulated because the synthetic rows have no
exits, and rule 4 because they have no rank outcome. "Alone" is the share of discovery rows that
are the only row of their SIC-3 × start-year cell. The sampling error of a share over 200 worlds
is about ±1 point near 2%, ±2.3 near 12% and ±3.5 near 50%.

| synthetic world (table) | alone | rules 1-2 | **+ rule 5: half of β** | + a fixed 3 pp floor (not adopted) |
|---|---|---|---|---|
| null, no group structure (11) | 15% | 0.5% | 0.5% | 0.5% |
| null, SIC-2 label, fully aligned (12) | 15% | 0.5% | 0.5% | 0.5% |
| **null, sub-industry label, fully aligned (13)** | 15% | **89.0%** | **0.0%** | 11.5% |
| null, sub-industry label, half aligned (14) | 15% | 29.5% | 2.0% | 7.5% |
| null, sub-industry label, 12 per sector (15) | 34% | 100.0% | 0.0% | 13.5% |
| null, sub-industry label, 40 per sector (16) | 57% | 100.0% | 2.0% | 12.0% |
| null, sub-industry label, 120 per sector (17) | 78% | 99.5% | 2.0% | 17.5% |
| real effect 0.12, all within-sector (18) | 15% | 54.5% | 54.5% | 54.5% |
| real 0.12, 12 sub-industries per sector (19) | 34% | 59.5% | 54.0% | 57.5% |
| real 0.12, 40 per sector (20) | 57% | 56.0% | 44.0% | 50.5% |
| real 0.12, 120 per sector (21) | 78% | 61.5% | 38.0% | 45.0% |
| real effect 0.20, all within-sector (22) | 15% | 99.0% | 98.5% | 99.0% |
| real 0.20, 120 per sector (23) | 78% | 98.5% | 73.5% | 88.0% |
| real 0.12, predictor half between-sector (24) | 15% | 66.5% | 66.0% | 66.5% |
| real 0.12, half between-sector, 40 per sector (25) | 57% | 63.0% | 43.0% | 56.5% |
| mixed: real 0.12 plus a fully aligned sub-industry label (26) | 15% | 100.0% | 40.5% | 96.5% |

**Why half of β, and not a fixed floor.** The first draft of this amendment registered a fixed
floor of 3 pp, the number this note already uses for the bounds rule. Measured, it left 11.5% to
17.5% of pure sub-industry labels passing: 3 pp is about half a standard error of the SIC-3
estimate, so the rule was close to two coin flips. A share of β asks the question the rule is
for — does most of the effect survive holding the sub-industry fixed? — and it is the same hurdle
in scale for every predictor, which a fixed floor is not, because β is per unit of the cohort-wide rank
(Limitations). It leaves 0% to 2%.

**0.5 is a new constant, chosen after seeing these synthetic tables.** No real number informed
it. The result does not hang on it: with one third in its place a fully aligned label passes
1.0% to 9.0% (tables 13 and 15-17), and with two thirds 0% to 1%; a real effect of 0.12 at 57%
alone passes 49.5%, 44.0% and 36.0% at one third, one half and two thirds.

**What it costs.** Nothing measurable where sub-industries are not thin (tables 18, 22, 24). As
they thin out it costs power: 5.5 points at 34% alone, 12 at 57%, 23.5 at 78% for a real effect
of 0.12. The fixed floor costs less there (2, 5.5 and 16.5 points) and filters far less. A null is an
acceptable result in this study; a pass enters the register as a closed verdict.

**What it fails on purpose.** A mixed predictor (table 26): a real within-sector effect whose β
is mostly a sub-industry component passes 40.5%. The verdict must not read such a failure as
"no effect"; `beta_sic3_cells` is printed so the split can be seen.

**What it does not fix.**

- A label at a grain finer than SIC-3. The 0% to 2% assumes SIC-3 holds the label fixed.
- The real SIC-3 grain is unknown. `gates.json` reports the share of rows alone in their cell
  before any β is read, and the verdict must quote it. No rule here adjusts for it.
- A cut that cannot be computed fails the rule. β and its SE are still reported.

**Considered and not adopted** (all measured, same tables): a fixed 3 pp floor in both windows
(above); the same floor on discovery only (23.5% to 38.0% of labels pass); the point floors of
rules 1 and 2 applied to the SIC-3 β, 10 pp and 6 pp (0.5% to 6.5% of labels, and less power
than half of β in most tables: 43.0% against 54.0% at 34% alone). Not measured: a floor scaled
to the SE of the SIC-3 estimate, which would add a multiplier and a second bootstrap.

### 3 — the reproduction gate, and the points the first text left open (2026-10-05)

**State of knowledge when made.** As for amendments 1 and 2: no SEC bulk archive had been
downloaded, and `fetch`, `gates`, `discovery` and `holdout` had never run on real data. One new
number was read, from the `frames` responses the first probe had already cached: the share of
each reproduction target with no `US-` address (`scripts/probe_durability_frames.py`, section 4).
No outcome, predictor or conditional rate enters it.

**Why now.** A review of the whole branch listed the places where the note and the code could
part, and the places where the note was silent and the code had chosen. Each is settled here
while a change is still free. After the gates run, the same change would follow a seen number.

**What changed. In place:** the paragraph under the header; one clause of "What has already
been seen"; the Data line of the definitions; the outcome-B row and one sentence under the
states table; the P1, P2 and P4 rows and a new paragraph under the predictor table; one sentence
added to the standard-error paragraph and one to the wrong-metric paragraph; a clause of the
Windows discovery line; the reproduction row of the gates table and two new paragraphs under
the table; four reported items; five limitations. The wording replaced is quoted in 3a, 3b, 3c
and here:

> **Amended twice on 2026-10-05 (the sector control; a fifth pass rule), both before any SEC
> bulk data was read.**
>
> … the per-year universe sizes used by the reproduction gate, and that companyfacts keeps
> nine named filers that stopped filing.
>
> `gates.json` records the SHA-256 of the compacted file.

Every other change is an addition; the text beside it is as registered.

**3a. The reproduction gate compares like with like.** The row first registered was:

> | reproduction | universe size per year 2011–2024 within ±15% of: 2114 · 2102 · … · 2179 | stop and diagnose |

The fourteen targets and the ±15% band are unchanged. What is counted against them changed.

| | the targets (SEC `frames`) | the study's universe | the comparison count (adopted) |
|---|---|---|---|
| sector mask | none | financials, REITs and insurers out | none |
| filing date | none | filed within 120 days of the year end | none |
| values | restated | as first reported | restated |
| forms | any | 10-K and 10-K/A | 10-K, 10-K/A, 20-F, 40-F and their amendments |

The mask, the filing date and the form filter each lower the study's universe against the
targets. Restated values move a firm either way across a floor. A band around the targets would
then have tested the size of those differences, not whether the archive was read correctly, and
a failure would have had to be explained after the numbers were seen.

The size of the mask and of the filing-date rule was **not measured**: the mask needs a SIC
code for every filer, and those come only from the bulk download. That is why no bar is set on
the study's universe or on its unmasked size. Both are written for every year, and the verdict
prints all three counts beside the target.

The form filter could be bounded. A filer on forms 20-F or 40-F is a foreign issuer, and many
filers with a non-US address file a 10-K, so the share of a target with no `US-` address (a
blank address included) is close to an upper bound on what a 10-K filter removes:

| year | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| no `US-` address | 10.1% | 9.4% | 9.1% | 8.8% | 8.3% | 8.3% | 9.4% | 9.8% | 10.6% | 10.6% | 10.4% | 9.7% | 10.0% | 10.6% |

Up to a tenth, against a band of 15%: a 10-K count would have left a margin of 4.4 points in
the worst year. So the comparison count reads the foreign annual forms too. The compaction
keeps their facts apart from the study's, each row read as a 10-K row, and only this count
reads them. A first draft kept the 10-K count and registered a recount with the foreign forms
as the one thing that could clear a failure on the low side. It was withdrawn before commit: a
rule applied after a failure, by code not yet written, is the discretion this note exists to
remove.

Differences that stay, of unknown sign and size and expected to be small: a filer whose only
annual facts for a year are on another form (a 10-KT, a registration statement) is in the
target and not in the count; the targets add the current portion of debt on top of
`LongTermDebt`; they take the balance sheet at the calendar year end, which for a non-December
filer comes from a 10-Q; and they read the revenue tags in another order.

What settles a failure is fixed now and not after one (§Gates): a place where the code does not
do what this note says. Nothing else clears the gate, and a wider tag list (the remedy of the
tag-artefact gate) is not such a fix. What the gate no longer tests is a
registered limitation: an error in the 120-day snapshot or in the sector mask would not fail
it, and it counts membership, not values.

**Considered and not adopted:** a wider band (a bar moved with no evidence); recounting the
targets with a sector mask (needs the bulk download before the gates); keeping the first gate
and reporting the other counts beside it (leaves a gate that is expected to fail for a known
reason, and a fix that would follow a seen number); a 10-K comparison count with a recount
rule (above).

**3b. `track` and `stability` start in 2012 — now enforced.** The Windows section registered
"`track` and `stability` are defined from 2012". The code did not enforce it: it defined both
whenever three of the four history buckets were observed. For start year 2011 those buckets are
2008 to 2011, and XBRL was phased in by filer size for fiscal periods ending after 15 June 2009,
2010 and 2011. A 2011 row with three observed buckets would be an early adopter, ranked against
a universe of early adopters. The code now leaves both predictors undefined for start year
2011. The registered sentence stands; a clause was added to it and to the P1 and P2 rows.

**3c. Outcome B: three readings the code had already fixed, now stated. No code change.**
The row first registered was:

> | **B `compounded`** | `held`, and revenue(`t+3`) / revenue(`t`) ≥ the cohort median of that ratio |

- *The median.* The text did not say over which rows. The code takes it within the cohort-year,
  over the rows with a determined `held` and a revenue ratio.
- *A row with no revenue ratio.* It is out of outcome B, whether it held or not. A firm that did
  not hold did not compound, so its B is known without the ratio, and a first draft of this
  amendment kept such rows as "not compounded". That was withdrawn before commit: keeping them
  while dropping the firms that held and have no ratio selects the outcome-B sample on outcome
  A, and moves β towards any predictor of `held`. The n of a predictor's B test against its A
  test shows how many rows this leaves out.
- *The bounds runs.* The text said `exit` is coded "all held, then all not held". For outcome B
  the code codes it all compounded, then all not compounded.

**3d. Readings the code had already fixed, now stated.** "≥ 5 peers" counts the firm itself.
For P1 and P2 the firm's own history years need a defined ROIC and not the revenue floor. No
code changed for either. One code change belongs here: `raw_tercile_spread` has no value when a
third of the predictor is empty; that used to make the whole test an error, and it now leaves β
alone.

**3e. The bootstrap must be complete.** The note registered 2,000 replications. The code left
out a replication whose resample could not be fitted and took the SE over the rest; with one
survivor the SE is 0 and the `2 × SE` arm is no hurdle. Now a test with any such replication is
recorded as an error and does not pass. β, the SE over the fitted replications and their count
are still written, so the verdict can tell "not computed" from "not shown". How often this
happens on real data was not measured. It is expected to be rare: a resample cannot be fitted
when too few of its rows share a cell, or when the predictor is an exact combination of the
controls. No constant was added: a tolerated share of failed replications would have been one.

**3f. Five limitations and four reported numbers were added.** The limitations: zero debt; the
52/53-week year end near 30 June; what the reproduction gate does not test; the revenue tag
order; thin early start years for `share_stability` and `incremental_roic`. None is new
behaviour: each describes what the code as registered already did. The numbers:
`zero_debt_share`, the three counts per year, `unreadable_members`, `n_by_year`. A change to the
182-day bucket shift was considered and not made: it is a registered constant, the last bucket
required of each completeness filer was derived with it, and the defect touches a few filers.

**3g. The procedure, not the definitions.** For the reader of the outputs: every output records
the data and the code it came from (§Definitions, Data), read when the step starts; the same
archive always compacts to the same bytes; no step runs while a file of that code differs from
its committed version; `discovery` refuses a `gates.json`, and `holdout` a `discovery.json`,
written from other data or other code; `discovery` needs `gates.json` committed; `holdout`
checks in git that the verdict note is committed with its `## Discovery` section, as the
Windows section already said it would; no step replaces its own earlier output until that
output is committed, so a failed gate is in git before the run that follows its diagnosis,
unless the file is deleted by hand; both archives are kept until the gates pass, and `fetch`
reads a kept archive again without a new download, so that run is on the same data.

**Nothing else changed.** The predictors and their signs, both outcomes, the outcome states,
the windows, the controls, the five pass rules and their bars, the other three gates, the
fourteen targets and the ±15% band, the decision rule, the forms the study reads (10-K and
10-K/A), and the bootstrap unit, count and seed are as registered.

**Evidence.** Section 4 of `scripts/probe_durability_frames.py` for the table above. The rest is
pinned by tests: `tests/test_durability_study.py`, `tests/test_durability.py`,
`tests/test_probe_durability.py`.

### 4 — the share-stability predictor, and a last review before the data (2026-10-07)

**State of knowledge when made.** As for amendments 1 to 3: no SEC bulk archive had been
downloaded, and `fetch`, `gates`, `discovery` and `holdout` had never run on real data. A full
dry run on a synthetic archive (6,000 filers, the registered 2,000 replications) had run, to
find crashes and the run time. One new table was read from the `frames` responses already
cached: the share of filers with none of the four revenue tags
(`scripts/probe_durability_frames.py`, section 5). No outcome, predictor or conditional rate
enters it.

**Why now.** A second reviewer read the whole branch with one question: what will be regretted
once the data has been read. It found two defects and one hole, below. This is the last
amendment that follows no seen number.

**What changed. In place:** the paragraph under the header; in the definitions, one sentence on
how year ends are located, a new line on facts filed before their period end, and "the Python
version" in the Data line; two sentences under the states table; the P4 row; one sentence and
one paragraph added under the predictor table; a clause of the tag-artefact row; under the
gates table, three sentences added to the tag-artefact paragraph and one new paragraph; three
reported items; three limitations. The wording replaced:

> **Amended three times on 2026-10-05 (the sector control; a fifth pass rule; the reproduction
> gate and the points the first text left open), all before any SEC bulk data was read.**
>
> | P4 | `share_stability` | abs. change in share of SIC-3 revenue, `t-3` to `t`; peers = all filers with revenue in both years; ≥ 5 peers, the firm included |

Every other change is an addition.

**4a. P4 is the relative change in share, not the absolute one.** This is a change to a
registered predictor. The absolute change in a share grows with the share: a firm with 40% of
its SIC-3 moves 2 points for a growth gap that moves a firm with 1% by 0.05 points. Ranked, the
registered form orders firms by their size inside the industry. On synthetic industries of 5 to
60 firms with lognormal sizes and growth that does not depend on size, its rank correlation
with the firm's SIC-3 share is −0.82 to −0.88; for the log ratio it is +0.05 to +0.09
(`scripts/probe_durability_share_form.py`, three settings, invented parameters). C2 holds the
revenue rank fixed, not the share, and the SIC-3 cells of rule 5 do not help, because the
variation is inside a SIC-3. A pass would have been read as "a stable share persists" and
would have meant "a small share persists", or the reverse.

The predictor is now −\|ln(share at `t` / share at `t-3`)\|, which is −\|ln firm growth − ln
SIC-3 growth\|. Its sign, its peers, the five-peer rule and its tests (A and B) are as
registered. The reviewer found the defect and it was measured again independently before the
change. A form that leaves the firm out of its own industry total was also measured (−0.01 to
+0.04); it is not adopted, because it is no longer a share and the gain is small.

**4b. A fact filed before its own period ended is never read.** `fiscal_ends` read every annual
fact to locate year ends. A year-long period typed with the year 2105 gave a filer a last
bucket of 2105: a dead filer then read as a `gap`, not an `exit`, was left out of the bounds
runs, and passed the completeness gate without being complete. A period typed one year ahead
put last year's values in a bucket the firm never reported. Real facts are expected to
satisfy end ≤ filed. The rule is applied to values and to year ends alike. How often such
facts occur in the archive is not known; `gates.json` counts the rows and the filers, and a
large count would say the expectation is wrong.

**4c. The revenue-tag hole is registered, not closed** (Limitations). Closing it needs other
tags, which are often a part of revenue and not the total, and it would move the comparison
count away from targets that are frozen.

**4d. Readings the code had already fixed, now stated. No code change:** how year ends are
located; that history floors and peer totals are read from snapshot `t`; the rank, floor and
standard-deviation conventions; that a late `t+3` filing is a `gap`; that the first and last
outcome years cannot be flagged by the tag-artefact gate; that `gates` builds the holdout
cohorts for the `gap` rate only.

**4e. The tag-artefact remedy** is as registered, with its consequences stated: the wider list
applies to the comparison count too, and every gate is judged again.

**4f. What a pass for `track` or `stability` means** is fixed before one is seen (Limitations).
In a world where ROIC is a firm level plus a year's noise and nothing else, `track` is expected
to show a positive β; whether it clears the bars depends on the share of noise. The decision
rule already gives that result its own row.

**4g. Reported numbers added:** `gap` and `low_ic` rate by predictor tercile;
`universe_no_revenue_tag` and its `…_assets_500m` part; `facts_filed_before_period_end`; the
Python version in every output. Procedure: `fetch.json` and `sic.json` are committed with
`gates.json`.

**Considered and not changed.** Bracketing `gap` in the bounds runs: it would tighten rule 3
by an amount nobody can state before the `gap` rate is known. Counting `low_ic` with positive
operating income as held: as registered. Hashing only the sector ranges of `config.yaml`: the
whole file stays frozen from `fetch` to `holdout`.

**Nothing else changed.** The other five predictors, both outcomes, the states, the windows,
the controls, the five pass rules and their bars, the four gates, their bands and targets, the
decision rule, and the bootstrap unit, count and seed are as registered.
