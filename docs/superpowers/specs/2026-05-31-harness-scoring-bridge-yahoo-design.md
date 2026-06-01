# Design: Harness scoring bridge + Yahoo price source

**Date:** 2026-05-31
**Status:** Implemented
**Source roadmap item:** `docs/DATA_SOURCES.md` §A3 (Yahoo price history) + the
"scoring only sees `StockMetrics`, never `TickerSnapshot`" architectural gap.

## Problem

Two coupled gaps surfaced while evaluating `docs/DATA_SOURCES.md`:

1. **The richer harness stack is never scored.** `scoring.score()` consumes the
   flat `StockMetrics` produced by the synchronous screener `Provider`s
   (`screen.run()` → `providers/` → `merge.py` → `scoring`). The async harness
   (`data/sources.py` → `merge_snapshots` → `TickerSnapshot`) produces a richer,
   audited snapshot that **nothing scores**. Enriching the harness is invisible to
   the model until a bridge exists.

2. **Momentum dies exactly when FMP gates a symbol.** The screener FMP provider
   already fills `price_vs_200dma` (`providers/fmp.py:52-54`) and `rel_strength_6m`
   vs SPY (`providers/fmp.py:111-114, 140-144`). But all of it rides on FMP's
   `quote`/`stock-price-change` endpoints, which return empty on the per-symbol 402
   "Special Endpoint" gating (e.g. GEV, AXON). On those symbols momentum loses both
   legs *and* the value axis collapses — `opportunity` (heaviest weight, 0.30)
   becomes the flimsiest input. The fields are also opaque vendor pre-computations
   we can't audit.

## Goal

Make the harness `TickerSnapshot` scoreable, and add a **keyless, gating-immune**
price source so momentum/risk are computed by us (auditable) and survive FMP
gating.

Non-goals (out of scope): `eps_revision` (needs Alpha Vantage / §B1), closing the
`pe_median_5y` parity gap, any new scoring gate, retiring the screener, EDGAR XBRL
(§A1), Tier-D composites.

## Architecture

Three cohesive pieces.

### 1. The bridge — `src/shortlist/data/bridge.py`

Pure `snapshot_to_metrics(snap: TickerSnapshot) -> StockMetrics`. Maps the six
snapshot sections onto the flat `StockMetrics` that `scoring.score()` consumes. No
I/O. Unlocks **every** harness source for scoring, not just Yahoo.

Most fields map 1:1. Two are **derived in the bridge** (the harness has the raw
material but not the field):

- `gross_margin_stability` ← `Statements.gross_margins()` via the shared
  `shortlist.stats.gross_margin_stability` helper (same formula the screener FMP
  provider uses — lifted into one place so the two paths cannot drift).
- `fcf_positive` ← `Statements.free_cash_flow[0] > 0` (most-recent year).

**Accepted parity gaps** (harness doesn't fetch these → `None`):

- `pe_median_5y` → `pe_vs_history()` is `None` → harness `value` runs on 3 legs
  instead of 4.
- `roic_5y_avg` → `moat_score()` already falls back to TTM `roic`.

These degrade gracefully (the scorer redistributes weight across present inputs)
and are documented, not hidden.

### 2. YahooSource — `src/shortlist/data/sources.py`

New async `Source` (httpx). Keyless
`query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d` plus one
SPY pull, browser `User-Agent`. Computes from adjusted-close OHLCV: `price`,
`ma200`, `ret_6m`, `rel_strength_6m` (stock 6m − SPY 6m), `realized_vol`
(annualized daily-return stdev), `max_drawdown` (trailing ~1y). Populates the
`Price` section. Day-cached at `.cache/yahoo/{SYMBOL}-{YYYY-MM-DD}.json`; the SPY
series is fetched once per run. Errors routed through `redact_secrets()`. Listed
**ahead of FMP** in the harness merge priority so its auditable price fields win.

### 3. New fields + integration

Added to both `data/models.py:Price` and `models.py:StockMetrics`:
`realized_vol`, `max_drawdown` — populated but **not scored** (surfaced in
JSON/CSV/research). No new gate.

`shortlist --engine harness` (default `screener`) runs `collect()` →
`snapshot_to_metrics()` → `score()` with the same table/JSON/CSV/research output.
Default harness chain: `yahoo,fmp,finnhub,edgar` (config `harness_sources`).
Harness-only — no screener `YahooProvider` this round.

## Error handling

One bad source never kills a run; a failed Yahoo fetch just means momentum loses
the Yahoo legs and `coverage()` drops honestly. The bridge never raises on missing
data. Harness-engine cards carry no `coverage` diagnostic (that lives on the
screener path); snapshot `coverage()`/`missing()` remain available via
`shortlist-harness`.

## Testing (offline)

- `tests/test_stats.py` — shared stability helper.
- `tests/test_bridge.py` — field mapping, derived fields, parity gaps, empty
  snapshot.
- `tests/test_yahoo_source.py` — vol/drawdown/rel-strength/ma200 math, chart
  parsing, SPY reuse, non-fatal errors.
- `tests/test_screen_engine.py` — `run_harness` + `--engine harness` end to end on
  the mock source.

## Follow-ups (not this change)

1. Annual `ratios` fetch in `FMPSource` → fills `pe_median_5y` (closes the value
   parity gap) + `roic_5y_avg`.
2. Optional volatility/drawdown risk gate once thresholds are tuned.
3. Surface a coverage diagnostic on the harness engine path.
