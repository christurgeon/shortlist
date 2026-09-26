# The `risk` tilt — measured, and weighted 0.0

**Date:** 2026-09-25 · **Change:** `weights.risk: 0.10 → 0.0`. The risk sub-score is still
computed and displayed (report, CSV, `--json`, `/deep`); it no longer moves the composite.
Committed evidence of record. Probe: `scripts/probe_composite_weights.py`. Raw output:
`raw-2026-09-25-composite/` — `largecap.json` / `smallmid.json` are the probe;
`backtest-{xbrl,mom}-{largecap,smallmid}.json` are the stock `shortlist-backtest --source
{xbrl,momentum} --universe <u> --horizons 1,3,6,12 --json --allow-stale-universe` runs the
per-leg numbers below (`value_pe_vs_history`, `value_fcf_yield`, `residual_momentum`) come
from.

## Why measured

`weights.risk: 0.10` shipped as an unfitted prior (`ASSESSMENT_GAPS.md` §2.9). Its own config
comment asked for a standalone rank-IC backtest before it was trusted, and that backtest was
never run. The 2026-08-11 external review argued for taking risk out of the composite; this
repo declined because that was an argument, not evidence. This note supplies the evidence.

Nothing blocked the measurement. `realized_vol` and `max_drawdown` come from price history, so
the price-history backtest can rebuild the leg on the same universes as every other verdict.
Snapshot replay cannot rebuild it: the accumulate chain omits Yahoo, so no stored snapshot
carries `realized_vol` (`TODO.md` §3).

## Method

- Universes: the two committed ones, `largecap` (80 names) and `smallmid` (148 after 5
  delisted or renamed symbols drop out). Grid from about 14 months after the earliest price.
  Horizons 1/3/6/12 months, non-overlapping. Forward returns are excess over SPY. Price as-of
  date 2026-09-25.
- Axes rebuilt point-in-time. `quality`, `moat`, `growth` and `value` come from SEC
  companyfacts. `value` has 2 of its 4 legs here (`fcf_yield`, `pe_vs_history`): `peg` and
  `upside_to_target` need analyst history that nobody stores. `momentum` uses the production
  legs, including `residual_momentum`; `eps_revision` has no history. `risk` is
  `scoring.risk_score` on the price series truncated at the grid date. `insider` is absent,
  and every composite renormalizes over the axes present, as `scoring.score()` does.
- Per-date cross-sectional (XS) Spearman IC uses the engine's breadth floor
  (`_TRUST_MIN_BREADTH` = 30). The per-signal means reproduce `shortlist-backtest`'s `xs_ic`
  exactly. For example, large-cap standalone `growth` at h=1 is +0.0269 (t 1.82) in both
  `largecap.json` and `backtest-xbrl-largecap.json`.
- The composite variants were fixed before any composite result was seen: `cur` (shipped),
  `no_risk`, `equal` (1/N), `lit` (a literature prior) and `fund` (fundamentals only). A
  second round, fixed before it ran, rebuilt `momentum` from the residual leg alone. **This
  is a weak pre-registration**: it lives only as a code comment committed with the results,
  not in a separate dated note like `2026-08-25-*-prereg.md`. The per-leg `xs_ic` for every
  axis had already been read when the variants were written, and the standalone bar applied
  below is borrowed from the accruals note after the fact.
- The paired test is the per-date IC difference between two composites on the same dates and
  the same names, with a t-statistic over those dates.

**Instrument trap, caught before it cost a conclusion.** A first paired run used
`_per_date_ic` with no breadth floor. That added about 25 thin early small/mid dates, and on
those dates the shipped composite's mean IC read −0.007 instead of the engine's +0.006. Any
paired number that does not reproduce the engine's per-signal `xs_ic` is measuring different
dates. The probe applies the floor for this reason.

## Results

### `risk` standalone: wrong sign in both universes at every horizon

XS rank IC, mean (t):

