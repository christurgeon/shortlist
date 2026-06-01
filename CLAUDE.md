# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Guidance for working in this repo. See `README.md` (screener) and `HARNESS.md`
(data layer) for the user-facing docs.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat /
growth / opportunity (momentum **or** value) / insider, rank a shortlist for a
human deep dive. Config-driven via `config.yaml` (thresholds, weights, gates).

## Two layers, two separate registries

There are **two parallel stacks** that don't share fetching code:

- **Screener** (`shortlist.*`): synchronous `requests`-based `Provider`s in
  `providers/`, merged by `merge.py`, scored by `scoring.py`. CLI: `shortlist`.
- **Data harness** (`shortlist.data.*`): async `httpx`-based `Source`s in
  `data/sources.py`, merged by `data/models.py:merge_snapshots`. Richer, audited
  `TickerSnapshot` output. CLI: `shortlist-harness`.

Each has its **own provider/source registry**. `fmp`, `finnhub`, and `edgar` are
wired in **both** (`mock` too in the harness; the keyless `yahoo` OHLCV source —
price/momentum/risk we compute ourselves — is **harness-only**, and leads the
harness price merge: `harness_sources: [yahoo, fmp, finnhub, edgar]`). The
screener can score off the harness via `--engine harness` (`bridge.py:snapshot_to_metrics`
adapts a `TickerSnapshot` into the `StockMetrics` the scorer consumes). The shared Form 4 aggregation lives
in `providers/_form4.py` — a dependency-free leaf module used by both the screener
`EdgarProvider` and the harness `EdgarSource`; edit insider extraction logic there,
not in two places. The shared EDGAR financials leaf (`providers/_edgar_facts.py`,
10-K statements) follows the same pattern.

## Stacks built on the two layers

Three further stacks **orchestrate** the two scoring layers — they add discovery,
validation, and history, not new scoring:

- **Backtest** (`shortlist.backtest.*`, CLI `shortlist-backtest`): validates the
  scorer against forward returns (rank IC + quantile spreads) by replaying the
  **real** scoring chain on price series truncated point-in-time. Signal-agnostic
  (`Observation(as_of, ticker, {signal: sub-score})`). Momentum is validated today;
  fundamental/weight-fitting paths are built but **guarded** on snapshot history.
  See `HARNESS.md` → "Backtesting" and `docs/ASSESSMENT_GAPS.md` §2.1.
- **Accumulation** (`shortlist.data.accumulate`, CLI `shortlist-accumulate`):
  idempotent, point-in-time daily capture of `TickerSnapshot`s into `store.py` so the
  guarded backtest paths can activate (≥24 daily snapshots). **Scheduling ships OFF**
  — a disabled systemd sample lives in `deploy/`. See `HARNESS.md` → "Feeding the
  snapshot path".
- **Scout** (`shortlist.scout.*`, CLI `shortlist-scout`): autonomously discovers
  candidate tickers from free signal feeds, deep-screens them via
  `screen.run_harness`, runs the Claude research layer on the leaders, and ships a
  daily Telegram report. Discovery + delivery only; reuses the existing scorer. Full
  design in `docs/AUTONOMOUS_SCOUT.md`.

## Dev workflow (uv)

```bash
uv sync                      # core + dev deps (pytest); uv.lock pins everything
uv sync --extra edgar        # add the SEC EDGAR insider source
uv run pytest                # data-harness layer + scoring + provider tests
uv run pytest tests/test_scoring.py::test_norm_endpoints_midpoint_and_clamp  # single test
uv run shortlist --demo     # offline, no keys
```

`pip install -e .` still works as a fallback — `pyproject.toml` is standard.

## Screener data flow

`screen.run()` drives the screener layer:
1. `Provider.fetch(ticker)` → `StockMetrics` (flat dataclass; unavailable fields stay `None`)
2. `merge.merge(per_provider_list)` → single `StockMetrics` filled by priority
3. `scoring.score(metrics, config)` → `ScoreCard` (six 0–100 sub-scores + composite + gates)

