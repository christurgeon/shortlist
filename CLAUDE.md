# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Guidance for working in this repo. See `README.md` (overview) and `HARNESS.md`
(data layer) for the user-facing docs.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat /
growth / opportunity (momentum **or** value) / insider / risk, rank a shortlist
for a human deep dive. Config-driven via `config.yaml` (thresholds, weights, gates).

## One fetching layer: the data harness

The async `httpx`-based **harness** (`shortlist.data.*`) is the sole production
data layer: `Source`s in `data/sources.py` (`yahoo`, `fmp`, `finnhub`, `edgar`,
`finra`, `wsb`, `mock`), merged by `data/models.py:merge_snapshots` into an audited
`TickerSnapshot`, then adapted by `bridge.py:snapshot_to_metrics` into the
`StockMetrics` that `scoring.py` consumes. Two CLIs front it: `shortlist` (rank a
shortlist) and `shortlist-harness` (emit raw snapshots). The keyless `yahoo` OHLCV
source — price/momentum/risk we compute ourselves — **leads the price merge**
(`harness_sources: [yahoo, fmp, finnhub, edgar, finra, wsb]`). **Note:** passing
`--provider` overrides `harness_sources`, so omit it on the default path or
yahoo/finra are dropped.

(The legacy synchronous screener engine — `Provider`s in `providers/fmp.py`/
`finnhub.py`/`edgar.py`, `merge.py`, the screener `run()`, and the `--engine` flag —
was **retired**. What survives in `providers/`: the shared leaves `_form4.py`
(Form 4 aggregation) and `_edgar_facts.py` (10-K/balance-sheet extraction), used by
the harness `EdgarSource` **and** the XBRL backtest — edit insider/financials
extraction there, not in two places; the `Provider` base + `MockProvider`, now only
a lightweight offline `StockMetrics` factory for the scoring tests; and the
`quiver`/`fred` stubs, awaiting a harness-side `Source`.)

## Stacks built on the data layer

Three further stacks **orchestrate** the harness + scorer — they add discovery,
validation, and history, not new scoring:

- **Backtest** (`shortlist.backtest.*`, CLI `shortlist-backtest`): validates the
  scorer against forward returns (rank IC + quantile spreads) by replaying the
  **real** scoring chain on price series truncated point-in-time. Signal-agnostic
  (`Observation(as_of, ticker, {signal: sub-score})`). Momentum is validated on live
  prices; **`--source xbrl`** now validates the fundamental axes (quality/moat/growth/
  value) point-in-time, keylessly, from SEC companyfacts, and can **fit fundamental
  weights** walk-forward (`--fit` — proposes only, never writes `config.yaml`). The
  snapshot-replay path stays **guarded** on accumulated history. See `HARNESS.md` →
  "Backtesting" / "XBRL source" and `docs/ASSESSMENT_GAPS.md` §2.1.
- **Accumulation** (`shortlist.data.accumulate`, CLI `shortlist-accumulate`):
  idempotent, point-in-time daily capture of `TickerSnapshot`s into `store.py` so the
  guarded backtest paths can activate (≥24 daily snapshots). **Scheduling ships OFF**
  — a disabled systemd sample lives in `deploy/`. See `HARNESS.md` → "Feeding the
  snapshot path".
