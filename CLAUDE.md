# CLAUDE.md

Guidance for working in this repo. See `README.md` (screener) and `HARNESS.md`
(data layer) for the user-facing docs.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat /
opportunity (momentum **or** value) / insider, rank a shortlist for a human deep
dive. Config-driven via `config.yaml` (thresholds, weights, gates).

## Two layers, two separate registries

There are **two parallel stacks** that don't share fetching code:

- **Screener** (`shortlist.*`): synchronous `requests`-based `Provider`s in
  `providers/`, merged by `merge.py`, scored by `scoring.py`. CLI: `shortlist`.
- **Data harness** (`shortlist.data.*`): async `httpx`-based `Source`s in
  `data/sources.py`, merged by `data/models.py:merge_snapshots`. Richer, audited
  `TickerSnapshot` output. CLI: `shortlist-harness`.

Each has its **own provider/source registry**. `fmp`, `finnhub`, and `edgar` are
wired in **both** (`mock` too in the harness). The shared Form 4 aggregation lives
in `providers/_form4.py` — a dependency-free leaf module used by both the screener
`EdgarProvider` and the harness `EdgarSource`; edit insider extraction logic there,
not in two places.

## Dev workflow (uv)

```bash
uv sync                      # core + dev deps (pytest); uv.lock pins everything
uv sync --extra edgar        # add the SEC EDGAR insider source
uv run pytest                # 5 tests (data-harness layer only; scorer untested)
uv run shortlist --demo     # offline, no keys
```

`pip install -e .` still works as a fallback — `pyproject.toml` is standard.

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
- **FMP's free plan also gates many symbols** (e.g. GEV) per-symbol with a `402`
  "Special Endpoint" — fundamentals/statements come back empty and coverage drops
  to "thin." Not a bug; major large-caps (AAPL/MSFT/LMT) work on the free tier.

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

## Scale / rate limits (the honest catch)

Free tiers are fine for individual names or a small watchlist, but don't scale to
a full universe. The harness makes **~11 FMP calls per ticker**, so FMP's
**250/day** free limit is roughly **20 tickers/day**. Screening the whole S&P 500
daily needs either FMP's paid **Starter tier (~$14–20/mo**, lifts per-minute and
bandwidth limits) or the **caching layer** from the hardening list — whichever you
hit first. **Finnhub's 60/min is comfortable** either way.

## Data scale conventions

- Margins/returns are stored as **fractions** (0.42 == 42%). **FMP `/stable/`
  already returns fractions** (use as-is); **Finnhub returns percentages** and is
  divided by 100 (`_pct`). Don't double-convert.
- Equity-centric moat/quality proxies are blank for banks/insurers (e.g. SCHW);
  coverage correctly flags this. Sector-aware thresholds are the real fix.

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