| universe | h=1 | h=3 | h=6 | h=12 (exploratory) |
|---|---|---|---|---|
| largecap `risk` | −0.040 (−1.89) | −0.066 (−1.85) | −0.075 (−1.36) | −0.078 (−1.07) |
| largecap `risk_vol` leg | −0.046 (−2.13) | −0.076 (−2.06) | −0.099 (−1.75) | −0.091 (−1.18) |
| largecap `risk_dd` leg | −0.027 (−1.42) | −0.043 (−1.34) | −0.046 (−0.97) | −0.056 (−0.95) |
| smallmid `risk` | −0.017 (−1.12) | −0.025 (−0.94) | −0.023 (−0.56) | −0.027 (−0.42) |
| smallmid `risk_vol` leg | −0.013 (−0.89) | −0.020 (−0.83) | −0.015 (−0.43) | −0.008 (−0.14) |
| smallmid `risk_dd` leg | −0.016 (−1.06) | −0.024 (−0.89) | −0.025 (−0.62) | −0.036 (−0.60) |

On large cap the volatility leg is the worse half. On small/mid the drawdown leg is.

The shipped leg orders names so that the calmer ones score higher. On these universes, the
calmer names did worse over the next 1–12 months.

### The composite without it: better in all 8 cells, significant in none

| universe | h | `cur` XS IC (t) | `no_risk` XS IC (t) | paired diff (t) |
|---|---|---|---|---|
| largecap | 1 | +0.0145 (1.20) | +0.0216 (1.78) | +0.0070 (1.82) |
| largecap | 3 | +0.0152 (0.69) | +0.0266 (1.25) | +0.0114 (1.62) |
| largecap | 6 | +0.0347 (1.07) | +0.0500 (1.57) | +0.0153 (1.53) |
| largecap | 12 | +0.0421 (0.79) | +0.0549 (1.07) | +0.0128 (1.00) |
| smallmid | 1 | +0.0063 (0.62) | +0.0079 (0.85) | +0.0017 (0.56) |
| smallmid | 3 | +0.0141 (1.01) | +0.0167 (1.23) | +0.0026 (0.50) |
| smallmid | 6 | +0.0202 (0.98) | +0.0208 (1.11) | +0.0005 (0.08) |
| smallmid | 12 | +0.0344 (1.15) | +0.0354 (1.30) | +0.0009 (0.09) |

Among the other pre-registered variants, `equal` was the worst in every cell. `lit`'s XS IC
was within 0.004 of `no_risk` in every cell. `fund` (price axes dropped) and `no_risk_r`
(residual-only momentum) beat `no_risk` by a paired t of at most 1.19. Nothing past dropping
risk is supported.

### Axis correlations (mean per-date rank correlation, 3-month grid)

Risk is close to orthogonal to value and moat. It is mildly negative with growth
(−0.20 / −0.17) and mildly positive with momentum (+0.08 / +0.24). So it is not a
duplicate leg that could be dropped for free; it is a separate bet with the wrong sign.

## Verdict: weight 0.0

The bar for keeping a leg is the one that disabled accruals (`2026-07-12`): XS IC positive,
with t > ~2, on a reproducible universe at h ≤ 6. `risk` is **negative** on both universes at
every horizon, so it fails on sign, not just strength. **The verdict does not rest on the
composite improving.** That gain is consistent but not significant (large cap paired
t 1.0–1.8, small/mid about 0), and a pre-registered alternative that beat the shipped weights
by t < 2 would not justify a change on its own.

**Why `0.0` and not a deleted key.** Deleting `weights.risk` sets `risk_on` to False, and
`card.risk` becomes None everywhere: report column, CSV, `--json` and the `/deep` prompt. The
review's point was that low trailing volatility is a preference, not an expected-return claim,
and that it belongs on the display, not in the composite. `0.0` does exactly that. `score()`
still computes `ri`, and a zero-weight part leaves the weighted average unchanged whenever
any other component is present. The one exception is a card where risk is the only part:
the denominator is 0 and the composite posts 0.0 (it posted the risk score before).
`min_composite_components` already marks that card unscored, and
`tests/test_scoring_composite_floor.py` pins the 0.0.

**Side effect worth knowing.** On a card where momentum is the only real axis, for example a
Yahoo-only run, risk had been **56%** of the composite (0.10 of 0.18). Illustration from an
uncommitted live 4-name `--provider yahoo` run: KO moved 72.6 → 46.3 and AAPL 73.0 → 64.9,
which is exactly the momentum sub-score. The arithmetic checks: (0.08 × 46.3 + 0.10 × 93.7)
/ 0.18 = 72.6. On a fully-sourced card, risk was about 10% of the weight.