- **Scout** (`shortlist.scout.*`, CLI `shortlist-scout`): autonomously discovers
  candidate tickers from free signal feeds, deep-screens them via
  `screen.run_harness`, runs the Claude research layer on the leaders, and ships a
  daily Telegram report. Discovery + delivery only; reuses the existing scorer. Full
  design in `docs/AUTONOMOUS_SCOUT.md`; report delivery semantics + the Telegram-client
  hardening plan in `docs/NOTIFICATIONS.md`.
  The `src/shortlist/scout/report/` package is a renderer-agnostic view-model → section
  registry → HTML/text renderers, plus a Pillow PNG "glance" chart. Adding a report section
  = one `Section` class + one `SECTIONS` registry entry. **Pillow is lazy-imported
  only in `report/png.py` — never import it from `viewmodel`/`sections`/`html`/`theme`
  (keeps the core screener + demo path Pillow-free).** `uv sync --extra scout` installs
  Pillow for chart rendering. Daily artifacts are written to `scout/<date>/`
  (dashboard.png, report.html, report.txt, manifest.json) and delivered to Telegram via
  sendPhoto (chart) + sendDocument (HTML deep-dive), with a chunked text fallback when
  Telegram is unconfigured or failing.
  An interactive **`shortlist-bot`** (`shortlist.scout.bot`, CLI `shortlist-bot`) long-polls
  Telegram `getUpdates` (no webhook, no inbound ports) so the operator drives screening on
  demand: `/screen <tickers>` (fast scores/gates → same PNG+HTML report pipeline) and
  `/deep <ticker>` (adds the Claude research brief). It allowlists `TELEGRAM_CHAT_ID`
  (ignores all other senders), runs command handlers on a single worker thread (the poll
  loop never blocks), and reuses `run_harness`/`build_report`/`deliver` unchanged.
  Coexists with the daily push on one token (polling + sendMessage don't conflict; only
  two concurrent `getUpdates` pollers 409 — run ONE instance). The autonomous daily push
  is feature-flagged OFF by default (`scout.daily_push.enabled`); the bot is the primary
  interactive driver. Soft caps and poll timeout live in `config.yaml: scout.bot`. The
  always-on systemd unit is `deploy/shortlist-bot.service` (Type=simple).

## Dev workflow (uv)

```bash
uv sync                      # core + dev deps (pytest); uv.lock pins everything
uv sync --extra edgar        # add the SEC EDGAR insider source
uv run pytest                # data-harness layer + scoring + provider tests
uv run pytest tests/test_scoring.py::test_norm_endpoints_midpoint_and_clamp  # single test
uv run shortlist --demo     # offline, no keys
```

`pip install -e .` still works as a fallback — `pyproject.toml` is standard.

## Screen data flow

`screen.run_harness()` drives a screen:
1. `data.collector.collect(tickers, sources, config)` → one `TickerSnapshot` per
   ticker (each `Source` fetches + normalizes; `merge_snapshots` merges by priority)
2. `bridge.snapshot_to_metrics(snapshot)` → flat `StockMetrics` (unavailable fields stay `None`)
3. `scoring.score(metrics, config)` → `ScoreCard` (seven 0–100 sub-scores + composite + gates)

