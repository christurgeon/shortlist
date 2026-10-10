# Backtesting (`shortlist.backtest`, CLI `shortlist-backtest`)

The scorer's weights and bands are validated against forward returns here
(closes `docs/ASSESSMENT_GAPS.md` §2.1). The backtest harness is **signal-agnostic**: the unit
of currency is an `Observation(as_of, ticker, {signal: 0–100 sub-score})`, and
every signal value is a sub-score produced by the **real** scoring functions —
not a reimplementation — so a future point-in-time fundamentals source slots in
without engine changes.

```bash
uv run shortlist-backtest --universe largecap --horizons 1,3,6,12   # rich table
uv run shortlist-backtest --universe smallmid --horizons 3,6 --json # the second universe
uv run shortlist-backtest --tickers AAPL,MSFT,LMT --json            # ad-hoc, JSON
```

**Two bundled universes, and a claim needs both.** `largecap` (~80 names) and `smallmid`
(~198) are separate files under `backtest/`; `--tickers` is an ad-hoc CSV alias for
`--universe`. A single-universe `t≈2` is noise at this scale — cross-universe reproduction
is the bar (`CLAUDE.md` → Design premise). Named universes are checked against SEC's current
ticker map on load and **refuse to run** if symbols have been renamed or delisted, since a
dead symbol contributes nothing while still counting toward breadth; `--allow-stale-universe`
overrides.

Grid and diagnostic flags: `--start` / `--end` bound the observation grid, `--step-months`
overrides the default non-overlapping spacing (`0` = step equals the horizon), `--buckets`
sets the quantile count (default 5), `--csv` writes per-signal rows, and `--residualize
TARGET~CTRL1,CTRL2` reports TARGET's **partial** rank IC after removing linear exposure to
the controls — the test for whether a candidate leg adds anything beyond legs already
scored. `--residualize` picks its own grid step per horizon and so rejects an explicit
`--step-months`.

What it computes per signal × horizon:
- **Rank IC** (Spearman) — two flavours: **time-series** (does a name's own
  momentum predict its own forward return; the primary metric for small
  watchlists) and **cross-sectional** (across the universe per date; gated until
  ≥ ~30 names/date). Aggregated to mean / std / ICIR / **t-stat** / **hit-rate**.
- **Quantile spread** — equal-weighted top-minus-bottom forward return + monotonicity.

How it stays honest:
- **No look-ahead.** Signals use only closes `≤ T`; forward returns use only data
  `> T`. The price layer reuses the live Yahoo leg math on a series **truncated at
  T** (`sources.snapshot_from_closes` → `bridge.snapshot_to_metrics` →
  `scoring.momentum_score`).
- **Non-overlapping** observation grid (step = the horizon) so the t-stat is valid
  without Newey-West.
- **Excess-over-SPY** returns by default (the momentum signal is itself
  benchmark-relative); `--return-mode raw` for absolute.
- **Survivorship caveat** printed every run: the bundled `largecap` universe is
  currently-listed names, so spreads are an upper bound — read results as
  *relative signal validation*, not tradeable PnL. Below the trust floor (~30
  names/date, ~24 periods) results are labelled **EXPLORATORY**.

**Three sources, and what each can validate.** `--source momentum` (the default) is
price-only and keyless. `--source xbrl` reconstructs the **fundamental** axes point-in-time
from SEC companyfacts and needs no accumulated history — it is what has actually measured
(and mostly killed) the candidate legs, and it drives `--fit`. `--source snapshot`
(`SnapshotSignalSource`) replays captured daily snapshots and is the only path to the axes
neither of the others can reach — SUE above all — so it stays **guarded** until
`shortlist-accumulate` clears both trust floors. Yahoo full daily history is fetched via
`period1=0` epoch params — **never `range=max`**, which silently degrades to quarterly bars.

## XBRL source: fundamental-axis IC without waiting (`--source xbrl`)

`XbrlSignalSource` (`backtest/signals.py`) reconstructs the quality / moat / growth / value
sub-scores point-in-time from SEC `companyfacts`, so it needs no snapshot accumulation. It
reads only filings with `filed ≤ as_of` (restatement-aware), resolves concept aliases by
priority, and feeds the metrics through the real `scoring.*_score` functions. The extractor
is `providers/_xbrl_facts.py` plus `backtest/xbrl.py` (keyless fetch, disk-cached under
`.cache/sec_xbrl`). Set `SEC_IDENTITY` to a contact email in `.env`; no API key is needed.

- **Memory-bounded.** Facts load lazily, one ticker at a time, and the engine iterates
  ticker-major, so peak RAM is one companyfacts plus a small LRU rather than the universe.
  `quantile_spread` tie-breaks on forward return so results do not depend on row order.
- **IFRS 20-F issuers** (`ifrs-full` rather than `us-gaap`) are skipped and return `None`.
- **Value coverage:** only `fcf_yield` and `pe_vs_history` are reconstructable. `peg` and
  `upside_to_target` need live FMP data, and the `insider` axis is absent.

