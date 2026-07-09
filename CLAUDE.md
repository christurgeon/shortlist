# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repo. See `README.md`
(overview) and `HARNESS.md` (data layer) for the user-facing docs.

## Session wrap-up — capture follow-ups

Before ending a session that produced **unfinished follow-up work, deferred
decisions, or known gaps**, append them to `TODO.md` so they are never forgotten.
Match its format: newest at top, a dated `## <title> (YYYY-MM-DD)` heading, a short
body, and a closing **Status:** line. Nothing to capture → add nothing.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat /
growth / opportunity (momentum **or** value) / insider / risk, rank a shortlist
for a human deep dive. Config-driven via `config.yaml` (thresholds, weights, gates).

## One fetching layer: the data harness

The async `httpx` **harness** (`shortlist.data.*`) is the sole production data layer:
`Source`s in `data/sources.py` (`yahoo`, `fmp`, `finnhub`, `edgar`, `finra`, `wsb`,
`mock`), merged by `data/models.py:merge_snapshots` into an audited `TickerSnapshot`,
adapted by `bridge.py:snapshot_to_metrics` into the `StockMetrics` that `scoring.py`
consumes. Two CLIs front it: `shortlist` (rank a shortlist) and `shortlist-harness`
(raw snapshots). The keyless `yahoo` OHLCV source — we compute price/momentum/risk
ourselves — **leads the price merge** (`harness_sources: [yahoo, fmp, finnhub, edgar,
finra, wsb]`). **`--provider` overrides `harness_sources`**, so omit it on the default
path or yahoo/finra are dropped.

The legacy synchronous screener (the `Provider`s, `merge.py`, screener `run()`,
`--engine`) was **retired**. Surviving in `providers/`: the shared leaves `_form4.py`
(Form 4 aggregation) and `_edgar_facts.py` (10-K/balance-sheet extraction) — used by
both `EdgarSource` and the XBRL backtest, so **edit extraction there, not in two
places**; the `Provider` base + `MockProvider` (now just an offline `StockMetrics`
factory for the scoring tests); and the `quiver`/`fred` stubs.

## Stacks built on the data layer

Three stacks **orchestrate** the harness + scorer (discovery, validation, history —
no new scoring):

- **Backtest** (`shortlist.backtest.*`, `shortlist-backtest`): validates the scorer
  against forward returns (rank IC + quantile spreads) by replaying the real scoring
  chain on point-in-time-truncated price series. Signal-agnostic (`Observation(as_of,
  ticker, {signal: sub-score})`). Momentum validates on live prices; **`--source
  xbrl`** validates the fundamental axes (quality/moat/growth/value) keylessly from SEC
  companyfacts and can **fit fundamental weights** walk-forward (`--fit` — proposes
  only, never writes `config.yaml`). It also emits **measurement-only** axes (rank IC
  checkable before any wiring): `share_count`, `net_debt_to_ebitda`, the EV/EBIT slice
  (`ebit_ev_yield` + per-leg value-attribution axes), `asset_growth`, `accruals`,
  `shareholder_yield`, plus a **multi-pair collinearity diagnostic**
  (`cli.py:_COLLINEARITY_PAIRS`, stderr + `--json` `collinearity`: corr ≳ 0.5 vs a
  scored axis = duplicate). `ebit_ev_yield` is derived on both paths and carried on
  `StockMetrics` but **no production sub-score reads it** (value leg deferred;
  `scoring.py:ebit_ev_yield_score` is backtest-only). The snapshot-replay path stays
  **guarded** on accumulated history. See `HARNESS.md` → "Backtesting" / "XBRL source",
  `docs/ASSESSMENT_GAPS.md` §2.1 / §2.2.
- **Accumulation** (`shortlist.data.accumulate`, `shortlist-accumulate`): idempotent
  point-in-time daily capture of `TickerSnapshot`s into `store.py` so the guarded
  backtest paths can activate (≥24 daily snapshots). **Scheduling ships OFF** (disabled
  systemd sample in `deploy/`). See `HARNESS.md` → "Feeding the snapshot path". Thin
  snapshots (FMP-quota-gated, keyless-only coverage) are persisted by default — the
  keyless SUE inputs ride them — and the store is gzipped.
- **Scout** (`shortlist.scout.*`, `shortlist-scout`): autonomously discovers tickers
  from free signal feeds, deep-screens via `screen.run_harness`, runs the Claude
  research layer on leaders, ships a daily Telegram report. Discovery + delivery only;
  reuses the scorer. Design in `docs/AUTONOMOUS_SCOUT.md`; delivery + Telegram-client
  hardening in `docs/NOTIFICATIONS.md`.
  - `scout/report/` is a renderer-agnostic view-model → section registry → HTML/text
    renderers + a Pillow PNG "glance" chart. New section = one `Section` class + one
    `SECTIONS` entry. **Pillow is lazy-imported only in `report/png.py`** — never import
    it from `viewmodel`/`sections`/`html`/`theme` (keeps the core + demo Pillow-free);
    `uv sync --extra scout` installs it. Daily artifacts → `scout/<date>/`, delivered via
    sendPhoto (chart) + sendDocument (HTML), with a chunked text fallback.
  - Interactive **`shortlist-bot`** (`shortlist.scout.bot`) long-polls Telegram
    `getUpdates` (no webhook, no inbound ports): `/screen <tickers>`, `/deep <ticker>`
    (adds the research brief), `/portfolio` (reads the gitignored `portfolio.csv` —
    `ticker,shares`; path/cap in `config.yaml: portfolio`; runs `run_harness` on
    holdings → report with an applies()-gated `_Portfolio` section: exposure, sector
    concentration by `sic_bucket`, per-holding deterioration alerts; **never silently
    truncates** — overflow past `portfolio.max_holdings` is warned, naming dropped
    tickers; pure leaf in `shortlist/portfolio.py`), `/explain <term>` (static
    financial glossary — `scout/glossary.py`, a pure semantics-only leaf, no config
    values quoted; scoring's declarative `KNOWN_GATES`/`KNOWN_FLAGS` + an AST-scan
    test bind emitted gate/flag literals to glossary + `theme.py` legend entries, so
    adding a flag fails CI until documented). Allowlists `TELEGRAM_CHAT_ID`,
    handlers on a single worker thread (poll loop never blocks), reuses
    `run_harness`/`build_report`/`deliver`. **Run ONE instance** (two concurrent
    `getUpdates` pollers 409). The daily push is **OFF by default**
    (`scout.daily_push.enabled`); the bot is the primary driver. Caps/timeout in
    `config.yaml: scout.bot`; systemd unit `deploy/shortlist-bot.service`.

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

`screen.run_harness()`:
1. `data.collector.collect(tickers, sources, config)` → one `TickerSnapshot` per ticker
   (each `Source` fetches + normalizes; `merge_snapshots` merges by priority)
2. `bridge.snapshot_to_metrics(snapshot)` → flat `StockMetrics` (unavailable fields stay `None`)
3. `scoring.score(metrics, config)` → `ScoreCard` (seven 0–100 sub-scores + composite + gates)

A `coverage` diagnostic (`coverage.py`) annotates each `ScoreCard`: per-source fetch
status (`ok`/`gated_402`/`rate_limited_429`/`empty`/`error`, from provenance/errors via
`data/coverage_adapt.py`), the null output fields, and a note. It surfaces in `--json`
(a `coverage` block, only when a source had trouble) and a stderr summary — so a null
`value` reads as "FMP gated this symbol," not an unexplained gap.

**Value and momentum are weighted independently** (default value 0.22 / momentum 0.08 —
value pulls ~3×). `ScoreCard.opportunity = max(momentum, value)` is **display-only** and
does **not** feed the composite. Composite is a weighted blend (default quality 0.18 /
moat 0.18 / growth 0.135 / value 0.22 / momentum 0.08 / insider 0.135 / risk 0.10).
**Gates** are hard filters (negative FCF, sub-threshold market cap, over-leverage, heavy
insider selling) that flag a name regardless of score.