A `coverage` diagnostic (`coverage.py`) annotates each `ScoreCard`: per-source
fetch status (`ok`/`gated_402`/`rate_limited_429`/`empty`/`error`, derived from the
snapshot's provenance/errors by `data/coverage_adapt.py`), the null output fields,
and an interpretive note. It surfaces in `--json` (a `coverage` block, emitted only
when a source had trouble) and as a stderr `Coverage notes` summary — so a null
`value` reads as "FMP gated this symbol," not an unexplained gap.

**Value and momentum are weighted independently** (value-tilt: default value 0.22 /
momentum 0.08 — value pulls ~3× momentum). `ScoreCard.opportunity = max(momentum,
value)` is retained for display only and does **not** feed the composite. Composite
is a weighted blend (default quality 0.18 / moat 0.18 / growth 0.135 / value 0.22 /
momentum 0.08 / insider 0.135 / risk 0.10). **Gates** are hard filters (negative
FCF, sub-threshold market cap, over-leverage, heavy insider selling) that flag a
name regardless of score.

The **`over_leveraged`** and **`negative_fcf`** gates are config-gated (`gates.leverage` /
`gates.fcf`, shipped **ON**; remove a block for the byte-identical pre-feature gate — pinned by
`tests/test_gate_backcompat.py`). `over_leveraged` trips on **net-debt/EBITDA** when EBITDA is
usable (present, >0, margin ≥ `min_ebitda_margin`), else an **artifact-guarded, coverage-
corroborated D/E** fallback: abstain on the equity-distortion artifact (D/E ≤ 0 or >
`dte_artifact_ceiling`), trip plausible leverage (D/E in (max, ceiling]) only when interest
coverage is weak/absent — so buyback compounders (thin/negative equity) are spared while
distressed levered names are caught. `negative_fcf` is **stage-aware** (excused when `revenue_cagr`
**and** `revenue_growth_persistence` clear their thresholds); a soft **`cash_burn`** flag fires on
any negative FCF regardless. The `StockMetrics` fields `revenue`/`ebitda`/`cash_and_equivalents`/
`net_debt_to_ebitda` (signed; display-floored to net-cash in JSON/CSV) are populated from FMP where
present (`FMPSource`) and otherwise from a **EDGAR balance-sheet + cash-flow extraction** in
`providers/_edgar_facts.py` (debt = LT+current+short, cash = `CashAndMarketableSecurities`, **D&A
from the cash-flow statement** `DepreciationExpense`, balance columns are **instant dates** not
`(FY)`; these are edgartools' normalized `standard_concept` buckets, NOT raw us-gaap — validated
live in `tests/test_edgar_leverage_live.py`). The bridge derives `ebitda` (operating income + D&A)
and `net_debt_to_ebitda` from those. All thresholds are **unfitted priors** — the `--source xbrl`
backtest emits a standalone `net_debt_to_ebitda` axis. Gate names are unchanged, so sector masking
and `research.screening_call.gate_clamp` are untouched. **Note:** `fcf_positive` is the latest-year
real FCF on the harness; the XBRL backtest panel leaves it unset.

The **risk** sub-score (7th axis: realized volatility + max drawdown, both
inverted so safer scores higher) is a **composite-only tilt** — sector-neutral
(never masked) but deliberately **excluded from `confidence`/`scored`** (`scoring.py`
keeps it out of `components`). (The risk axis once relied on a ×0.9 rescale of the
other weights to stay composite-invariant when absent; the value/momentum split
**retired that invariant** — the composite is a normalized weighted average, so the
absolute weight sum is cosmetic and only ratios matter.) The risk weight is an
**unfitted prior** — trailing vol/drawdown peak at the bottom and can be
anti-predictive at turning points; backtest its standalone rank IC before trusting
it (`docs/ASSESSMENT_GAPS.md`).

The value-tilt also **lowers the `scored` floor** (`validity.min_scored_weight`
0.34→0.25, `ranking.thin_below` 0.5→0.40): splitting the never-gated `opportunity`
into a frequently-FMP-gated `value` + a small `momentum` shrinks a gated name's
always-present confidence, and without the lower floor value-tilted names —
especially financials, where `moat` is masked — would fall below `scored` and drop
out of the ranking.

Soft **`flags`** (`ScoreCard.flags`) are *advisory* — they never affect
`passed`/`composite` (distinct from hard `gates`). Flags include
`crowded_short = short_pct_outstanding ≥ t ∧ days_to_cover ≥ t ∧ rising ∧ fresh`
(harness engine + `finra`; thresholds in `config.yaml` → `flags.crowded_short`), the
conviction advisories `insider_cluster_buy` / `planned_sale` (inert unless `insider.conviction`
is enabled), and `value_trap` — fires when a name looks cheap (high `value` sub-score)
while quality OR growth is weak (`config.yaml` → `flags.value_trap`; a level-based
prior, never affects `passed`/`composite`/`scored`). An optional `flags.value_trap.piotroski`
sub-block (ships **OFF**, bit-identical when absent) refines it with a Piotroski-inspired
fundamental-quality fraction (`scoring.py:piotroski_score`, won/legs → 0–100; ScoreCard
`piotroski_f`/`piotroski_f_legs`): **suppresses** the flag on cheap-but-improving names,
**confirms** it on cheap-but-deteriorating ones. Sector-masked, an **unfitted prior** — and
the same fundamental-quality axis the `--source xbrl` backtest validates. The harness also
emits **presence-based filing-stream advisories** (`recent_8k` / `activist_13d` /
`passive_13g` / `planned_insider_sale_144`) into `flags` — set by the EDGAR bridge,
no config thresholds (`scoring.py:285`; see `docs/DATA_SOURCES.md`
§A1). The `dilution` flag fires on persistent net share issuance
(`share_count_cagr ≥ flags.dilution.min_share_cagr`; ON by default, advisory only).
`risk_off_regime` fires on leveraged (net-debt/EBITDA or D/E above thresholds, with the
same `dte_artifact_ceiling` guard the `over_leveraged` gate uses) or cyclical-bucket names
when the run-level FRED macro regime is risk-off (`config.yaml` → `flags.risk_off_regime`);
advisory only — never affects `passed`/`composite`/`scored`; **not** XBRL-backtest-validatable.
A run-level `MacroContext` is built once per run by `data/macro.py:fetch_macro` (keyless FRED
CSV, day-cached under `.cache/fred/`, never-raises); display + advisory only, threaded into
`score(..., macro=)` and the report (`_MacroHeader` + `risk_off_regime` flag); `--demo` skips it.

The **`quality.dilution`** block (`config.yaml`) is the **scoring** half of the
share-count/dilution feature (ASSESSMENT_GAPS §2.5). It ships **commented out** (OFF): when
enabled, `quality_score` gains an inverted `share_count_cagr` leg (diluters score below
buyback compounders) and the growth `eps_cagr` leg switches from the net-income proxy to
genuine per-share diluted-EPS CAGR (`eps_cagr_ps`). The scorer is **byte-identical** to the
pre-feature scorer when the block is absent (None-safe leg redistribution). `share_count_cagr`
(diluted weighted-avg share count; + = issuance, − = buybacks) and `eps_cagr_ps` are derived
from already-fetched data on **both** the harness (`Statements.diluted_shares` via
`_edgar_facts._row_diluted_shares`) and the XBRL backtest
(`WeightedAverageNumberOfDilutedSharesOutstanding`); `share_count_cagr` is surfaced in
JSON/CSV. The band/threshold/leg are **unfitted priors** — `backtest/signals.py`
`XbrlSignalSource` emits a standalone `share_count` axis (`--source xbrl`) so the rank IC is
measurable (`scoring.py:share_count_score` is backtest-only, not a production sub-score). Not
masked for financials/REITs (share count is universally defined); reads **as-reported** counts
with no split-flag guard yet (a reverse split can inject a spurious jump). See ASSESSMENT_GAPS §2.5.

The **`insider.conviction`** block (`config.yaml`) enriches `insider_score` with three
Form-4-derived signals — cluster buys, role-weighted buy pressure, and 10b5-1 planned-sell
forgiveness. It ships **commented out** (OFF by default); the scorer is **bit-identical** to
the pre-feature scorer when absent. Conviction is a **one-directional buy-side tilt**: it can
only *raise* `insider` (`max(base, avg(base, conviction))`), never penalize a name that simply
has no buys — and the `max` guard also avoids double-counting buying already in the net-flow leg.
The `heavy_insider_selling` gate is deliberately untouched
(10b5-1 detection forgives the score only, never the gate). All conviction weights are
**unfitted priors** — backtest before trusting. `EdgarSource` accepts the conviction config and
passes it through to `providers/_form4.py`, which extracts role strings and 10b5-1 footnote
heuristics from already-fetched edgartools objects.

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
  — so it's per-symbol gating, *not* a quota/key problem. When a symbol gates,
  2 of 4 value legs are recovered from free sources: `fcf_yield` from EDGAR 10-K FCF
  ÷ market cap (Finnhub/Yahoo backfill), and `pe_vs_history` from EDGAR annual EPS +
  Yahoo monthly closes. PEG and analyst-target upside (`upside_to_target`) still
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

## Short interest (harness)

`FinraSource` (keyless) pulls the **`ConsolidatedShortInterest`** dataset — NOT
`EquityShortInterest`, which is **frozen (last cycle 2022-09-15) and OTC-only**.
The symbol field is **`symbolCode`**. `settlementDate` is a **partition key** —
discover the latest cycle via the `/partitions/` endpoint; you cannot sort it in
the data query. The `record-max-limit` is **5000**, so paginate. `days_to_cover`
is FINRA-supplied; its `999.99` zero-volume sentinel is dropped to `None` in the
bridge. The source does one bulk fetch per run, caches by settlement date, and
indexes in memory — **no per-ticker request load**.

## WSB social hype (harness + scout)

`WsbSource` (keyless) and the scout `WsbHypeSignal` both read **ApeWisdom**
(`https://apewisdom.io/api/v1.0/filter/wallstreetbets`) via the shared
`data/apewisdom.py` leaf — one bulk GET/run, disk-cached by fetch date under
`.cache/apewisdom` (shared between both consumers). It populates the `social`
aux snapshot section (excluded from coverage, like `short_interest`) → bridge
`social_*` metrics → the soft **`social_hype`** flag (advisory; never affects
`passed`/`composite`/`scored`). SwaggyStocks itself has no keyless API — ApeWisdom
is the free, documented substitute (mention volume + 24h delta, not finance-tuned
bull/bear). Tune via `config.yaml: flags.social_hype` (scoring) and
`scout.wsb_hype` (discovery thresholds + index-ETF deny-list).

## Yahoo screener WAF gotcha (scout discovery)

The scout's `YahooScreenerSignal` (`scout/signals.py`) hits the **unofficial**
`query1.finance.yahoo.com/v1/finance/screener/predefined/saved` endpoint. A `429` there
is almost always a **cold-start fingerprint block from Yahoo's edge WAF, not throttling**:
a bot-shaped (UA-only) request gets an **HTML** `429 "Too Many Requests"`
(`content-type: text/html`), while a request with a **full browser header set**
(`_YAHOO_HEADERS` — `Accept`/`Accept-Language`/`Accept-Encoding`/`Sec-Fetch-*`/`Origin`/
`Referer`) returns `200 JSON` (no crumb needed). This is why it "never worked" on a fresh
machine — the header shape is identical everywhere, so the rejection was deterministic.

Headers are the **primary** lever but **not proven sufficient on a truly cold IP** (there's
a secondary per-IP reputation effect: once one well-formed request succeeds the IP is
trusted for a window). So the **per-run bail-out and the cross-run cooldown are
load-bearing and must not be removed**: on an HTML 429 the signal bails after a *single*
request (it does **not** retry an HTML 429, and does **not** fire the remaining screens),
and `daily.py` persists a **rest-of-day cooldown** in `ScoutState`
(`mark_yahoo_blocked`/`yahoo_blocked_on`) so the next runs make **zero** Yahoo requests.
Only a JSON 429 *with* a `Retry-After` is retried (once, capped). **Never retry-spam an
HTML 429** — that's how you earn a real ban. `Accept-Encoding` must stay a subset of what
httpx can decode (no `br`/`zstd` without the dep, or `.json()` fails). `query2` is a manual
escape hatch only — no auto-failover (a fingerprint block re-triggers from any host).

## Scale / rate limits (the honest catch)

Free tiers are fine for individual names or a small watchlist, but don't scale to
a full universe. The harness makes **~13 FMP calls per ticker**; FMP's **250/day**
free limit is therefore roughly **19 tickers/day** — a theoretical ceiling.
(The scout caps deep-screening lower still — **10/day** by default (`scout.daily_x`) —
and `shortlist-accumulate` at **15/day** (`--max-tickers`), for headroom.) Screening the whole
S&P 500 daily needs either FMP's paid **Starter tier (~$14–20/mo**, lifts per-minute
and bandwidth limits) or the **caching layer** — whichever you hit first.
**Finnhub's 60/min is comfortable** either way.

