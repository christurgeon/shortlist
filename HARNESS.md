# Data harness (`shortlist.data`)

The collector/extractor layer: given one or more tickers, every source fetches
everything it can, the collector merges it into a single **assessment-ready
snapshot**, and the raw payloads are retained as a point-in-time record.

This is the foundation the scorer, the predictor, and any Claude feature-step
sit on top of — it deliberately knows nothing about scoring or predictions.

## Run

```bash
# Offline demo (no keys):
uv run shortlist-harness --tickers GEV,LMT,SCHW --sources mock --out snapshots

# Live (keys from the environment or a .env file):
uv run shortlist-harness --tickers GEV,LMT --sources fmp,finnhub --out snapshots --print
# equivalently: uv run python -m shortlist.data.cli --tickers GEV,LMT --out snapshots
```

**Caching.** FMP and Finnhub responses are cached on disk (`.cache/http.sqlite`,
gitignored) **by default**, so a warm re-run of the same tickers within TTL makes
zero upstream calls — the fix for the free-tier daily-quota ceiling. `--no-cache`
disables it for a run; `--refresh-cache` bypasses cached responses and repopulates.
Yahoo and FINRA keep their own per-day / per-settlement caches; EDGAR (free, uncapped)
is uncached. TTLs are per data half-life (`config.yaml: cache.ttl`); full design in
`docs/DATA_SOURCES.md` §6.

Output per ticker is a coverage line — the harness's honest answer to "do we
have what we need?":

```
GEV    coverage=  96% [ok] sources=mock -> snapshots/GEV/2026-05-31.json
SCHW   coverage=  82% [ok] sources=mock  missing: fundamentals.roic, fundamentals.gross_margin, ...
```

## What a snapshot contains

A `TickerSnapshot` (see `models.py`) with six normalized objects — `profile`,
`fundamentals`, `statements` (5y), `analyst`, `insider`, `price` — plus:

- `raw`: every source's verbatim payloads, kept for audit and point-in-time replay
- `provenance`: which source supplied each object
- `errors`: per-section failures (one bad endpoint never kills the run)
- `coverage()` / `missing()`: completeness, so thin data is visible not silent

## How sources combine (`merge_snapshots`)