## Why risk, and not moat — the rule is not "negative sign means disable"

`moat` (weight 0.18) is **also** negative on both universes at every horizon:

| universe | h=1 | h=3 | h=6 | h=12 |
|---|---|---|---|---|
| largecap `moat` | −0.007 (−0.61) | −0.013 (−0.67) | −0.015 (−0.62) | −0.054 (−1.59) |
| smallmid `moat` | −0.009 (−0.66) | −0.009 (−0.39) | −0.022 (−0.70) | −0.036 (−0.69) |

The raw momentum legs are negative on small/mid, and no axis clears "positive, t > ~2, at
h ≤ 6" on both universes. So a sign rule on its own would disable `moat` too. Two things
separate the cases, and neither is survivorship bias, which cuts the same way for both (the
dead names were high-volatility *and* low-moat):

1. **Magnitude.** At h ≤ 6, risk's large-cap XS IC is 5–6× moat's (−0.040 / −0.066 / −0.075
   against −0.007 / −0.013 / −0.015), and its volatility leg reaches t −2.1. Small/mid is
   close to a tie, so this rests on large cap.
2. **What the leg is for.** The design premise (`CLAUDE.md`; `TODO.md` header) is a triage
   funnel where "it is fine for a signal to have no measurable edge". A moat is part of what
   the funnel screens *for*: the human deep dive checks exactly that. Risk was added as a
   composite tilt explicitly pending validation (`ASSESSMENT_GAPS.md` §2.9, and its own
   config comment). Its preference role, "how much a name hurts to hold", survives on the
   display.

The second point is a judgement, not a measurement. Moat's negative IC is recorded, not
dismissed. `TODO.md` §3 carries it, and any moat weight change needs its own pre-registered
variant.

## Caveats — read before reopening

- **Survivorship bias works AGAINST this leg** (and equally against `moat` and `quality`).
  Both universes are names listed today. The high-volatility names that went to zero are
  missing, so the volatile side of the ranking looks better than it really was. Do not cite this note as "low volatility underperforms".
  It shows that the leg's value is **not demonstrated** on the data this repo has. The
  low-volatility literature (Ang et al. 2006; Frazzini-Pedersen 2014) is also about
  *risk-adjusted* returns. A raw excess-return rank IC does not measure that benefit, and a
  ranking funnel cannot deliver it anyway.
- 2010s–2026 was a long bull market, and low-volatility names lag in rallies. The sample does
  not cover a full regime cycle.
- **Reopen only on a survivorship-free, delisting-adjusted universe**, not on another run over
  these two.

## Measured in the same run, NOT acted on

- **Residual-only momentum.** Rebuilding `momentum` from `residual_momentum` alone beats the
  shipped momentum axis on small/mid (paired t +1.26 / +1.49 / +1.46 at h=1/3/6; standalone
  t 2.17 at h=6). On large cap it is flat to negative (paired t +0.51 / +0.56 / −0.03).
  Because it does not reproduce across universes, momentum is unchanged. The raw legs
  (`price_vs_200dma`, `rel_strength_6m`) remain the weak part of that axis.
- **`pe_vs_history` changes sign between universes.** Standalone XS IC is negative on large
  cap (h=1/3/6: −0.007 / −0.009 / −0.023) and positive on small/mid (+0.023 / +0.025 /
  +0.026). `fcf_yield` is positive on both. No change: a leg with an unstable sign is noise
  until measured otherwise, and the combined `value` axis is positive on both universes.
- **`quality` and `moat`** are about 0 and slightly negative XS on both universes. The same
  survivorship caveat applies with more force: dead low-quality names are the ones missing.
  Nothing changed. `TODO.md` §3 tracks the `roe` band saturation found in the same session.

## Reproduction

```bash
set -a && . ./.env && set +a
uv run python docs/audits/scripts/probe_composite_weights.py largecap > lc.json
uv run python docs/audits/scripts/probe_composite_weights.py smallmid > sm.json
```

The Yahoo and companyfacts caches must be warm (a `shortlist-backtest --source xbrl` run warms
both). Peak RSS was about 380 MB per run on oracle-prod. The variant weights are hard-coded in
the probe, so it measures the same thing whatever `config.yaml` ships.