When the limit *is* hit, FMP returns **`429`** and the harness degrades
honestly rather than failing hard: `FMPSource._get` retries with `Retry-After`-aware
backoff (`fmp.max_retries`), the collector keeps whatever sections already succeeded,
and coverage reports a distinct `rate_limited_429` status (vs. `402` gating). But retry
can't manufacture quota — the real fix for **repeated** runs is caching, which **now
exists** (see "Caching" below).

## Caching (`cache.py`)

A persistent SQLite HTTP-response cache (`src/shortlist/cache.py`) wraps the harness
`FMPSource` and `FinnhubSource` `_get` boundaries, so a warm re-run of the same basket
within TTL makes **zero** upstream calls. **On by default** (`.cache/http.sqlite`,
gitignored); `--no-cache` disables it for a run and `--refresh-cache` bypasses reads
and repopulates. `--demo` runs with the cache off (offline). TTLs are per data
half-life and config-driven (`config.yaml: cache.ttl.<bucket>`); the endpoint→bucket
map is keyed on `(provider, path)` in `cache.py`. A configured process-global
singleton (`configure_default_cache`/`get_default_cache`) means every entrypoint —
the harness CLI, accumulate, scout — gets caching without build-path plumbing.

Two things to keep right when editing: (1) **never cache soft failures** — FMP/Finnhub
return 200-OK with empty `[]`/`{}` or `{"error": …}` on gating/no-coverage, so the
`_is_cacheable` predicate (not `raise_for_status` alone) gates writes; (2) the **`v1:`
key prefix** in `cache_key` must be bumped to `v2:` whenever a `_get`/normalizer output
shape changes, or stale-shape payloads are served until TTL. Design + rationale:
`docs/DATA_SOURCES.md` §6. Yahoo/FINRA keep their own
disk caches; EDGAR (free, uncapped) is intentionally uncached.

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
- Equity-centric moat/quality proxies are undefined for banks/insurers/REITs (e.g.
  SCHW). These legs are now **masked and abstained** per sector — see "Sector-aware
  applicability & abstention" below. (Sector-specific *recalibration* of the
  surviving legs is still future work; v1 only masks the undefined ones.)

