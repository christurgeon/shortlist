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

## EDGAR source rate limit

`EdgarSource` runs the synchronous `edgartools` work in a worker thread
(`asyncio.to_thread`) and funnels all EDGAR fetches through a shared semaphore
(`_EDGAR_MAX_CONCURRENCY`, default 3). SEC enforces ~10 req/s fair-access per IP
and each ticker pulls many Form 4 filings, so the collector's per-ticker
semaphore is *not* enough — the EDGAR gate is what keeps a universe run under the
limit. Requires the `[edgar]` extra and `SEC_IDENTITY`; absent either, the source
is skipped (not fatal).

## Scoring a snapshot — the bridge

`data/bridge.py:snapshot_to_metrics()` converts a `TickerSnapshot` into the flat
`StockMetrics` the screener's scorer consumes, so the harness can feed the **same**
`scoring.score()`:

```bash
uv run shortlist --tickers GEV,AXON --engine harness
```

The bridge **derives** several fields the snapshot doesn't store directly, all
from the 5y `Statements` via the shared `shortlist.stats` helpers the screener
also uses: `gross_margin_stability`, `fcf_positive` (most-recent free cash flow),
and the **growth** legs `revenue_cagr` / `fcf_cagr` / `eps_cagr` (net-income proxy)
/ `revenue_growth_persistence`. It surfaces Yahoo's `realized_vol` and
`max_drawdown` as risk fields that are **populated but not yet scored**.

`FMPSource` fetches annual `ratios` and `key-metrics` history, so the bridge now
maps `pe_median_5y` (harness `value` runs on the full 4 legs, via the shared
`shortlist.stats.median_pe` helper the screener also uses) and `roic_5y_avg` (moat
uses the 5y ROIC average instead of falling back to TTM `roic`, via
`shortlist.stats.avg_roic`). The one remaining **accepted parity gap** vs. the
screener is `eps_revision` (Alpha Vantage, out of scope) — it maps to `None` and
the scorer redistributes weight. Harness-engine cards carry no `coverage`
diagnostic; the snapshot's own `coverage()`/`missing()` remain available via
`shortlist-harness`.

## Adding a source

Subclass `Source`, implement `async def fetch(ticker) -> SourceResult` returning
verbatim `raw` plus a normalized `partial` `TickerSnapshot`, and register it in
`_REGISTRY` in `sources.py`. (Yahoo, FMP, Finnhub, EDGAR, and Mock are all wired.)

## Known limitations (next hardening pass)

- Dataclasses, not pydantic — bad payloads normalize to `None` rather than failing loud.
- No caching/backoff yet **except Yahoo** (day-cached on disk); a full universe run
  on the keyed sources will still hit rate limits.
- **FMP's free plan gates many symbols** (e.g. GEV) behind premium with a `402`
  "Special Endpoint" on a per-symbol basis — coverage correctly drops to "thin"
  for those names. Major large-caps (AAPL/MSFT/LMT) work on the free tier.
- Equity-centric fields are blank for banks (SCHW) — coverage correctly flags it; sector-aware extraction is the fix.
- Mock data is illustrative, not verified.