The **`over_leveraged`** / **`negative_fcf`** gates are config-gated (`gates.leverage` /
`gates.fcf`, **ON**; remove a block for the byte-identical pre-feature gate, pinned by
`tests/test_gate_backcompat.py`). `over_leveraged` trips on **net-debt/EBITDA** when
EBITDA is usable (present, >0, margin ≥ `min_ebitda_margin`), else an **artifact-guarded
D/E fallback**: abstain on equity distortion (D/E ≤ 0 or > `dte_artifact_ceiling`), trip
plausible leverage (D/E in (max, ceiling]) only when interest coverage is weak/absent —
so buyback compounders (thin/negative equity) are spared while distressed levered names
are caught. `negative_fcf` is **stage-aware** (excused when `revenue_cagr` **and**
`revenue_growth_persistence` clear their thresholds); a soft **`cash_burn`** flag fires
on any negative FCF regardless.

`revenue`/`ebitda`/`cash_and_equivalents`/`net_debt_to_ebitda` (signed; display-floored
to net-cash in JSON/CSV) come from FMP where present, else an **EDGAR balance-sheet +
cash-flow extraction** in `providers/_edgar_facts.py` (debt = LT+current+short, cash =
`CashAndMarketableSecurities`, **D&A from the cash-flow statement** `DepreciationExpense`,
balance columns are **instant dates** not `(FY)`; these are edgartools' normalized
`standard_concept` buckets, NOT raw us-gaap — validated in
`tests/test_edgar_leverage_live.py`). The bridge derives `ebitda` (operating income +
D&A) and `net_debt_to_ebitda`. Thresholds are **unfitted priors** (`--source xbrl` emits
a `net_debt_to_ebitda` axis). Gate names are unchanged, so sector masking +
`research.screening_call.gate_clamp` are untouched.

`fcf_positive` (sign of the latest-year real FCF) is derived on **both** the harness
(`bridge.py`, from `Statements`) and the XBRL panel (`_xbrl_facts.panel_to_metrics`, sign
of `latest(p.fcf)` = OCF−capex, **abstaining to None when the latest FY lacks a capex
tag** so it never reports a stale older-year sign). No XBRL-backtest path evaluates the
`negative_fcf` gate yet (axes only), so the stage-aware FCF excuse remains a measurement
gap.

The **risk** sub-score (7th axis: realized vol + max drawdown, both inverted so safer
scores higher) is a **composite-only tilt** — sector-neutral (never masked) but
deliberately **excluded from `confidence`/`scored`** (kept out of `components`). The
composite is a normalized weighted average, so the absolute weight sum is cosmetic and
only ratios matter. The risk weight is an **unfitted prior** — trailing vol/drawdown peak
at bottoms and can be anti-predictive at turning points; backtest its standalone rank IC
before trusting it (`docs/ASSESSMENT_GAPS.md`).

The value-tilt also **lowers the `scored` floor** (`validity.min_scored_weight`
0.34→0.25, `ranking.thin_below` 0.5→0.40): splitting the never-gated `opportunity` into a
frequently-FMP-gated `value` + a small `momentum` shrinks a gated name's confidence, and
without the lower floor value-tilted names (esp. financials, where `moat` is masked) would
fall below `scored` and drop out of the ranking.

Soft **`flags`** (`ScoreCard.flags`) are *advisory* — they never affect
`passed`/`composite`/`scored` (unlike hard `gates`):
- `crowded_short` = short% ≥ t ∧ days-to-cover ≥ t ∧ rising ∧ fresh (harness + `finra`;
  `flags.crowded_short`).
- `value_trap` — cheap (high `value`) while quality OR growth is weak (`flags.value_trap`,
  a level-based prior). An optional `flags.value_trap.piotroski` sub-block (**OFF**,
  bit-identical when absent) refines it with a Piotroski fundamental-quality fraction
  (`scoring.py:piotroski_score`; ScoreCard `piotroski_f`/`piotroski_f_legs`): suppresses
  on cheap-but-improving, confirms on cheap-but-deteriorating. Sector-masked, unfitted
  prior — the same axis `--source xbrl` validates.
- Presence-based EDGAR filing-stream advisories: `recent_8k` / `activist_13d` /
  `passive_13g` / `planned_insider_sale_144` (no config thresholds; `docs/DATA_SOURCES.md`
  §A1).
- `dilution` — persistent net issuance (`share_count_cagr ≥ flags.dilution.min_share_cagr`;
  ON).
- `insider_cluster_buy` / `planned_sale` — inert unless `insider.conviction` is enabled.
- `risk_off_regime` — leveraged (net-debt/EBITDA or D/E above thresholds, same
  `dte_artifact_ceiling` guard as the gate) or cyclical-bucket names when the run-level
  FRED macro regime is risk-off (`flags.risk_off_regime`). **Not** XBRL-backtest-validatable.

The run-level `MacroContext` is built once per run by `data/macro.py:fetch_macro`
(official FRED API + free `FRED_API_KEY`, day-cached `.cache/fred/`, never-raises, `None`
when unkeyed — the keyless `fredgraph.csv` host is IP-blocked on datacenter IPs). Display
+ advisory only, threaded into `score(..., macro=)` and the report; `--demo` skips it.

The **`quality.dilution`** block (ASSESSMENT_GAPS §2.5) ships **OFF** (commented out):
enabled, `quality_score` gains an inverted `share_count_cagr` leg (diluters < buyback
compounders) and growth's `eps_cagr` leg switches from the net-income proxy to genuine
per-share diluted-EPS CAGR (`eps_cagr_ps`). **Byte-identical** when the block is absent
(None-safe leg redistribution). `share_count_cagr` (diluted weighted-avg; + = issuance,
− = buybacks) and `eps_cagr_ps` are derived on **both** paths (harness
`Statements.diluted_shares` via `_edgar_facts._row_diluted_shares`; XBRL
`WeightedAverageNumberOfDilutedSharesOutstanding`); `share_count_cagr` is in JSON/CSV.
Unfitted priors — `XbrlSignalSource` emits a `share_count` axis (`scoring.share_count_score`
backtest-only). Not masked (share count universally defined); reads **as-reported** counts,
no split guard yet (a reverse split can inject a spurious jump).

The **`quality.earnings_quality`** block (PREDICTIVE_SIGNALS §3 — Cooper-Gulen-Schill 2008
asset growth, Sloan 1996 accruals; both negative predictors) ships **ON with per-leg
control**: **`accruals` ENABLED** (backtest-validated: XS-IC +0.036 t=2.1 @3m on the
195-name broad universe, hit-rate 60–69%, orthogonal to Piotroski), **`asset_growth` OFF**
(`asset_growth: false` — no XS edge, −0.006 t=−0.3; still measured-but-off). Per-leg flags
default True when absent (back-compat). An enabled leg adds an **inverted** leg to
`quality_score`: `asset_growth` (`Assets_t/Assets_{t-1}−1`, consecutive fiscal ends) /
`accruals` (`(NetIncome−CFO)/avg-assets`, Sloan convention — avg `(A_t+A_{t-1})/2`, CFO
**as-reported, no sign flip**). **Byte-identical** when the block is absent. Derived on
both paths (harness `_edgar_facts.py` `Assets` via `standard_concept`; XBRL `_xbrl_facts.py`
raw us-gaap `Assets`/`NetIncomeLoss`/`NetCashProvidedByUsedInOperatingActivities`); both in
JSON/CSV. Shared math in `stats.py` (a consecutive ~1yr fiscal-end guard drops gap-spanning
ratios + stubs). Unfitted priors — `XbrlSignalSource` emits `asset_growth`/`accruals` axes
with `accruals~piotroski` + `asset_growth~growth` collinearity pairs. **Masked** for
financials/REITs on the production path; the backtest axis stays unmasked. See
PREDICTIVE_SIGNALS_RESEARCH §3.