## Sector-aware applicability & abstention

The scorer used to silently average metrics that are structurally undefined for a
company's sector (gross margin / FCF-yield / leverage for a bank), producing a
misleading composite. It now detects the sector and **abstains** the inapplicable
legs explicitly instead of dropping-then-averaging them.

- **Detection is SIC-based and EDGAR-only.** The harness `EdgarSource` emits a
  partial `Profile(sic=…)` (one extra lightweight SEC request, semaphore-bounded) and
  the bridge copies it to `m.sic`. `sectors.py:resolve_bucket` maps SIC → bucket via
  `config.yaml: sectors.buckets` (an **ordered** list; first matching range wins).
  Scoring **never** reads the free-text `StockMetrics.sector` (source-dependent) —
  only `m.sic`. If EDGAR isn't in the chain / no `SEC_IDENTITY`, the ticker resolves
  to `unknown`.
- **`unknown` bucket is a bit-identical no-op** — no masking, any present leg
  scores, composite always `scored`. The abstention floors are **bucket-gated** and
  only ever touch the masked sectors. This is the back-compat guarantee for
  operating companies (and is covered by an explicit regression test).
- **v1 masks** (config `sectors.masked_legs` / `masked_gates`) for
  financials/insurers/REITs: `gross_margin`, `gross_margin_stability`, `roic`,
  `fcf_yield`, `fcf_cagr`, `interest_coverage`, `debt_to_equity`, plus the
  `negative_fcf` / `over_leveraged` gates. `net_margin` is intentionally **not**
  masked (defined, only miscalibrated → deferred). Exchanges (6231), asset
  managers/advisers (6282), funds, SPACs and real-estate operators are deliberately
  left `unknown`/unmasked.
