# Roadmap — open ideas, declined ideas, and the literature behind the optional legs

A design-time list, not a description of the code. Current behaviour: [`SCORING.md`](SCORING.md),
[`RESEARCH.md`](RESEARCH.md), [`BACKTEST.md`](BACKTEST.md), [`HARNESS.md`](../HARNESS.md).
Settled verdicts: [`audits/README.md`](audits/README.md). Committed work items: `TODO.md`.

An idea here is a hypothesis. Anything that would move a score needs reproducible
cross-universe rank IC first (`CLAUDE.md` → Design premise); a `/deep` display line needs only
to help a human judge the name.

## Open ideas

### Scoring

- **Sector-relative scoring.** One absolute band cannot serve a 90%-margin software name and a
  25%-margin industrial. Rank each metric within its sector or the run universe instead. It
  needs enough peers per sector: a universe run (paid FMP tier, or SEC `frames` keylessly). The
  same peer data could give `/deep` a peer table, which the 2026-08-11 external review asked
  for first: display it before it feeds any score. Absolute momentum bands probably saturate in
  a bull market, so test relative momentum in the same work.
- **The sign of `upside_to_target`.** Brav & Lehavy (2003) find the *level* of target-implied
  upside predicts returns negatively; the *revision* predicts. The leg is the scorer's one
  opt-out block (`scoring.py:_upside_to_target_on`), so the leg-off counterfactual is a config
  run. Nobody has run it.
- **A momentum/value divergence flag.** `opportunity = max(momentum, value)` hides the case
  where momentum is falling and value looks cheap, the falling-knife pattern. An advisory flag
  would ask the deep dive "value trap or mispricing?".
- **Distress and manipulation flags.** Altman Z and Beneish M are both derivable from XBRL and
  would be advisory flags. Accruals were measured and disabled
  (`audits/2026-07-12-accruals-leg-disable.md`); going-concern language could not be validated
  (0 of 228 names).
- **The full Piotroski score.** `stats.piotroski_f` implements 6 of the 9 tests; the omitted
  ones (current ratio, new shares, asset turnover, ROA) need inputs added to both stacks.
- **A gate-impact backtest.** The stage-aware `negative_fcf` excuse and the leverage thresholds
  are unfitted priors. `XbrlSignalSource` emits sub-score axes and never calls `check_gates`, so
  measuring a gate needs a cohort comparison (excused vs gated names).
- **The dead `eps_revision` leg.** It is always `None`: no free point-in-time EPS-estimate
  history exists (Finnhub returns 403, FMP estimates are paid, Yahoo is IP-blocked on the VPS).
  The only route is accumulating it ourselves from day one.
- **Price refinements, all computable from the price history we hold.** 52-week-high proximity
  (George & Hwang 2004), the MAX effect (Bali, Cakici & Whitelaw 2011), vol-scaled momentum
  (Barroso & Santa-Clara 2015). None measured.
- **`beta` as a third risk leg.** Low value while `weights.risk` is 0.0.

### `/deep`

- **Earnings-call transcripts.** The most current management signal, but no keyless source.
- **DEF 14A narrative sections** (related-party transactions, CD&A). The proxy is ~350K
  characters; the shipped line reads only its structured XBRL fields.
- **Form 20-F issuers** (ASML, TSM, NVO). edgartools' `TwentyF` exposes the right sections, but
  a full-document parse OOM-kills the 1.9 GB VPS. Needs section-targeted extraction with peak
  memory measured on the VPS before it reaches the bot.
- **Industry data not written by the issuer** (Census, BLS PPI, EIA, openFDA,
  ClinicalTrials.gov, FDIC). The keyless endpoints answered from the VPS on 2026-08-23; there is
  no evidence any of it changes a judgement. If tried, land it as an auxiliary `Source` like
  `gov_contracts`, which reaches `/deep` without touching scores.
- **Options IV rank / percentile.** Needs a time series the CBOE feed cannot supply.

### Data and measurement

- **The FMP quota** is over-subscribed: `TODO.md` §4.
- **Feeds never wired, all unvetted:** Alpha Vantage (EPS estimates; 25 calls/day), Tiingo
  (a fundamentals cross-check), GDELT news tone, Wikimedia pageviews, Google Trends.
- **A portfolio simulation with turnover and costs**, the real "does the composite make money"
  test. Blocked on snapshot history, like the composite weight fit (`BACKTEST.md`).

## Declined

Not otherwise recorded in the register.

- **Congressional-trade following.** The cited alpha is pre-STOCK-Act (Ziobrowski 2004, 2011).
  On disclosed post-2012 trades the evidence is null to negative (Eggers & Hainmueller 2013;
  Belmont-Sacerdote et al. 2022). The 30–45-day disclosure lag and range-only amounts make
  conviction unknowable. Never a scored leg.
- **EV/EBIT, Magic Formula, Acquirer's Multiple as scored legs.** EV/EBIT correlated +0.72 with
  `fcf_yield` on the measured set; the others recombine legs already scored.
- **Reordering the rank by data coverage.** It double-counts absence (missing weight is already
  redistributed) and penalizes FMP-gated large caps for a subscription tier. `confidence` breaks
  exact ties only.
- **Reverse-DCF as a scored leg.** It has free parameters and no clean backtest; it ships as a
  `/deep` line instead.
- **Estimate dispersion** (Diether, Malloy & Scherbina 2002). No free per-analyst forecast data.

## Literature behind the optional legs

Status and config: `SCORING.md` → Optional legs.

| Leg | Config key | Evidence |
|---|---|---|
| Residual momentum | `momentum.residual` | Blitz, Huij & Martens (2011): about twice the risk-adjusted return of total-return momentum; Gutierrez & Pirinsky (2007). Some recent replications underperform. |
| Earnings-surprise drift (SUE) | `momentum.sue` | Bernard & Thomas (1989, 1990); Chan, Jegadeesh & Lakonishok (1996): drift survives price-momentum controls; Novy-Marx (2015). |
| Asset growth (inverted) | `quality.earnings_quality.asset_growth` | Cooper, Gulen & Schill (2008). |
| Accruals (inverted) | `quality.earnings_quality.accruals` | Sloan (1996). Disabled on our evidence. |
| Shareholder yield | `value.shareholder_yield` | Boudoukh, Michaely, Richardson & Roberts (2007): total payout beats dividend yield. |
| Insider conviction | `insider.conviction` | Purchases predict, routine sales do not: Lakonishok & Lee (2001); Cohen, Malloy & Pomorski (2012). |
| Piotroski (value-trap refinement) | `flags.value_trap.piotroski` | Piotroski (2000). |
| Rating change (display only) | — | Jegadeesh, Kim, Krische & Lee (2004): the change in consensus predicts, the level does not. |