The **`value.shareholder_yield`** block (PREDICTIVE_SIGNALS §5 — Boudoukh et al. 2007 /
Faber) ships **OFF**: enabled, `value_score` gains one **straight** (non-inverted, unlike
the §3 legs) leg `shareholder_yield = (dividends + net buybacks + net debt reduction) /
market_cap`. **Byte-identical** when absent. **Sign discipline:** dividends + repurchases
are added (`stats.shareholder_yield()` abs()-normalizes each, agreeing whether the source
reports positive magnitudes — raw companyfacts — or sign-flipped outflows — edgartools'
`to_dataframe()`); the net-debt leg is `repayments − issuance` **sign-preserved** (a net
debt *issuer* carries a negative leg, never clamped to 0). The four financing legs are
**net-new XBRL extraction** via concept **families** (raw us-gaap `concept` column —
`standard_concept` mislabels financing rows, e.g. buckets `PaymentsOfDividends` under
`DistributionsToMinorityInterests`; `_xbrl_facts.sum_family` sums distinct common+preferred
members per fiscal end), divided by `market_cap`. In `--json`. Unfitted prior —
`XbrlSignalSource` emits a `shareholder_yield` axis with `~value_fcf_yield` + `~share_count`
collinearity pairs. **Masked** for financials (capital returns ride CCAR/SCB; loan-book debt
distorts the net-debt leg). See PREDICTIVE_SIGNALS_RESEARCH §5.

The **`insider.conviction`** block enriches `insider_score` with three Form-4 signals —
cluster buys, role-weighted buy pressure, 10b5-1 planned-sell forgiveness. Ships **OFF**;
**bit-identical** when absent. A **one-directional buy-side tilt**: it can only *raise*
`insider` (`max(base, avg(base, conviction))`), never penalize a name with no buys — the
`max` also avoids double-counting buying already in the net-flow leg. The
`heavy_insider_selling` **gate is untouched** (10b5-1 forgives the score, never the gate).
Unfitted priors. `EdgarSource` passes the config to `providers/_form4.py`, which extracts
role strings + 10b5-1 footnote heuristics from already-fetched edgartools objects.

When a sub-score has no inputs (all `None`) it is excluded and its weight redistributed
across the remaining components — never silently zeroed. Tune thresholds, weights, and
gates in `config.yaml` — no code changes needed.

## Secrets

- Keys load from the environment or a root-level `.env` (gitignored; see `.env.example`).
  `env.py:load_env()` searches upward from cwd; an explicit `export` wins over `.env`. Run
  from inside the repo so `.env` is found.
- **Any error string that may contain a request URL MUST pass through
  `env.py:redact_secrets()`** before being printed or stored — provider HTTP errors embed
  `?apikey=`/`?token=` and otherwise leak the key. Keep it that way when adding sources.

## FMP gotchas (learned the hard way)

- **Use the `/stable/` API only.** The legacy `/v3`–`/v4` endpoints were retired for new
  keys on **2025-08-31** (`403 Legacy Endpoint`). Every `/stable/` endpoint takes
  `?symbol=`. Don't reintroduce `v3/...` paths.
- Field moves vs. the old API: **PE/PEG in `ratios-ttm`** (`priceToEarningsRatioTTM`,
  `priceToEarningsGrowthRatioTTM`); **ROE/ROIC in `key-metrics-ttm`**
  (`returnOnEquityTTM`, `returnOnInvestedCapitalTTM`); recommendations from
  `grades-consensus`.
- **FMP insider trading is paid** (`402` on free plans) — wrapped to skip quietly; EDGAR
  is the free authoritative source.
- **FMP's free plan also gates many symbols** per-symbol (`402` "Special Endpoint" — GEV,
  AXON, MELI, ISRG, SCHW, TMO…): fundamentals/statements come back empty, coverage drops
  to "thin." Major large-caps (AAPL/MSFT/LMT) work. **Diagnosing:** the symbol 402s on the
  basic `/stable/quote` endpoint while others on the same key return `200` — per-symbol
  gating, *not* a quota/key problem. When gated, 2 of 4 value legs recover from free
  sources: `fcf_yield` (EDGAR 10-K FCF ÷ market cap, Finnhub/Yahoo backfill) and
  `pe_vs_history` (EDGAR annual EPS + Yahoo monthly closes); PEG + `upside_to_target` still
  need FMP (`null` when gated). Paid **Starter (~$14–20/mo)** lifts gating. `market_cap` is
  always Finnhub-backfilled, which is why the insider sub-score survives gating.

## Insider merge (harness)

`insider` is neither flat nor pick-first merged — it has a **bespoke merger** in
`data/models.py` (`_merge_insider`). The coupled transaction facts
(`net_value_6m`/`buy_count`/`sell_count`/`recent`) are taken wholesale from one source so
they stay coherent; `sentiment_mspr` is filled independently. **Don't move `insider` into
the `_FLAT` set** — field-by-field merge there can glue one source's dollar figure to
another source's trade counts (silent incoherence).

## EDGAR in the harness

`EdgarSource` wraps synchronous `edgartools` in `asyncio.to_thread` and rate-limits via a
shared module-level semaphore (`_EDGAR_MAX_CONCURRENCY`, default 3 — SEC fair-access is
~10 req/s and the collector's per-ticker semaphore doesn't bound SEC request rate).
`set_identity` is process-global — set once in `__init__`, never per-ticker (thread race).

It supplies **two failure-isolated sections**: Form 4 insider trades (shared
`providers/_form4.py`) and **10-K financial statements** (revenue, net income, OCF, FCF,
diluted EPS; latest ~3 fiscal years; absolute USD). Symbols with no XBRL financials
(Form 20-F foreign issuers, recent spin-offs) degrade statements to `None` without
touching insider data. `get_financials()` roughly doubles per-ticker EDGAR requests — the
concurrency semaphore bounds SEC load, but full-universe runs still need caching.

## Short interest (harness)

`FinraSource` (keyless) pulls **`ConsolidatedShortInterest`** — NOT `EquityShortInterest`
(frozen since 2022-09-15, OTC-only). The symbol field is **`symbolCode`**. `settlementDate`
is a **partition key** — discover the latest cycle via the `/partitions/` endpoint; you
cannot sort it in the data query. The `record-max-limit` is **5000**, so paginate.
`days_to_cover` is FINRA-supplied; its `999.99` zero-volume sentinel is dropped to `None`
in the bridge. One bulk fetch per run, cached by settlement date, indexed in memory — **no
per-ticker request load**. The FINRA row shape + pure helpers are single-sourced in
`data/finra.py` (the `_form4.py`/`_edgar_facts.py` shared-leaf pattern) so the async
`FinraSource` and the sync scout fetcher (below) agree on one definition + cache contract.

## Short-interest discovery (scout)

`FinraShortInterestSignal` (`scout/signals.py`, keyless, **VPS-safe** — FINRA needs no
browser headers, unlike the Yahoo screener) is the **discovery analogue** of the per-ticker
`crowded_short` flag: it scans the same `ConsolidatedShortInterest` bulk dataset and surfaces
tickers whose short interest **jumped vs the prior settlement cycle**. Pure aggregator
(`short_interest_jumps_from_rows`) + a **sync** fetcher (`fetch_short_interest_rows`) that
**shares the harness `FinraSource` disk cache** (`.cache/finra/<settlement>.json`) — the
fetcher writes the **complete, UNFILTERED** rows so the async source still sees every symbol
(filtering happens in the aggregator, never before the cache write).

**It is a CONTESTED prior, NOT a defensible one** — heavy/rising short interest has a
*negative* base rate for a long book (Asquith-Pathak-Ritter 2005; Cohen-Diether-Malloy 2007 —
**the jump itself is the sharpest negative component**; Hong et al 2016 — DTC is a *stronger*
negative predictor than the level). So, unlike the 13D/Form-4 originators (established-positive
sign, shipped enabled), it **ships disabled at weight 0.5** and supplies **attention, not
direction**: the downstream quality/value/growth scorer + gates decide the sign (the positive
slice = *a good business the shorts are wrong about*), and the **selection ledger** measures
forward returns to earn it a weight (pre-registered promotion/kill rule in the spec). It uses a
**middle band** (a jump ≥ `min_jump_pct` off a **non-extreme base** — `prior_dtc ≤ max_prior_dtc`,
prior shares ≥ `min_prev_short_shares` — with current DTC in `[min_dtc, max_dtc]`, the
extreme-DTC falling-knife tail *excluded*), **not** the floors the advisory flag uses; an ADV
liquidity floor + a 5th-letter security-suffix drop (`*F`/`*Y`/`*W`/`*U`/`*R`/`*Q`) + a
`deny_list` filter the OTC/foreign/ETF junk the FINRA universe contains (scorer abstention is
the long-tail backstop). The split-flag + `999.99` sentinel drops mirror the bridge. Emits
**once per new settlement cycle** (the data updates only ~bi-monthly; `ScoutState.finra_last_settlement`
gates daily re-emission — the 7-day `cooldown_days` is too short). Tune via
`config.yaml: scout.short_interest` (+ `scout.signals.finra_short_interest`). Spec:
`docs/superpowers/specs/2026-06-29-finra-short-interest-originator-design.md`.