- **`ScoreCard` gains** `sic_bucket`, `confidence` (present-applicable component
  weight ÷ applicable weight), `scored` (above the validity floor; always `True`
  for `unknown`), and `abstentions` (`{field, reason: inapplicable|missing, scope}`).
  These surface in `--json` (and CSV `scored`/`sic_bucket` columns). **`passed` is
  now `not gates and scored`** — a not-scored name can't pass, rank to the top
  (sort key is `(scored, composite)`), or be selected for research.
- **Coverage vs abstentions don't contradict:** a sub-score that is `None` because
  it was masked-inapplicable is excluded from `coverage.unavailable` (it isn't a
  data gap). Per-leg *missing* is left to coverage; `abstentions` records masking +
  whole-sub-score abstention.
- Tune everything in `config.yaml: sectors` + `validity` — no hardcoded sector
  logic. `sectors.py` is the only interpreter of those blocks.

## Extension scaffolds (not wired)

`providers/extensions.py` contains `QuiverProvider` and `FredProvider` stubs sketching
the signals to implement. Quiver (congressional trades, gov-contract awards) and FRED
(10y yield, 2s10s curve) are the highest-leverage next additions, in that order. **They
predate the harness and are written against the retired screener `Provider` interface** —
to actually wire them, reimplement each as an async `Source` in `data/sources.py` and
register it in that module's `_REGISTRY` (the `--provider`/`harness_sources` chain resolves
against the harness Source registry now, **not** `providers/__init__.py:build_providers`).

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