A `coverage` diagnostic (`coverage.py`) annotates each `ScoreCard`: per-provider
fetch status (`ok`/`gated_402`/`empty`/`error`, the latter derived from the fetch
exception and the `metrics.sources` audit trail), the null output fields, and an
interpretive note. It surfaces in `--json` (a `coverage` block, emitted only when a
provider had trouble) and as a stderr `Coverage notes` summary — so a null `value`
reads as "FMP gated this symbol," not an unexplained gap.

`opportunity = max(momentum, value)` so a name qualifies on **either** axis rather
than being averaged down. Composite is a weighted blend (default quality 0.20 /
moat 0.20 / growth 0.15 / opportunity 0.30 / insider 0.15). **Gates** are hard filters
(negative FCF, sub-threshold market cap, over-leverage, heavy insider selling)
that flag a name regardless of score.

When a sub-score has no inputs (all `None`), it is excluded and the composite
weight is redistributed across the remaining components — never silently zeroed.

Tune thresholds, weights, and gates in `config.yaml` — no code changes needed.

## Secrets

- Keys load from the environment or a root-level `.env` (gitignored; see
  `.env.example`). `env.py:load_env()` searches upward from cwd; an explicit
  `export` wins over `.env`. Run from inside the repo so `.env` is found.
- **Any error string that may contain a request URL MUST pass through
  `env.py:redact_secrets()`** before being printed or stored — provider HTTP
  errors embed `?apikey=`/`?token=` and will otherwise leak the key. All current
  print/store sites already do this; keep it that way when adding sources.

## FMP gotchas (learned the hard way)

- **Use the `/stable/` API only.** The legacy `/v3`–`/v4` endpoints were retired
  for new keys on **2025-08-31** (they return `403 Legacy Endpoint`). Every
  `/stable/` endpoint takes `?symbol=`. Don't reintroduce `v3/...` paths.
- Field locations moved vs. the old API: **PE/PEG are in `ratios-ttm`**
  (`priceToEarningsRatioTTM`, `priceToEarningsGrowthRatioTTM`); **ROE/ROIC are in
  `key-metrics-ttm`** (`returnOnEquityTTM`, `returnOnInvestedCapitalTTM`).
  Recommendations come from `grades-consensus` (`strongBuy/buy/hold/sell`).
- **FMP insider trading is a paid endpoint** (`402` on free plans). It's wrapped
  to skip quietly — EDGAR is the authoritative, free insider source instead.
- **FMP's free plan also gates many symbols** (e.g. GEV, AXON, MELI, ISRG, SCHW,
  TMO) per-symbol with a `402` "Special Endpoint" — fundamentals/statements come
  back empty and coverage drops to "thin." Not a bug; major large-caps
  (AAPL/MSFT/LMT) work on the free tier. **Diagnosing it:** the symbol 402s on the
  basic `/stable/quote` endpoint while other symbols on the same key return `200`
  — so it's per-symbol gating, *not* a quota/key problem. The visible fallout is a
  **`null` `value` sub-score** (and `null` `upside_to_target`). **On `--engine
  harness`**, 2 of 4 value legs are now recoverable from free sources: `fcf_yield`
  from EDGAR 10-K FCF ÷ market cap (Finnhub/Yahoo backfill), and `pe_vs_history`
  from EDGAR annual EPS + Yahoo monthly closes. PEG and analyst-target upside still
  require FMP and remain `null` when gated. For full value coverage, **FMP's paid
  Starter tier (~$14–20/mo)** lifts the gating. (`market_cap` is always backfilled
  by Finnhub, which is why the insider sub-score survives gating.)

## Insider merge (harness)

`insider` is neither flat nor pick-first merged — it has a **bespoke merger** in
`data/models.py` (`_merge_insider`). The coupled transaction facts
(`net_value_6m`/`buy_count`/`sell_count`/`recent`) are taken wholesale from one
source so they stay coherent; `sentiment_mspr` is filled independently. Don't move
`insider` into the `_FLAT` set — field-by-field merge there can glue one source's
dollar figure to another source's trade counts (silent incoherence).

## EDGAR in the harness

`EdgarSource` wraps synchronous `edgartools` in `asyncio.to_thread` and rate-limits
via a shared module-level semaphore (`_EDGAR_MAX_CONCURRENCY`, default 3) — SEC
fair-access is ~10 req/s and the collector's per-ticker semaphore doesn't bound
SEC request rate. `set_identity` is process-global; set once in `__init__`, never
per-ticker (thread race).