Each `Source` owns both fetching raw and normalizing its own payloads, then the
collector merges by priority (`yahoo > edgar > fmp > finnhub > mock`). Flat objects
(`profile`, `fundamentals`, `analyst`, `price`) merge **field-by-field** — a gap
in the primary source is filled from the next. **Yahoo leads** so its keyless,
auditable price/momentum (200dma, 6m rel-strength vs SPY, realized vol, max
drawdown — all computed by us, day-cached under `.cache/yahoo/`) wins the `price`
merge and survives FMP's per-symbol gating; FMP backfills any price field Yahoo
lacks. `statements` takes the best single
source (you don't interleave fiscal years). `insider` has a **bespoke merger**:
the coupled transaction facts (`net_value_6m`, `buy_count`, `sell_count`,
`recent`) come wholesale from the highest-priority source that has trades — so the
dollar figure and the counts always describe the *same* trades — while
`sentiment_mspr` (Finnhub's MSPR, which no transaction source supplies) is filled
independently. EDGAR's authoritative flow and Finnhub's sentiment thus compose.
Async fan-out (`asyncio.gather`) runs tickers and sources concurrently.

## EDGAR source — insider trades and 10-K financials

`EdgarSource` supplies two independent sections:

1. **Form 4 insider trades** — aggregated buy/sell flow via the shared `providers/_form4.py` leaf module (also used by the XBRL backtest).
2. **10-K financial statements** — revenue, net income, operating cash flow, free cash flow, diluted EPS, and (ASSESSMENT_GAPS §2.7) **balance-sheet debt/cash + leverage inputs** — total debt (long-term + current portion + short-term), cash & equivalents, operating income, D&A (from the cash-flow statement), and interest expense — for the latest ~3 fiscal years (absolute USD), sourced from the company's most recent annual filing via `get_financials()`. The bridge derives `ebitda` (operating income + D&A), `net_debt_to_ebitda`, and an `interest_coverage` fallback (FMP keeps priority where present). These drive the net-debt/EBITDA `over_leveraged` gate, so the EDGAR backfill matters most for the FMP-402-gated large-caps that gate targets. **edgartools caveat:** these read edgartools' normalized `standard_concept` buckets (e.g. cash = `CashAndMarketableSecurities`, D&A = cash-flow `DepreciationExpense`), NOT raw us-gaap tags, and balance-sheet columns are **instant dates** (no `(FY)` suffix) — validated against a live AAPL filing in `tests/test_edgar_leverage_live.py`.

Both sections are failure-isolated: a missing XBRL filing (Form 20-F foreign issuers, recent spin-offs) degrades the statements to `None` gracefully without affecting insider data or crashing the run.

The source runs the synchronous `edgartools` work in a worker thread
(`asyncio.to_thread`) and funnels all EDGAR fetches through a shared semaphore
(`_EDGAR_MAX_CONCURRENCY`, default 3). SEC enforces ~10 req/s fair-access per IP
and each ticker pulls many filings, so the collector's per-ticker
semaphore is *not* enough — the EDGAR gate is what keeps a universe run under the
limit. The `get_financials()` call roughly doubles per-ticker EDGAR SEC requests
vs. Form 4 alone; a full-universe run still needs the caching layer. Requires the
`[edgar]` extra and `SEC_IDENTITY`; absent either, the source is skipped (not fatal).

### Bridge derivations from EDGAR + Yahoo (value legs when FMP is absent)

When FMP has gated a symbol (`402`), `bridge.py:snapshot_to_metrics` derives two value-axis legs from free sources:

- **`fcf_yield`** = latest-annual FCF (EDGAR 10-K) ÷ market cap (backfilled from Finnhub or Yahoo when FMP is absent).
- **`pe_vs_history`** = current P/E vs. own trailing median, using EDGAR annual diluted EPS + Yahoo historical closes. `YahooSource` now fetches 5 years of monthly-sampled dated closes; the bridge matches each fiscal-year-end to the nearest available close to reconstruct annual P/E.

**Limitations of the EDGAR+Yahoo value derivation:**

- History is ~3 fiscal years (10-K depth), not 5 — the trailing P/E median window is shorter than FMP's.
- Closes are monthly-sampled, not exact fiscal-year-end prices — P/E reconstruction is approximate.
- `pe_ttm` uses the latest annual EPS as a TTM proxy, not true trailing-twelve-months earnings.
- Symbols with no XBRL financials (Form 20-F foreign issuers, recent spin-offs) degrade both derived legs to `None` gracefully.
- PEG and analyst-target upside still require FMP — they are not recoverable from free sources.

## Scoring a snapshot — the bridge

`data/bridge.py:snapshot_to_metrics()` converts a `TickerSnapshot` into the flat
`StockMetrics` that `scoring.score()` consumes:

```bash
uv run shortlist --tickers GEV,AXON
```

The bridge **derives** several fields the snapshot doesn't store directly, all
from the 5y `Statements` via the shared `shortlist.stats` helpers (also used by the
XBRL backtest): `gross_margin_stability`, `fcf_positive` (most-recent free cash flow),
and the **growth** legs `revenue_cagr` / `fcf_cagr` / `eps_cagr` (net-income proxy)
/ `revenue_growth_persistence`. It surfaces Yahoo's `realized_vol` and
`max_drawdown`, which now feed the scored **7th risk axis** — a composite-only
tilt (sector-neutral, deliberately excluded from `confidence`/`scored`; see
`CLAUDE.md` → the risk sub-score).

`FMPSource` fetches annual `ratios` and `key-metrics` history, so the bridge
maps `pe_median_5y` (`value` runs on the full 4 legs, via the shared
`shortlist.stats.median_pe` helper) and `roic_5y_avg` (moat uses the 5y ROIC
average instead of falling back to TTM `roic`, via `shortlist.stats.avg_roic`).
The one **accepted coverage gap** is `eps_revision` (Alpha Vantage, out of scope) —
it maps to `None` and the scorer redistributes weight. Cards carry a `coverage`
diagnostic built by `data/coverage_adapt.py` from the snapshot's provenance/errors;
the snapshot's own `coverage()`/`missing()` also remain available via
`shortlist-harness`.

**Sector-aware abstention.** The SIC that drives sector detection comes **only**
from EDGAR: `EdgarSource` emits a partial `Profile(sic=…)` (merged field-by-field,
so SIC survives even when FMP/Finnhub gate the profile) and the bridge copies it to
`m.sic`. `score()` reads `m.sic` (never the free-text `Profile.sector`). The harness
pays one extra lightweight SEC request per ticker for the SIC lookup (`EdgarSource`
has no reusable `Company` handle), bounded by the EDGAR concurrency semaphore. See
`CLAUDE.md` → "Sector-aware applicability & abstention".

## Short interest and soft flags

`FinraSource` (keyless) leads no merge but fills the `ShortInterest` snapshot
section — current/previous short shares, days-to-cover, settlement date, split/
revision flags. It does **one bulk fetch per run** (the latest consolidated cycle),
caches it by settlement date, and indexes every symbol in memory; per-ticker
lookups are local. It therefore adds **no per-ticker request load** to a universe
run. The bridge derives `short_pct_outstanding` (vs. derived shares outstanding,
conservative vs. float), `days_to_cover`, `short_interest_rising`, and
`short_data_age_days` onto `StockMetrics`.

**Soft `flags` vs. hard `gates`.** `gates` are hard filters that flip
`ScoreCard.passed` to `False`. **`flags`** are *advisory* — they annotate a card
but **never change `composite` or `passed`**. The `crowded_short` flag fires only
with `finra` present, when
`short_pct_outstanding ≥ threshold AND days_to_cover ≥ threshold AND rising AND
fresh` (thresholds in `config.yaml` → `flags.crowded_short`:
`min_short_pct_outstanding`, `min_days_to_cover`, `require_rising`,
`max_staleness_days`). It marks a name for a closer look — squeeze candidate or
credible skeptic case — without altering the rank.

## Adding a source

Subclass `Source`, implement `async def fetch(ticker) -> SourceResult` returning
verbatim `raw` plus a normalized `partial` `TickerSnapshot`, and register it in
`_REGISTRY` in `sources.py`. (Yahoo, FMP, Finnhub, EDGAR, FINRA, WSB, and Mock are all wired.)

## Backtesting (`shortlist.backtest`, CLI `shortlist-backtest`)

The scorer's weights and bands are validated against forward returns here
(closes `ASSESSMENT_GAPS.md` §2.1). The backtest harness is **signal-agnostic**: the unit
of currency is an `Observation(as_of, ticker, {signal: 0–100 sub-score})`, and
every signal value is a sub-score produced by the **real** scoring functions —
not a reimplementation — so a future point-in-time fundamentals source slots in
without engine changes.

```bash
uv run shortlist-backtest --universe largecap --horizons 1,3,6,12   # rich table
uv run shortlist-backtest --tickers AAPL,MSFT,LMT --json            # ad-hoc, JSON
```

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

**Scope today (v1):** the **momentum axis** is validated on real data (price-only,
keyless). The composite, fundamental sub-scores, **weight-fitting** (walk-forward
+ shrinkage toward the prior, `backtest/fit.py`) and the **snapshot-replay** source
(`SnapshotSignalSource`) are built, tested, and **guarded** — they activate once
point-in-time fundamentals accumulate (organic daily `store.py` captures or the
EDGAR-XBRL source in `DATA_SOURCES.md` A1). Yahoo full daily history is fetched via
`period1=0` epoch params — **never `range=max`**, which silently degrades to
quarterly bars.

### XBRL source: fundamental-axis IC without waiting (`--source xbrl`)

`XbrlSignalSource` (`backtest/signals.py`) reconstructs the **quality / moat /
growth / value** sub-scores point-in-time from SEC `companyfacts`, bypassing the
24-date snapshot accumulation clock entirely. It reads only filings with
`filed ≤ as_of` (restatement-aware), resolves concept aliases by priority (not
merge), and feeds the extracted metrics through the real `scoring.*_score`
functions — the same path as a live screen. The extractor is `providers/_xbrl_facts.py`
(pure, stateless leaf) + `backtest/xbrl.py` (keyless companyfacts fetch, disk-cached
under `.cache/sec_xbrl`).

**Requirements:** set `SEC_IDENTITY` to a contact email in `.env` (SEC fair-access
`User-Agent`); no API key is needed otherwise.

**IFRS 20-F foreign issuers** (facts filed under `ifrs-full` rather than `us-gaap`)
are **skipped** — their concept names don't map to the extractor's alias tables, so
they return `None` cleanly rather than producing garbled scores.

**Value coverage:** 2 of 4 value legs are reconstructable from XBRL (`fcf_yield`,
`pe_vs_history`). `peg` and `upside_to_target` require live FMP data and are
**not** available in the XBRL path. The `insider` axis is not reconstructable from
XBRL and is absent from this source.

**Standalone (non-production) axes.** Beyond the four production sub-scores, the
source also emits three diagnostic axes so their rank IC is measurable before they
graduate to the live composite: `net_debt_to_ebitda` (the leverage input behind the
`over_leveraged` gate), `share_count` (dilution), and `piotroski` (fundamental
quality). Their scorers (`scoring.py:share_count_score`/`piotroski_score`) are
backtest-only — not production sub-scores. All three are **unfitted priors**; this
is how we validate them point-in-time. See `CLAUDE.md` → dilution / Piotroski / gate
notes.

```bash
uv run shortlist-backtest --source xbrl --universe largecap --horizons 3,6,12 --json
```

On a largecap universe, 3m/6m horizons (≥32 periods) clear the engine's trust gates
of ≥24 periods and ≥30 names/date; 12m is flagged **EXPLORATORY** at 16 periods.
All results are early, survivorship-biased directional evidence — treat them as
signal diagnostics, not fitted predictions.

### Fitting fundamental weights (`--fit`)

`--fit` (with `--source xbrl`) wires the built-in walk-forward fitter
(`backtest/fit.py`) to the XBRL source: it fits the **fundamental** sub-weights
(quality/moat/growth/value) by coordinate ascent on in-sample composite IC, shrinks
50 % toward the `config.yaml` prior, and scores each fold out-of-sample. It emits a
**proposal**, never a config write.

```bash
uv run shortlist-backtest --source xbrl --fit --fit-horizon 6 --universe largecap
uv run shortlist-backtest --source xbrl --fit --fit-horizon 3 --universe largecap --json
```

- **Scope is the 4 fundamental axes only** (momentum/insider/risk aren't in XBRL). The
  fit speaks to the **within-block ratios**; the fundamental block's *total share*
  stays an unfitted prior. The `config-mapped` column rescales the fitted ratios into
  the block's current share.
- **Co-emission:** only (date, ticker) rows where **all** fitted axes are present are
  used, so every composite the fitter scores is an apples-to-apples blend.
- **Endorsement gate → PROPOSE or NO-CHANGE.** A config change is endorsed only if, on
  the per-fold **paired** (shrunk-fit vs prior) OOS difference: ≥36 periods, ≥5 OOS
  folds, mean edge ≥ +0.02, ≥4/5 folds positive, and t-stat ≥ 2. These are deliberately
  hard to clear on survivorship-biased data — **NO-CHANGE is the expected default**.
- `--fit-horizon` is required (fitted ratios are horizon-conditional); `--n-folds`
  (default 6), `--shrink` (default 0.5), and `--fit-axes` tune the run.

The bundled-largecap result (2026-06) is **NO-CHANGE** at both 3m and 6m (6m is below
the period floor; 3m clears it but the paired OOS edge is only +0.005 vs the +0.02 bar,
positive in just 2/5 folds).

### Feeding the snapshot path: accumulation (`shortlist-accumulate`)

The snapshot-replay/weight-fitting paths above stay guarded until the store holds
≥ 24 organically-captured daily snapshots. `shortlist-accumulate` builds that
history — an **idempotent, per-ticker-isolated, point-in-time** daily capture over
`collect` + `store.save` (now an atomic write):

```bash
uv run shortlist-accumulate run     --root snapshots            # capture today (idempotent)
uv run shortlist-accumulate status  --root snapshots            # "N / 24 needed -> READY|NOT READY"
```

- **Point-in-time integrity:** captures **only the current UTC day** (`as_of` =
  utcnow); a snapshot older than the run day is rejected — **no backfill**, because
  backfilled/restated data would reintroduce look-ahead into the backtest.
- **Idempotent + frugal:** an already-captured ticker is skipped *before* any API
  call. Errors are isolated per ticker and routed through `redact_secrets`.
- **Thin-gate:** snapshots below `--min-coverage` (default 0.5) are flagged THIN and
  **not saved**, so a gated/empty symbol (FMP per-symbol 402) can't pollute the
  backtest as if it were real signal. (Use `--min-coverage 0` for price-only runs.)
- **Free-tier aware:** `--max-tickers` defaults to 15 (≈195 < FMP's 250/day);
  default watchlist avoids the 402-gated symbols. Scale needs paid FMP or caching.
- **Scheduling is OFF by default.** A disabled systemd sample lives in `deploy/`;
  enabling a daily timer is an explicit opt-in (`deploy/README.md`).

## Known limitations (next hardening pass)

- Dataclasses, not pydantic — bad payloads normalize to `None` rather than failing loud.
- **Free-tier daily quota, not per-call backoff, is the scale ceiling.** Caching
  (`cache.py`, on by default) and FMP's `Retry-After`-aware `429` backoff now exist,
  so warm re-runs and transient throttling are handled — but FMP's 250/day free
  limit still caps a cold full-universe run (~19 tickers/day on the harness path).
  Paid FMP Starter or a warm cache is the fix.
- **FMP's free plan gates many symbols** (e.g. GEV) behind premium with a `402`
  "Special Endpoint" on a per-symbol basis — coverage correctly drops to "thin"
  for those names. Major large-caps (AAPL/MSFT/LMT) work on the free tier.
  `fcf_yield` and `pe_vs_history` are partially recoverable from free EDGAR + Yahoo
  data (see bridge derivations above); PEG and analyst-target upside still require FMP.
- **Sector miscalibration, not blank fields, is the residual gap for banks/REITs.**
  Equity-centric legs that are structurally undefined for a sector are now SIC-detected
  and explicitly **abstained** (not silently averaged) — see `CLAUDE.md` →
  "Sector-aware applicability & abstention". What remains deferred is sector-specific
  *recalibration* of the surviving legs (`net_margin` is defined but miscalibrated).
- Mock data is illustrative, not verified.

## Engine history: the screener was retired (Phase C, done)

The harness is now the **only** engine. The legacy synchronous screener providers
(`providers/fmp.py`/`finnhub.py`/`edgar.py`), `merge.py`, the screener `run()`, and
the `--engine` flag were **removed** — the async harness `Source`s in
`data/sources.py` are the sole production data layer. The shared leaves
`providers/_form4.py` and `providers/_edgar_facts.py` were **kept** (the harness
sources and the XBRL backtest depend on them), as were the `Provider` base +
`MockProvider` (a lightweight offline `StockMetrics` factory the scoring tests use)
and the `quiver`/`fred` scaffolds (awaiting a harness-side `Source`).