The brief now bundles three EDGAR documents (`filings.py:fetch_bundle` →
`FilingBundle`): the latest **10-K** (primary, displayed), the latest **10-Q's
MD&A** (Part I Item 2 — via `get_item_with_part`, **NOT** the TenK
`management_discussion` attribute), and a **YoY Item-1A risk-factor diff**
(`riskdiff.py`, stdlib `difflib` on normalized block prefixes) surfaced as a
distinct `added_risks` brief section. The brief is cached on a **composite key**
(`<10-K-acc>+<10-Q-acc>`) so a new quarter invalidates; the prior-year 10-K is a
diff baseline only and **never enters the prompt or the grounding haystack**
(`FilingBundle.haystack()` excludes it, so a quote present only there reads as
unverified). `added_risks` is parsed leniently (malformed items skipped, never
sinks a brief). Tune via `config.yaml: research.risk_diff` / `max_added_risks` /
`max_chars.tenq_mda`. DEF 14A proxy + earnings-call transcripts remain deferred
(no keyless source); `FilingBundle` leaves room to add the proxy later.

The brief ends with a **screening call** (`research/models.py:ScreeningCall`,
config `research.screening_call`, ships **ON**) — a buy/hold/avoid stance +
conviction + one-sentence rationale, authored by Claude but bounded by three
deterministic guards in `assess.py:apply_guards`: a **gate clamp** (a tripped hard
gate can only move the stance more bearish, never more bullish), a **conviction
cap** (low `ScoreCard.confidence` or a real data gap forces ≤ MEDIUM), and a
**HIGH-conviction corroboration** requirement (high confidence + a verified
reconciliation + no contra-flag). The "decided without" / "not applicable" lines
are **Python-owned** (`research/coverage_caveat.py`, never the LLM) from the card's
coverage/abstention machinery. `enabled: false` is a byte-identical no-op **for
freshly generated briefs** — briefs are cached by filing accession (config-agnostic),
so an already-cached brief still renders its stored call; clear `research/` or use
`--refresh-cache` for a clean disabled run. It's an
LLM synthesis, **not** a backtested signal — the per-brief JSON record persists the
call + an `as_of_price` snapshot so a retrospective hit-rate is possible later.
Framed as **screening triage, not investment advice**; every standalone surface
(badge, scout pill, bot line) carries that tag. The JSON model key is `call` (the
Python field is `screening_call`); the user-facing label is "Screening call".