**Standalone (non-production) axes.** The source also emits diagnostic axes so their rank IC
is measurable: `net_debt_to_ebitda`, `share_count`, `piotroski`, `asset_growth`, `accruals`
and `shareholder_yield`, each with collinearity pairs against the legs it could duplicate
(e.g. `accruals~piotroski`, `shareholder_yield~share_count`). Their scorers in `scoring.py`
are backtest-only; live status is in `docs/SCORING.md` → Optional legs.

> **These are settled, not open questions.** `share_count`, `asset_growth`,
> `shareholder_yield` and `piotroski` failed the cross-sectional bar, and `accruals` was
> killed on evidence (`docs/audits/2026-07-12-accruals-leg-disable.md`,
> `docs/audits/2026-08-18-net-debt-to-ebitda-remeasure.md`). Every one is OFF by default and
> should stay OFF. Read `docs/audits/README.md` before re-opening any of them; the axes stay
> wired so a *future* measurement is cheap, not because the verdicts are provisional.

**SUE is NOT an XBRL or live-price axis.** It needs Finnhub earnings, which neither the
price-only momentum source nor SEC companyfacts carries. `scoring.sue_score` and the
`sue~momentum` pair ride only the guarded snapshot-replay path (`SnapshotSignalSource`) and
no-op until accumulation captures the earnings fields. `momentum.sue` is OFF by default; see
`docs/SCORING.md` → Optional legs and `docs/PREDICTIVE_SIGNALS_RESEARCH.md` §1.

**Residual momentum IS a live-price axis.** `MomentumSignalSource` date-inner-joins the stock
and SPY series (`stats.join_on_dates`) and emits `residual_momentum` beside the production
`momentum` sub-score, with the `residual_momentum~momentum` pair. It passed the
cross-sectional bar, so `config.yaml: momentum.residual.enabled` ships `true`. See
`docs/SCORING.md` → Optional legs and `docs/PREDICTIVE_SIGNALS_RESEARCH.md` §2.

```bash
uv run shortlist-backtest --source xbrl --universe largecap --horizons 3,6,12 --json
```

Results below the trust floor (≥ 24 periods, ≥ 30 names/date) are labelled **EXPLORATORY**.
All results are survivorship-biased directional evidence, not fitted predictions.

## Fitting fundamental weights (`--fit`)

`--fit` (with `--source xbrl`) runs the walk-forward fitter (`backtest/fit.py`): coordinate
ascent on in-sample composite IC over the quality/moat/growth/value sub-weights, shrunk toward
the `config.yaml` prior and scored out-of-sample per fold. It emits a **proposal**, never a
config write.

```bash
uv run shortlist-backtest --source xbrl --fit --fit-horizon 6 --universe largecap
```

- **Scope is the four fundamental axes.** It speaks to within-block ratios; the block's total
  share stays an unfitted prior. `config-mapped` rescales the fitted ratios into that share.
- **Co-emission:** only (date, ticker) rows where all fitted axes are present are used.
- **Endorsement gate → PROPOSE or NO-CHANGE.** On the paired (shrunk-fit vs prior) OOS
  difference: ≥ 36 periods, ≥ 5 folds, mean edge ≥ +0.02, ≥ 4/5 folds positive, t ≥ 2.
  NO-CHANGE is the expected default on survivorship-biased data.
- `--fit-horizon` is required (ratios are horizon-conditional); `--n-folds`, `--shrink` and
  `--fit-axes` tune the run. Recorded results: `docs/audits/`.

## Feeding the snapshot path: accumulation (`shortlist-accumulate`)

The snapshot-replay and weight-fitting paths stay guarded until the store holds ≥ 24
organically captured daily snapshots. `shortlist-accumulate` builds that history: an
idempotent, per-ticker-isolated, point-in-time daily capture over `collect` + `store.save`.

```bash
uv run shortlist-accumulate run     --root snapshots   # capture today (idempotent)
uv run shortlist-accumulate status  --root snapshots   # both floors + SUE breadth + store size
```

- **Point-in-time integrity.** Captures only the current UTC day and rejects anything older.
  There is no backfill: restated data would reintroduce look-ahead.
- **Idempotent and frugal.** An already-captured ticker is skipped before any API call; errors
  are isolated per ticker and routed through `redact_secrets`.
- **Store.** Snapshots are gzipped (`<day>.json.gz`, atomic write in `store.py:save`); legacy
  `<day>.json` files stay readable, and a same-day twin resolves to the `.gz`.
- **Thin snapshots are saved.** `--min-coverage` is an opt-in save gate (default 0.0). A
  quota-gated symbol still saves its keyless sections as a THIN snapshot, which is what the
  SUE and replay axes need. A total-outage snapshot is always skipped.
- **Breadth vs the free tier.** The replay path needs ≥ 30 names/date to clear the trust
  floor, but `--max-tickers` defaults to 15, so a default run stays below it. Pass
  `--max-tickers 42` (the `deploy/` sample does); the FMP free quota then leaves the rest THIN.
  A paid FMP tier removes that limit (`docs/DATA_SOURCES.md`).
- **`status` reports both floors:** distinct capture dates (≥ 24) and dates meeting the
  30-name breadth floor, plus SUE breadth and store size. READY needs both.
- **Scheduling is OFF by default.** A disabled systemd sample lives in `deploy/`; enabling it
  is an explicit opt-in (`deploy/README.md`).