## Activist 13D discovery + selection ledger (scout)

`EdgarActivist13DSignal` (`scout/signals.py`, keyless, **VPS-safe** — pure SEC EDGAR, no
Yahoo WAF) is a **discovery originator** scanning the SEC daily index for fresh **initial
SCHEDULE 13D** filings (an investor crossing 5% *with intent to influence* — a leading
re-rating catalyst); the discovery analogue of the per-ticker `activist_13d` flag (which
only confirms a *known* ticker). **Discovery plumbing, NOT a scoring signal:**
`scoring.score()` is byte-identical, nothing reads it, so per `AUTONOMOUS_SCOUT.md §9` it
ships as a **defensible prior**, not behind the rank-IC backtest gate new *scoring* legs
require — the selection ledger (below) measures forward-return quality empirically instead
(13D events aren't in companyfacts).

**Verified facts (live-checked 2026-06-28; do not "fix" back):** the modern form label is
**`SCHEDULE 13D`**, not `SC 13D` (legacy, ~1/day — both accepted, `/A` amendments
excluded); initial volume **~4–12/day**; `get_filings` returns **every row twice** (dedup
by accession before any header fetch); the **subject company** is the target
(`filing.header.subject_companies[0]…cik/.name`), the filer is the *activist* (the filer's
ticker would be wrong); `company_tickers.json` lists the **common stock first** per CIK, so
CIK→ticker is **first-occurrence-authoritative with a sibling-relative-only** unit/warrant/
preferred backstop (a blunt suffix rule mis-binds ~54 liquid issuers to `*F`/preferred
siblings, e.g. EQNR→STOHF). Math/ingestion: `scout/cik_tickers.py` (resolver),
`scout/quality.py` (`is_initial_13d` / SPAC-shell + affiliate-overlap drops / marquee alias
boost), `scout/edgar_index.py` (`activist_stakes_from_records` aggregator +
`fetch_recent_activist_records` live, with the same "index not published till ~02:00 UTC →
walk back" fallback as the Form-4 path). The firehose is SPAC/affiliate/foreign-heavy, so
`quality.py` filters it; the scorer + `below_min_mktcap` gate remain the downstream skeptic.
Tune `scout.activist_13d` (`daily_cap`, `drop_spacs`, `drop_affiliates`, `marquee_boost`) +
`scout.signals.edgar_activist_13d`.

**Selection ledger + scoreboard (`scout/picks.py` + `state.py`):** each daily run records
the surfaced picks (ticker, catalyst, scores, **as-of price**, gated flag) into
`ScoutState` under a `picks` key (keyed upsert; old state stays forward-compatible). The
digest shows a **prior-picks scoreboard** — return-since-selection vs SPY at fixed horizons
(`pick_performance`, **split-safe**: one fresh adjusted Yahoo series, never a
fresh÷stored-scalar ratio) — so every report shows whether the signal catches winners.
Gated picks are recorded too (raw-signal measurement). Tune `scout.picks`.

**Daily digest mode:** `scout.daily_push.research: false` runs the push as a
**screen+gate+rank digest** (the scorer + a copy-paste **`/deep` block** of the non-gated
names) and **skips the Claude auto-research phase** — no daily Claude/FMP burn; default
`true` preserves the legacy decision-ready push. The `/deep` block + scoreboard are two
report sections (`report/sections.py`). The autonomous push still ships **OFF**
(`scout.daily_push.enabled: false`). Framed as **activist re-rating candidates to watch /
pass to `/deep`** (we enter after-close, so the edge is post-filing drift —
Bebchuk-Brav-Jiang 2015 — not the filing-day pop), screening triage, not advice.

## 8-K discovery + negative-item veto (scout)

`EdgarEightKSignal` (`scout/signals.py`, keyless, **VPS-safe** — SEC-hosted, no Yahoo WAF)
discovers 8-Ks whose items contain a configured AND-set (default **1.01∧3.03** —
Lerman-Livnat 2010's only positive-drift pocket) from **EFTS** (EDGAR full-text search;
shared leaf `data/efts.py` — the daily index carries NO item codes, EFTS returns them
inline, so a full day costs 3–6 requests). **A CONTESTED prior, NOT a defensible one**
(the FINRA short-interest pattern): the unconditional 8-K sign is NEGATIVE (Zhao 2017) and
filing-day moves reverse (Ben-Rephael et al 2021), so it ships **disabled at weight 0.5**
(`scout.signals.edgar_8k`) and supplies attention, not direction. Upgrading on the FINRA
precedent, BOTH halves carry **pre-registered backfill cohorts**
(`preregister/edgar_8k.yaml` / `edgar_8k_negative.yaml`: K=3m, window 2022–2025, blocks≥8,
frac≥0.90) via the generalized per-signal backfill (`shortlist-scout backfill --signal
8k|8k-neg`, spec table in `scout/backfill.py`; the 8-K **filer IS the subject** — no header
fetch; PiT `Symbology` at the FILING date; EFTS `sics` reused so scoring skips a
submissions fetch; **free-disk preflight aborts below 8 GB**). Walk-back scan
session−2..session with a capped accession-seen set in `ScoutState`; tune `scout.eightk`.
Two knobs are **live-only** (the backfill cohort measures neither): `daily_cap` (default 6/day
— a live truncation the uncapped backfill cohort never applied) and a populated `deny_list` —
either can make the live signal diverge from what the cohort measured, so keep `deny_list`
empty and `daily_cap` generous unless re-measuring.

The **negative-item veto** (`scout.eightk.negative_veto`, ships **ON**) is the defensible
half: items {1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01} are reliably negative over the
funnel's 30–90d horizon, so a fresh match **DROPS the candidate LOUDLY** between prefilter
and select (`funnel.apply_veto`) before it burns one of the ~10 FMP deep-screen slots —
named "VETOED: <tk> — 8-K item <item> filed <date>" manifest note (deduped by
ticker+accession in state; the name re-vetoes daily but is noted once),
`RunManifest.vetoed` count, and a loud STALE-state note when the sweep fails. **Measured,
not assumed**: every match logs once to the firehose as its own signal
**`edgar:8k_negative`** (accession-deduped; the 30-day pruned `eightk_negative` state map
loses history — the firehose is the permanent record) with its own prereg cohort whose
EXPECTED sign is negative (KILL-shaped CONFIRMS the veto). The swept-through cursor lags
`EFTS_LAG_DAYS` so late-indexed filings still veto; cold-start walk-back is bounded at
`lookback_days`. Removing the `negative_veto` block gives the byte-identical pre-feature
funnel. `scoring.score()` is untouched by all of this.

**EFTS gotchas (live-probed 2026-07-07, twice — do not "fix" back):** needs **browser-ish
UA + Accept headers** (bot-shaped requests are rejected — the Yahoo-WAF lesson, and keep
`Accept-Encoding` httpx-decodable); **intermittent 500s are normal** → bounded
retry-on-5xx ONLY (never retry 4xx), backoff capped at 8 s, ≤3 req/s throttle; **EFTS
lags** — today's date returns `total: 0`, so the walk-back window and the day-cache
finality rule (`day <= fetched_on − EFTS_LAG_DAYS`) are load-bearing; **`forms=8-K`
filters `root_forms` and RETURNS `8-K/A` rows** — the `file_type != "8-K"` drop is
mandatory everywhere (an amendment would double-fire the originator, re-trigger the veto,
and double-count backfill events); the ES pagination window is **`from+size ≤ 10k`** — any
range whose `total ≥ 9,900` splits recursively at the date midpoint (earnings-heavy months
approach the cap). `.cache/efts/<day>.json` always stores the COMPLETE unfiltered day
(filtering is the aggregators' job, never before the cache write). **No `display_names`
ticker fallback anywhere** — CIK→ticker only via `cik_tickers` (live) / `Symbology` (PiT
backfill); names feed only the SPAC check. Spec:
`docs/superpowers/specs/2026-07-07-eightk-originator-design.md`.

## 13F marquee-fund cloning (scout)

`EdgarThirteenFSignal` (`scout/signals.py`, keyless, **VPS-safe** — pure SEC, no Yahoo
WAF) is a **discovery originator** that clones **new positions** in a curated set of
marquee funds' latest **13F-HR**. Per fund CIK (config `scout.thirteenf.funds`): pick the
latest EXACT `13F-HR` (`13F-HR/A` amendments **excluded** — a restatement diff would
double-fire), diff its holdings against the immediately-prior 13F-HR, and surface each
**new** position (a 9-char CUSIP present now, absent before) whose within-book weight
clears `min_position_pct` (0.005). Unlike the FINRA/8-K **contested** originators, this is
a **DEFENSIBLE, established-positive prior** (Martin-Puthenpurackal 2008 — cloning at
disclosure earned abnormal returns; Cohen-Polk-Silli 2010 — managers' "best ideas"
outperform), so it ships **ENABLED at weight 1.0** (the 13D shipping bar) — but *below*
the 13D/Form-4 tier because the info is up to **45 days stale**: the clone return is
measured from the **FILING date**, the disclosure lag priced into the literature (we're
not front-running the trade).

**Math/ingestion:** `scout/thirteenf.py` (pure `parse_infotable` / `aggregate_positions` /
`new_position_diff` / `thirteenf_emissions` + throttled fetch) and `scout/cusip_map.py`
(the CUSIP→ticker resolver). **Verified facts (live-checked 2026-07-09; do not "fix"
back):** a single holding legitimately spans **multiple `<infoTable>` rows** (sole/shared/
none voting split, combined-manager filings) — **aggregate by CUSIP, sum `value`** (within-
filing weights normalize away the 2023 $-vs-$1000s reporting change); **drop rows with a
`putCall`** (options) and **`sshPrnamtType != "SH"`** (PRN convertible debt); the
information table is the filing directory's `.xml` that is **neither `primary_doc.xml` nor
`xslForm13F...`** (arbitrary numeric name → an `index.json` fetch is required). The 7 seed
CIKs are **live-verified active filers** (stale /ADV shells like Baupost 1054420 / Appaloosa
1006438 are the trap — the config comment names them).

**CUSIP→ticker resolver** (`scout/cusip_map.py`, layered, abstains rather than guesses):
(1) **SEC fails-to-deliver files** (`cnsfails{YYYYMM}{a|b}.zip`, `SETTLEMENT|CUSIP|SYMBOL`
rows, ~58k/file) — the 2 most recent published, walk-back-bounded at 6 attempts for the
publication lag, cached **forever** by filename (immutable once posted), **most-recent-
settlement wins** on symbol churn; (2) **exact-normalized-issuer-name** fallback against the
`company_tickers.json` titles (uppercase, strip punctuation + INC/CORP/CO/LTD/PLC-style
suffixes, EXACT equality only — ambiguous names abstain); (3) **None** (abstain, counted in
`available()` detail). Every sec.gov request (submissions, index, infotable, FTD zips) goes
through the signal's own **~3 req/s min-interval throttle** (`SecThrottle`) — there is NO
shared sec.gov throttle to reuse.

**Seen-accession semantics** (`ScoutState.thirteenf_seen_accessions`, `_append_capped`,
forward-compatible): a fund's latest 13F-HR is marked **processed even when the diff yields
zero new positions** (else an empty-diff fund re-downloads both infotables daily forever) —
so the state exposes **`processed_accessions`**, NOT emissions, for the `daily.py` persist
hook. `max_filings_per_day` (default 3) caps processing (13F is quarterly-bursty: all funds
file mid-Feb/May/Aug/Nov); unprocessed filings stay **unseen and carry over** to later
sessions (never dropped). **Known limit (stated up front):** the CUSIP resolver yields a
ticker but no CIK, so 13F emissions carry **`cik=None`** — firehose events can't use
CIK-based delisting classification, and a **backfill cohort is deferred** (a PiT
CUSIP→symbology replay would leak post-event symbols; the picks ledger + firehose measure
the live signal from day one). `scoring.score()` is untouched. Tune `scout.thirteenf` +
`scout.signals.edgar_13f`. Spec:
`docs/superpowers/specs/2026-07-09-thirteenf-buyback-originators-design.md`.

## WSB social hype (harness + scout)

`WsbSource` (keyless) and the scout `WsbHypeSignal` both read **ApeWisdom**
(`apewisdom.io/api/v1.0/filter/wallstreetbets`) via the shared `data/apewisdom.py` leaf —
one bulk GET/run, disk-cached by fetch date (`.cache/apewisdom`, shared). It populates the
`social` aux section (excluded from coverage) → bridge `social_*` metrics → the soft
**`social_hype`** flag (advisory). SwaggyStocks has no keyless API; ApeWisdom is the free
documented substitute (mention volume + 24h delta, not finance-tuned bull/bear). Tune
`flags.social_hype` (scoring) / `scout.wsb_hype` (discovery thresholds + index-ETF deny-list).

## Government contracts (harness + research)

`GovContractsSource` (keyless) queries USAspending's **`spending_by_transaction`** endpoint
for trailing-24m federal **procurement** obligations (award types A/B/C/D), resolving
ticker→recipient via SEC `company_tickers.json` and confidence-filtering recipients
(`data/govcontract_match.py`; **abstains** below `gov_contracts.match_min_confidence` rather
than mis-attribute). It populates `gov_contracts` → bridge `gov_contract_*` (TTM/prior-TTM
obligations, YoY, materiality-to-revenue), in `--json`. **No scored leg or flag in v1** —
the signal is lumpy + fuzzily attributed, so it rides only as a caveated **research context
line** (`research/gov_contracts.py`, the reverse-DCF pattern: in the prompt, **never** the
grounding haystack). **Use `spending_by_transaction`, NOT `spending_by_award`** (the
latter's `time_period` is an award-overlap filter returning un-window-scoped totals — would
double-count decade-old awards; live-verified). Flag + scored leg deferred to Phase 2 (gated
on matcher-recall validation + accumulated snapshots — USAspending isn't in companyfacts, so
only the snapshot-replay path can measure IC). Known limit: subsidiary awards booked under
other names are under-counted (a small alias seed map covers marquee defense parents).
Self-caches `.cache/usaspending` (`cache.py` is GET-param-keyed, can't key a POST body).
Tune `gov_contracts` + `research.gov_contracts`.

## Federal lobbying (harness + research)

`LobbyingSource` (keyless) queries the **official Senate LDA REST API**
(`lda.gov/api/v1/filings/`, base URL config-driven — `lda.senate.gov` is retired after
2026-06-30) for trailing federal lobbying-disclosure **spend**, resolving ticker→client via
SEC `company_tickers.json` and confidence-filtering clients (`data/entity_match.py`, a
generic matcher; abstains below `lobbying.match_min_confidence`). Per filing, spend =
`income` (outside-firm fee) **or** `expenses` (in-house), summed across registrants and
bucketed TTM/prior-TTM by `dt_posted`. It populates `lobbying` → bridge `lobbying_*` (TTM
spend, YoY, registrant count), in `--json`. **No scored leg or flag in v1** — caveated
**research context line** (`research/lobbying.py`, reverse-DCF pattern: prompt, never the
haystack). **No `to_revenue`** (lobbying spend is tiny vs revenue; the signal is presence +
YoY trend). Keyless LDA allows ~15 req/min → Retry-After-aware backoff
(`lobbying.max_retries`), self-caches per `(ticker, day)` (`.cache/lda`). Known limit:
registrant/parent rollup isn't resolved. Tune `lobbying` + `research.lobbying`.

## News flow (harness)

`FinnhubSource` pulls **`company-news`** (free tier, exact ticker join) into the `news` aux
section (`NewsFlow`: 7d/prior-7d/30d article counts + latest date) via the pure `_news_flow`
helper. The bridge derives `news_count_7d` / `news_count_prior_7d` / `news_count_30d` /
`news_flow_rising` / `news_truncated` / `news_data_age_days`. Surfaced as the soft
**`news_spike`** flag (elevated + rising + fresh; `flags.news_spike`), mirroring
`social_hype` — advisory only, no-op when the block is absent. **Finnhub's free tier caps
company-news at ~250 articles**; for a high-volume name (AAPL) the 30d window collapses to
the last few days, so `_news_flow` **detects the cap** (`truncated`), blanks the unreliable
prior count, and the flag is **suppressed on truncated names** (a spike is meaningful for a
normally-quiet name, not an always-noisy mega-cap). Distinct from WSB hype (retail chatter)
— this is mainstream press volume. Cached 6h.

## Earnings execution (harness + research)

`FinnhubSource` pulls **`stock/earnings`** (last ~4 quarters of actual-vs-estimate
surprises) and **`calendar/earnings`** (next scheduled report) into the `earnings` aux
section (`Earnings`) via the pure `_earnings` helper. The bridge derives
`earnings_beat_rate`, `earnings_avg_surprise_pct`, `earnings_last_surprise_pct`,
`earnings_quarters`, `earnings_days_to_next`. Surfaced as a research context line
(`research/earnings.py`, reverse-DCF pattern) — beat consistency is a quality/PEAD-drift
signal, an imminent report a near-term catalyst. **Not scored in v1.** `surprisePercent`
is already in percent (don't ×100). Free tier; cached 1d (history) / 6h (calendar).

## Proxy statement (DEF 14A) compensation & governance (research-only)

`research/proxy.py` (keyless, **research-layer only — no harness Source**) reads the latest
**DEF 14A** via edgartools' `ProxyStatement` and renders a **caveated context line** for the
brief (`research.proxy`, the reverse-DCF pattern: **prompt only, NEVER the grounding
haystack** — a computed/interpretive proxy claim must not pass quote-verification as a filing
fact). **Not scored, gated, or flagged** (ASSESSMENT_GAPS §3.1). The proxy's reliable signal
is **structured XBRL** (Item 402(v) "Pay versus Performance", mandatory since FY2023), **not
narrative** (no clean related-party/CD&A extractor; raw text ~350K chars), so v1 reads
structured fields only: CEO total + **actually-paid** comp (sign preserved — can be
negative), the **CEO-to-average-NEO pay multiple** (`peo_total/neo_avg`), pay-for-performance
alignment (actually-paid vs TSR trend), **5%+ beneficial ownership / control concentration**
(the `0.5` "<1%" director sentinel dropped via `_is_real_pct`), CEO pay ratio (context only),
and governance-hygiene booleans. **Fetched per deep-dive in `assess()`** (NOT on every
screen's snapshot — the heavy fetch stays out of the harness, the `filing_text_change`
precedent), point-in-time (`fetch_proxy(ticker, as_of=…)`), failure-isolated (any error →
line omitted), accession-cached. A `governance` reconciliation token + the conditional
`PROXY_SYSTEM_ADDENDUM` keep the brief **byte-identical when `research.proxy` is absent or
`enabled: false`**. Evidence-framed: "associated with governance/valuation, **not** a return
prediction; founder control is double-edged." Ships **ON** (`research.proxy`: `max_holders`,
`control_pct`). **Phase 2 (deferred):** the narrative related-party/CD&A sections + a
`pay_for_performance_alignment` backtest axis (the PvP table is *structured* XBRL — a legit
future candidate, unlike narrative inputs). Spec:
`docs/superpowers/specs/2026-06-27-def14a-proxy-reader-design.md`.

## Lazy-Prices filing-text-change flag (research/PiT; PREDICTIVE_SIGNALS §4)

The "Lazy Prices" signal (Cohen-Malloy-Nguyen 2020: big YoY 10-K/10-Q text changes predict
negative returns) ships as a **config-gated advisory flag**, NOT a scored leg:
`flags.filing_text_change` fires when `filing_text_similarity < max_similarity` (default
0.7), is **byte-identical** when the block is absent, and never affects
`passed`/`composite`/`scored`. The metric is in `--json`. Similarity is a **stdlib
bag-of-words cosine** (`research/textsim.py`, `collections.Counter`, **no new dependency** —
NOT an extension of `riskdiff`, which is an Item-1A block extractor): `normalize_tokens`
(lowercase, strip digits/currency/punctuation, collapse whitespace) over the WHOLE Item-1A +
MD&A, so boilerplate/number/renumber churn cancels (the false-positive guard). LOW similarity
(big rewrite) → the signal; `None` when either side lacks a baseline (never fabricated as 0.0).

**Point-in-time:** `research/filings.py:filing_text_change(ticker, form, as_of)` compares the
current same-`form` filing vs the immediately-prior one, restricted to acceptance date
`≤ as_of` (the look-ahead guard for replay; `as_of=None` = live "now").

**Scope:** full filing text is NOT in the harness snapshot — `EdgarSource` fetches Form 4 +
financials + filing-index only; full text lives in the research layer. A heavy per-ticker
full-text fetch on `EdgarSource` was **deliberately deferred**, as was the snapshot-replay
backtest axis (text isn't in companyfacts). The PiT accessor is built + tested so future
wiring is correct-by-construction; the flag is exercisable today via any path that sets
`m.filing_text_similarity`.

## SUE / post-earnings-announcement-drift leg (scoring; PREDICTIVE_SIGNALS §1)

The **`momentum.sue`** block folds a **standardized earnings surprise** leg into
`momentum_score` (Bernard-Thomas 1989 drift; Novy-Marx 2015 — earnings surprise is the
*fundamental* momentum price momentum only weakly proxies). Ships **OFF**; **byte-identical**
when absent. `SUE = last_surprise_pct / dispersion`, **decayed** linearly to 0 over
`decay_trading_days` (~60) since the last announcement. Reuses already-fetched Finnhub
earnings — no new feed. Two prerequisite bridge derivations:
- **`earnings_surprise_dispersion`** — population std-dev of the firm's own recent surprise
  %s (`stats.surprise_dispersion`, ≥3 quarters), the SUE denominator.
- **`earnings_days_since_last_report`** — the decay anchor, a three-tier APPROXIMATION
  (best available first): (1) the most recent PAST `calendar/earnings` entry with an
  `epsActual` — a true announcement date, but **Finnhub's FREE tier returns no historical
  calendar entries at all** (live-probed 2026-07-09; the request still reaches back ~120d
  so a paid key activates this tier); (2) the **EDGAR 10-Q/10-K filed date**
  (`Events.last_report_filed`, exact forms only — /A amendments would wrongly freshen it;
  needs `edgar` in the source chain), a ~0-5d proxy applied in the bridge as
  `max(quarter_end, filed)` and only when `Earnings.last_report_date_estimated` (never
  degrades a true date; truth is bracketed quarter_end ≤ announcement ≤ filed); (3) the
  fiscal quarter-END `period` — over-states staleness ~30-45d, so the leg decays a touch
  fast. Old persisted snapshots lack the `estimated` flag → `from_dict` defaults it True,
  so snapshot-replay gets tier 2 retroactively. Keyed off the PAST report — **never**
  `days_to_next` (unannounced → look-ahead).

**Mandatory σ-guard** (`stats.sue` + `scoring._sue_value`): abstain (None) when dispersion
is None / below `sigma_floor` (the all-equal / common 4-equal case has σ≈0) **or** fewer
than `min_quarters` (~3) surprises exist — so the leg never divides by ~0. A beat → higher;
a fully-decayed stale print → exactly 0 (a real "no drift left", not an abstention). Never
sector-masked. Inputs in `--json`.

**Measurement (deferred):** SUE is **NOT a live-price backtest axis** — the momentum
backtest replays price-only snapshots (no earnings), and historical surprises aren't in
companyfacts. A backtest-only `scoring.sue_score` + a `sue ~ momentum` collinearity pair are
wired and `SnapshotSignalSource` emits a `sue` axis, but it rides ONLY the **guarded
snapshot-replay path** and **no-ops until daily accumulation captures the earnings fields**.
Unfitted priors.

## Residual (idiosyncratic) momentum leg (scoring; PREDICTIVE_SIGNALS §2)

The **`momentum.residual`** block folds a **residual-momentum** leg into `momentum_score`
(Blitz-Huij-Martens 2011): the **12-1 momentum of CAPM residuals**, vol-standardized — raw
momentum with the market-beta exposure stripped out. Ships **ON and fires on live screens**;
**byte-identical** when the block is absent (the invariance tests build configs without it
and pass). **Enablement:** the **live-price backtest** validated it as the **only** new
signal with a significant XS t-stat — **1m XS rank-IC +0.023, t=2.6** — while **raw**
momentum is anti-predictive there (TS-IC −0.03, t=−3.9). **Caveat:** short-horizon — its
edge **decays past ~3 months**, so a near-term de-betaed tilt, not a long-hold thesis.

**The real work was the price plumbing.** The regression needs **date-aligned** stock + SPY
series, but the live merge reduces Yahoo closes to scalars and `snapshot_from_closes` throws
dates away. The dated backtest seam (`snapshot_from_closes_dated`) always had them; the
**live `YahooSource.fetch` path now plumbs dates too** (`_dates_from_chart(raw)` aligned 1:1
to `_closes_from_chart`, `_spy()` caches `(_spy_dates, _spy_closes)` once/run, `fetch` →
`_normalize_yahoo(...)`). **Fallback:** date-less/misaligned payloads (`len(ts) !=
len(series)`, e.g. old cached 5y payloads with no `timestamp` array) → `_dates_from_chart`
returns `[]`, `Price.residual_momentum` stays **None** (the leg abstains; a screen never
crashes). The bridge copies it to `StockMetrics.residual_momentum`.

**#1 correctness risk — date-join, NOT position-pairing.** `stats.join_on_dates`
**inner-joins** the stock + SPY closes on their shared date keys **before** any return is
computed. Position-indexing `closes[i]` vs `spy_closes[i]` misaligns on different listing
dates/halts/lengths → a garbage beta. `stats.residual_momentum` joins first, OLS-regresses
stock returns on market returns (**stdlib only — manual covariance/variance, no numpy**),
takes residuals over **t-12..t-2 (the 12-1 skip is preserved — raw momentum reverses in the
latest month)**, and standardizes by `sd(resid)`.

**Point-in-time:** the caller passes series already truncated to `as_of`
(`PriceHistory.through`/`closes_through`), so beta/residuals/sum use only data `≤ as_of` (a
regression test corrupts post-`as_of` closes and asserts invariance). **Residual-vol guard**
(`sd == 0` / too few points / flat market window) → None.

**Measurement axis (LIVE-price, unlike SUE):** residual momentum is reconstructable from
prices, so it rides the live-price `MomentumSignalSource` as a real `residual_momentum` axis
with a backtest-only `scoring.residual_momentum_score` + a `residual_momentum ~ momentum`
collinearity pair. It **will** correlate with raw momentum (it IS momentum, de-betaed) — the
diagnostic confirms it **dominates on rank IC**, not orthogonality. That backtest is done;
the leg is enabled and live. Band/window remain unfitted priors.

The three **§2 price-refinement axes** (`pct_to_52w_high` George-Hwang, `max_daily_return`
Bali MAX-effect, `vol_scaled_momentum` Barroso-Santa-Clara) ride the same live-price source
as **backtest-only measurement axes** (pure `closes` fns, carried on `Price`/`StockMetrics`,
in `--json`, excluded from coverage via `_NON_SIGNAL_FIELDS`; `momentum_score`
byte-identical). **All three were measured on both universes and NONE earned wiring**
(`docs/superpowers/specs/2026-06-28-price-signal-bundle-results.md`): `pct_to_52w_high`
duplicates `price_vs_200dma` (corr ~0.70–0.74), `vol_scaled_momentum` duplicates raw scored
momentum (~0.52–0.54), and `max_daily_return` is orthogonal but its sign **flips across
universes** (the MAX/lottery effect reverses in mega-caps). Measured-and-parked — the
unfitted-prior bands stay only so the axes remain measurable.

## Yahoo screener WAF gotcha (scout discovery)

The scout's `YahooScreenerSignal` (`scout/signals.py`) hits the **unofficial**
`query1.finance.yahoo.com/v1/finance/screener/predefined/saved` endpoint. A `429` there is
almost always a **cold-start fingerprint block from Yahoo's edge WAF, not throttling**: a
bot-shaped (UA-only) request gets an **HTML** `429` (`content-type: text/html`), while a
**full browser header set** (`_YAHOO_HEADERS` — `Accept`/`Accept-Language`/`Accept-Encoding`/
`Sec-Fetch-*`/`Origin`/`Referer`) returns `200 JSON` (no crumb needed). That's why it "never
worked" on a fresh machine — the header shape is identical everywhere, so the rejection was
deterministic.

Headers are the **primary** lever but **not proven sufficient on a truly cold IP** (a
secondary per-IP reputation effect: once one well-formed request succeeds the IP is trusted
for a window). So the **per-run bail-out + cross-run cooldown are load-bearing and must not
be removed**: on an HTML 429 the signal bails after a *single* request (no retry, doesn't
fire the remaining screens), and `daily.py` persists a rest-of-day cooldown in `ScoutState`
(`mark_yahoo_blocked`/`yahoo_blocked_on`). Only a JSON 429 *with* a `Retry-After` is retried
(once, capped). **Never retry-spam an HTML 429** (that earns a real ban). `Accept-Encoding`
must stay a subset of what httpx can decode (no `br`/`zstd` without the dep, or `.json()`
fails). `query2` is a manual escape hatch only — no auto-failover.

## Scale / rate limits (the honest catch)

Free tiers fit individual names or a small watchlist, not a full universe. The harness makes
**~13 FMP calls/ticker**; FMP's **250/day** free limit ≈ **19 tickers/day**. (The scout caps
deep-screening at **10/day** (`scout.daily_x`); `shortlist-accumulate` at **15/day**
(`--max-tickers`).) Daily S&P 500 needs FMP's paid **Starter (~$14–20/mo)** or the **caching
layer**. **Finnhub's 60/min is comfortable.**

On the limit FMP returns **`429`** and the harness degrades honestly: `FMPSource._get`
retries with `Retry-After`-aware backoff (`fmp.max_retries`), the collector keeps
already-succeeded sections, and coverage reports a distinct `rate_limited_429` status (vs
`402` gating). Retry can't manufacture quota — the real fix for repeated runs is caching
(below).

## Caching (`cache.py`)

A persistent SQLite HTTP-response cache wraps the harness `FMPSource`/`FinnhubSource` `_get`
boundaries, so a warm re-run of the same basket within TTL makes **zero** upstream calls.
**On by default** (`.cache/http.sqlite`, gitignored); `--no-cache` disables it,
`--refresh-cache` bypasses reads + repopulates, `--demo` runs cache-off (offline). TTLs are
per data half-life and config-driven (`cache.ttl.<bucket>`); the endpoint→bucket map is keyed
on `(provider, path)`. A process-global singleton
(`configure_default_cache`/`get_default_cache`) gives every entrypoint caching without
build-path plumbing.

Two things to keep right: (1) **never cache soft failures** — FMP/Finnhub return 200-OK with
empty `[]`/`{}` or `{"error":…}` on gating, so the `_is_cacheable` predicate (not
`raise_for_status` alone) gates writes; (2) **bump the `v1:` key prefix** in `cache_key`
whenever a `_get`/normalizer output shape changes, or stale-shape payloads serve until TTL.
Design: `docs/DATA_SOURCES.md` §6. Yahoo/FINRA keep their own disk caches; EDGAR (free,
uncapped) is intentionally uncached.

## Data scale conventions

- Margins/returns are **fractions** (0.42 == 42%). **FMP `/stable/` returns fractions**
  (use as-is); **Finnhub returns percentages** (÷100 via `_pct`). Don't double-convert.
- **`market_cap` is absolute dollars** (FMP `quote.marketCap`). **Finnhub reports
  millions** (×1e6 via `_millions`). The `below_min_mktcap` gate + insider net-flow ratio
  assume dollars, so Finnhub is the free fallback denominator when FMP gates a symbol —
  without it EDGAR's insider dollars can't be normalized and the insider sub-score goes
  `null`.
- Equity-centric moat/quality proxies are undefined for banks/insurers/REITs (e.g. SCHW) —
  **masked and abstained** per sector (below). Sector-specific *recalibration* of the
  surviving legs is still future work.

## Sector-aware applicability & abstention

The scorer detects the sector and **abstains** structurally-undefined legs (gross margin /
FCF-yield / leverage for a bank) explicitly instead of silently dropping-then-averaging them.

- **Detection is SIC-based, EDGAR-only.** `EdgarSource` emits a partial `Profile(sic=…)`
  (one lightweight SEC request) → bridge `m.sic`. `sectors.py:resolve_bucket` maps SIC →
  bucket via `config.yaml: sectors.buckets` (an **ordered** list; first matching range
  wins). Scoring **never** reads the free-text `StockMetrics.sector` — only `m.sic`. No
  EDGAR / no `SEC_IDENTITY` → `unknown`.
- **`unknown` is a bit-identical no-op** — no masking, any present leg scores, always
  `scored`. The abstention floors are bucket-gated, touching only masked sectors (the
  back-compat guarantee for operating companies; explicit regression test).
- **v1 masks** (`sectors.masked_legs` / `masked_gates`) for financials/insurers/REITs:
  `gross_margin`, `gross_margin_stability`, `roic`, `fcf_yield`, `fcf_cagr`,
  `interest_coverage`, `debt_to_equity`, plus the `negative_fcf`/`over_leveraged` gates.
  `net_margin` is **not** masked (defined, only miscalibrated → deferred). Exchanges (6231),
  asset managers (6282), funds, SPACs, real-estate operators are left `unknown`/unmasked.
- **`ScoreCard` gains** `sic_bucket`, `confidence` (present-applicable weight ÷ applicable
  weight), `scored` (above the validity floor; always True for `unknown`), `abstentions`
  (`{field, reason: inapplicable|missing, scope}`). In `--json` + CSV. **`passed` is now
  `not gates and scored`** — a not-scored name can't pass, top-rank (sort key `(scored,
  composite)`), or be selected for research.
- **Coverage vs abstentions don't contradict:** a masked-inapplicable `None` is excluded
  from `coverage.unavailable` (it isn't a data gap); per-leg *missing* is left to coverage.
- Tune in `config.yaml: sectors` + `validity` — `sectors.py` is the only interpreter.

## Extension scaffolds (not wired)

`providers/extensions.py` has `QuiverProvider` + `FredProvider` stubs. **FRED has shipped**
as a run-level macro overlay (`data/macro.py:fetch_macro` — risk-off regime, display +
advisory only; the `FredProvider` stub now raises). **Quiver is largely superseded** — gov
contracts, lobbying, and WSB shipped keyless (USAspending / Senate LDA / ApeWisdom); its one
net-new feed, **congressional trades, is a contested prior** (no post-STOCK-Act aggregate
alpha; verdict in `docs/PREDICTIVE_SIGNALS_RESEARCH.md` → deferred/rejected): if ever wired,
a disabled-by-default scout discovery originator on the FINRA short-interest pattern, never
a scored leg or auto-copy. **The stubs predate the
harness (retired `Provider` interface)** — to wire Quiver, reimplement it as an async
`Source` in `data/sources.py` and register it in that module's `_REGISTRY` (the
`--provider`/`harness_sources` chain resolves against the harness Source registry, **not**
`providers/__init__.py:build_providers`).

## Skills

- **`/run`** — end-to-end screener skill (gather tickers → check env → `uv run shortlist
  --json` → interpret scores/gates/opportunity axis/null sub-scores/coverage gaps).
  `.claude/skills/run/SKILL.md`.

## Qualitative research layer (`shortlist/research/`)

Opt-in `--research N` enriches top-N non-gated names with a Claude-written 10-K brief via
the **`claude` CLI in headless mode, not the API SDK** (no key; uses CLI auth). The runner
(`research/claude_cli.py`) MUST keep the lockdown flags — `--tools "" --strict-mcp-config
--max-turns 1`, prompt on stdin, neutral cwd, and NO `--bare` (bare forces
`ANTHROPIC_API_KEY`). The package is lazy-imported so the core screener works without
`claude`/edgartools. Briefs are cached by filing accession; facts are quote-verified against
the filing, interpretive prose labeled. The summary prints to stderr (keeps `--json` stdout
clean). Output under `research/` (gitignored).

The brief bundles three EDGAR docs (`filings.py:fetch_bundle` → `FilingBundle`): the latest
**10-K** (primary, displayed), the latest **10-Q's MD&A** (Part I Item 2 via
`get_item_with_part`, **NOT** the TenK `management_discussion` attribute), and a **YoY
Item-1A risk-factor diff** (`riskdiff.py`, stdlib `difflib`) surfaced as a distinct
`added_risks` section. Cached on a **composite key** (`<10-K-acc>+<10-Q-acc>`) so a new
quarter invalidates; the prior-year 10-K is a diff baseline only and **never enters the
prompt or the haystack** (`FilingBundle.haystack()` excludes it). `added_risks` is parsed
leniently. Tune `research.risk_diff` / `max_added_risks` / `max_chars.tenq_mda`.

The QUANT CONTEXT carries a **reverse-DCF "price-implied FCF growth" line**
(`research/reverse_dcf.py`, **ON**) — a deterministic, **research-only** reframing the EV/EBIT
review routed *out* of the composite (ASSESSMENT_GAPS §2.2). A single-stage Gordon inversion
(`g = discount_rate − F0/market_cap`, `F0` = median of the last K positive FCF years) prints
*"market embeds ~X%/yr perpetual FCF growth"* for Claude to reconcile against realized CAGR.
A **framing aid, NOT a scored/backtested signal**: the scorer is byte-identical (pinned by a
`test_scoring.py` invariance test), no field/feed added, the line is in the prompt but **not**
`haystack()`. Three hardening choices (spec §12): **single-stage** (two-stage is a monotone
transform → false precision); the nudge is **symmetric** (high implied growth is rational for
a durable compounder — never read as "expensive" alone); it abstains on non-finite/non-positive
input, with a run-rate caveat when the latest FCF outruns the median base. `enabled: false` →
byte-identical (cached briefs need `--refresh`).

Two more **context lines** (prompt-only, NOT haystack — so a value can't pass
quote-verification as a 10-K fact): the recent-SEC-filings line (`filing_events`) and the
recent insider Form-4 trades line (`assess.py:_insider_line`, `research.insider_detail`,
**ON** — role/name/direction/$amount/date from `StockMetrics.insider_recent`, capped at
`max_items`). Both labeled "context only"; `enabled: false` → byte-identical.

The brief ends with a **screening call** (`research/models.py:ScreeningCall`,
`research.screening_call`, **ON**) — a buy/hold/avoid stance + conviction + one-sentence
rationale, authored by Claude but bounded by three deterministic guards in
`assess.py:apply_guards`: a **gate clamp** (a tripped gate can only move the stance more
bearish), a **conviction cap** (low `confidence` or a real data gap forces ≤ MEDIUM), and a
**HIGH-conviction corroboration** requirement. The "decided without" / "not applicable" lines
are **Python-owned** (`research/coverage_caveat.py`, never the LLM). `enabled: false` is
byte-identical **for freshly generated briefs** (cached briefs are config-agnostic; clear
`research/` or `--refresh-cache`). An LLM synthesis, **not** backtested — the per-brief JSON
persists the call + an `as_of_price` snapshot for a later hit-rate. Framed as **screening
triage, not investment advice**; every standalone surface (badge, scout pill, bot line)
carries that tag. JSON key `call` (Python field `screening_call`); label "Screening call".