`EdgarSource` now supplies **two failure-isolated sections**: Form 4 insider trades
(via the shared `providers/_form4.py` leaf) and **10-K financial statements** —
revenue, net income, operating cash flow, FCF, and diluted EPS for the latest ~3
fiscal years (absolute USD). Symbols with no XBRL financials (Form 20-F foreign
issuers, recent spin-offs) degrade statements to `None` without touching insider
data. The `get_financials()` call roughly doubles per-ticker EDGAR SEC requests;
the concurrency semaphore still bounds SEC load, but full-universe runs still need
the caching layer.

## Scale / rate limits (the honest catch)

Free tiers are fine for individual names or a small watchlist, but don't scale to
a full universe. The harness makes **~13 FMP calls per ticker** (the screener ~8,
since the paid insider call is gated off by default); FMP's **250/day** free limit
is therefore roughly **19 tickers/day** on the harness path. Screening the whole
S&P 500 daily needs either FMP's paid **Starter tier (~$14–20/mo**, lifts per-minute
and bandwidth limits) or the **caching layer** — whichever you hit first.
**Finnhub's 60/min is comfortable** either way.

When the limit *is* hit, FMP returns **`429`** and the screener now degrades
honestly rather than failing hard: `FMPProvider._get` retries with `Retry-After`-aware
backoff (`fmp.max_retries`), `fetch()` keeps whatever legs already succeeded, and
coverage reports a distinct `rate_limited_429` status (vs. `402` gating). But retry
can't manufacture quota — the real fix for **repeated** runs is caching, which is
**specced as a future work stream in `docs/DATA_SOURCES.md` §6** (not yet built).
Start there for the daily-quota problem.

## Data scale conventions

- Margins/returns are stored as **fractions** (0.42 == 42%). **FMP `/stable/`
  already returns fractions** (use as-is); **Finnhub returns percentages** and is
  divided by 100 (`_pct`). Don't double-convert.
- **`market_cap` is stored in absolute dollars** (matching FMP's `quote.marketCap`).
  **Finnhub reports market cap in millions** and is multiplied by 1e6 (`_millions`).
  The `below_min_mktcap` gate and the insider net-flow ratio both assume dollars, so
  Finnhub is the free fallback denominator when FMP gates a symbol (`402` Special
  Endpoint) — without it, EDGAR's insider dollars can't be normalized and the
  insider sub-score goes `null`.
- Equity-centric moat/quality proxies are blank for banks/insurers (e.g. SCHW);
  coverage correctly flags this. Sector-aware thresholds are the real fix.

## Extension providers (scaffolded, not wired)

`providers/extensions.py` contains `QuiverProvider` and `FredProvider` stubs with
the interface and the specific signals to implement. Quiver (congressional trades,
gov-contract awards) and FRED (10y yield, 2s10s curve) are the highest-leverage
next additions, in that order. Both are registered in `providers/__init__.py:_REGISTRY`
and can be activated with `--provider quiver` or `--provider fred` once implemented.

## Skills

- **`/run`** — end-to-end screener skill. Gather tickers → check env → run
  `uv run shortlist --json` → interpret results (scores, gates, opportunity axis,
  null sub-scores, coverage gaps). Lives at `.claude/skills/run/SKILL.md`.

## Qualitative research layer (`shortlist/research/`)

Opt-in `--research N` enriches top-N non-gated names with a Claude-written 10-K
brief. It uses the **`claude` CLI in headless mode, not the Anthropic API SDK**
(no key; uses the user's CLI auth). The runner (`research/claude_cli.py`) MUST
keep the lockdown flags — `--tools "" --strict-mcp-config --max-turns 1`, prompt
on stdin, neutral cwd, and NO `--bare` (bare forces ANTHROPIC_API_KEY). The whole
package is lazy-imported so the core screener works without `claude`/edgartools.
Briefs are cached by filing accession (not date); facts are quote-verified
against the filing, interpretive prose is labeled. The research summary prints to
stderr (keeps `--json` stdout clean). Output under `research/` (gitignored).
