# PRE-REGISTRATION — the two durability passes: did profit hold, and are they one finding? (2026-10-07)

**Written and committed BEFORE any quantity below was computed.** It follows
`2026-10-04-moat-durability-verdict.md` and its Addendum 1.

**This is a decomposition of a result that has been seen. It is not a new test.** It cannot
add a pass, remove a pass, or change the verdict. It decides one thing: how a `/deep` line
(Phase 1) may word the two predictors that passed, `investment` and `stability`.

## What has been seen, and what has not

Seen: everything in the verdict note, including β by start year for all eleven tests on both
windows, and the `low_ic` rate by tercile.

Not seen: any of the four quantities below. No outcome built on a frozen denominator, no split
of ROIC into profit and capital, and no model with two predictors has been computed, on any
window.

## Why

The verdict passed `investment` (low growth of invested capital in the last year goes with
holding a top-quintile ROIC). The final review of the branch pointed out that the predictor is
the growth of the outcome's own denominator. Capital growth persists, so the result can come
from the denominator alone, with no difference in what the business earns. The verdict also
never showed that `investment` and `stability` are two findings and not one.

## Data and code

The cohorts of the verdict, rebuilt by the committed code from the same compacted file
(SHA-256 `2d617f70…aa5639`) and the same SIC map. The script refuses to run on other data or
on other study code (it compares the hashes in `holdout.json`), and until this note is
committed. Script: `scripts/probe_durability_decomposition.py`. Output:
`raw-2026-10-04-durability/decomposition.json`.

Predictors: `investment` and `stability`, as registered and oriented (higher = the favourable
end). Windows: discovery (start years 2011–2017) and holdout (2018–2021), separately.

The model is the registered one in every case: within SIC-2 × start-year cells,
`outcome ~ C0 + C0² + C2 + P`, ranks fixed from the cohort-year. Standard errors: bootstrap
over CIKs, 2,000 replications, seed 20261004. No rule below uses a standard error.

## The four quantities

Sample **S**: cohort rows with state `observed` at `t+3` (ROIC defined in snapshot `t+3`), the
predictor defined, and a SIC code. `low_ic`, `gap` and `exit` rows are out: without an invested
capital at `t+3` there is nothing to split.

| id | outcome, on sample S | what it is |
|---|---|---|
| **A** `beta_observed` | `held`, as registered | the reference: the registered β without the `low_ic` rows |
| **B** `beta_frozen` | 1 if NOPAT(`t+3`) / IC(`t`) ≥ the `t+3` top-quintile floor, else 0 | would the firm still clear the floor on the capital it had at `t`? |
| **C** `beta_log`, `beta_profit`, `beta_capital` | ln(ROIC(`t+3`)/ROIC(`t`)); ln(NOPAT(`t+3`)/NOPAT(`t`)); ln(IC(`t+3`)/IC(`t`)) — on the rows of S with NOPAT(`t+3`) > 0 | an exact split: `beta_log` = `beta_profit` − `beta_capital` |
| **D** `beta_alone`, `beta_joint` | `held` on the registered primary sample, rows where BOTH predictors are defined; `beta_joint` adds the other predictor's rank as a covariate | does each predictor keep its β with the other held fixed? |

NOPAT, IC and the floor are those of the study (`shortlist/durability.py`): NOPAT = operating
income × 0.79, IC = equity + debt, values as known in snapshots `t` and `t+3`.

## Reading rules, fixed now

**R1 — profit or denominator.** For each of the two predictors: if `beta_observed` > 0 and
`beta_frozen` ≥ half of `beta_observed`, **on discovery and on the holdout**, the reading is
"**operating profit held**": the firms the predictor favours would still clear the floor on
the capital they had. Otherwise the reading is "**not separated from a denominator effect**".

**R2 — one finding or two.** If, for both predictors, `beta_joint` ≥ half of `beta_alone`, on
discovery and on the holdout, the reading is "**two findings**". Otherwise "**not shown to be
two**".

**C decides nothing.** It is printed so the split can be seen: −`beta_capital` / `beta_log` is
the part of the predictor's association with the change in ROIC that runs through capital.

The constant 0.5 is the one rule 5 of the pre-registration already uses ("half of β"). No other
constant is added.

## What Phase 1 may say, by result

| R1 for `investment` | wording allowed |
|---|---|
| operating profit held | "firms that added little capital kept both their profit and their return" |
| not separated | a statement about the ratio and capital growth only: "a top-quintile ROIC lasted more often when invested capital had grown little; part or all of that is the denominator" |

| R2 | consequence |
|---|---|
| two findings | Phase 1 may show the two predictors as separate lines |
| not shown to be two | Phase 1 shows them as one line, and names both |

`stability` keeps its track-record label whatever R1 says for it.

## Limitations, stated before the fact

- **Post hoc.** The question was chosen after the verdict was read. That is why no result here
  can add a pass.
- **B is biased against the predictor.** A firm that invested more has more capital earning at
  `t+3`, so its profit over its OLD capital is flattered. A fail of R1 therefore does not show
  the effect is only accounting; it shows the effect was not separated from it. A pass of R1 is
  the strong result.
- **C conditions on a positive profit at `t+3`**, which is part of the outcome. It is a
  description of the firms that still earned a profit, and the rows it leaves out are counted.
- **A is not the registered β.** It leaves out `low_ic` rows, which the registered outcome
  codes by the sign of operating income. The difference between A and the registered β is the
  size of that coding, and it is printed.
- **D holds one more rank fixed and nothing else.** Two predictors can both keep half of their
  β and still share a cause.
- One author; the same data as the verdict.
