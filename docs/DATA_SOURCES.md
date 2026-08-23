# Data sources for stock assessment — current state and a roadmap

> **Companion doc:** [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) covers the **methodology**
> gaps — the scoring model (incl. the growth sub-score spec), its validation/backtest, and
> the Claude qualitative layer. This doc is about the **data feeds** that back them.

**Audience:** whoever extends the screener/harness next.
**Bar:** what a buy-side analyst would actually want under the hood — authoritative,
point-in-time, and honest about coverage — not a pile of vanity feeds.

This document (1) inventories what we pull today, (2) names the assessment gaps that
leaves, and (3) specifies concrete additional sources, *why each earns its place*,
how to pull it, and where it plugs into the existing two-layer architecture
(`StockMetrics` + `scoring.py`, or `TickerSnapshot` + `merge_snapshots`, or a new
run-level macro overlay).

Every keyless source below was **actually pulled** into `scratch/` by
`scratch/pull_samples.py` — the numbers quoted are from that live run (AAPL, 2026-05-31),
so the pull code is verified, not hypothetical. Re-run it with `python3 scratch/pull_samples.py`.

---

## 1. What we pull today

| Source | Tier | Layer | Supplies |
|---|---|---|---|
| **FMP** `/stable/` | free (250 calls/day, ~20 tickers/day) | both | profile, quote, TTM ratios, key-metrics, 5y statements, price-target + grades consensus, insider, price-change |
| **Finnhub** | free (60/min) | both | profile, metrics, **insider sentiment (MSPR)**, recommendation trend, quote |
| **SEC EDGAR** (Form 4 + 10-K + events) | free (≤10 req/s) | both | authoritative insider transaction flow; 10-K XBRL financials (revenue/FCF/EPS); **filing-stream events: 8-K material events, SC 13D/13G activist/passive stakes, Form 144 planned insider sales** (harness only) |
| **Yahoo Finance** chart | free, keyless | harness | price history → 200dma, 6m rel-strength vs SPY, realized vol, max drawdown (computed by us, day-cached) |
| **Mock** | offline | harness | demo fixtures |
| **FRED** | free key (`FRED_API_KEY`) | harness (run-level) | macro regime: HY OAS, 2s10s curve, VIX, 10y, FFR → `MacroContext` → `risk_off_regime` flag |
| Quiver | scaffolded, not wired | screener | congress/gov-contracts |

A `snapshot_to_metrics` **bridge** (`data/bridge.py`) feeds the harness
`TickerSnapshot` into the scorer, exposed via `shortlist` (walkthrough in
`HARNESS.md`).

Scored signals today: **quality** (ROE, net margin, interest coverage, D/E),
**moat** (gross margin, margin stability, ROIC), **growth** (revenue/FCF/EPS CAGR +
YoY persistence), **momentum** (price vs 200dma, 6m rel strength, EPS revision),
**value** (upside-to-target, FCF yield, PE vs own history, PEG), **insider**
(sentiment + net-flow/mktcap).

## 2. Gaps a professional would flag

These are the holes that matter, ranked by how much they distort an assessment:

1. **Momentum is partly addressed.** ~~`rel_strength_6m` and volatility/drawdown were
   unfilled.~~ **Done (harness):** the Yahoo source now computes 6m relative strength vs
   SPY, realized volatility, max drawdown, and the 200dma ourselves — keyless and immune
   to FMP gating (see Tier A below). **Still open:** `eps_revision` (needs forward
   estimates — Alpha Vantage §B1) and beta.
2. **No forward estimates / revisions breadth.** Everything is trailing (TTM). PEG needs a
   growth rate; `eps_revision` needs an estimate trend. Analyst *target* ≠ analyst
   *estimate revisions*, which is the cleaner momentum signal. **Partly closed:** the
   *rating* revision (change in buy/hold/sell counts) ships as `/deep` + report context
   from the Finnhub history we already fetch. **Still open:** EPS *estimate* revisions,
   which are a different feed (Alpha Vantage §B1) and the one `eps_revision` needs.
3. **No earnings-quality red-flag.** ~~Accruals, restatements, and going-concern language are
   how you avoid value traps.~~ **Partly closed (research):** `/deep` now detects management's
   own conclusion that internal control over financial reporting or disclosure controls were
   **not effective as of this filing's period end** (`research/controls.py`), and the events
   source flags a fresh 8-K item 4.02 non-reliance restatement, 4.01 auditor change and 3.01
   listing deficiency. A DISCLOSURE, not an inference — which is why it shipped ahead of the
   Tier-D M/Z composites below. **Still open:** accruals (measured and disabled), Altman Z,
   Beneish M, and going-concern language (unvalidatable on the current universes — 0 of 228).
   Base rates, the phrase set and the tense rule that makes it work:
   `docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md`.
4. **No macro/risk regime.** A 9/10 cyclical in a widening-credit-spread regime is a
   different bet than in a calm one. We assess names in a vacuum.
5. **Smart-money / alt-data confirmation is partly addressed.** ~~13F institutional flow,
   short interest, congressional/gov-contract activity, and attention proxies are all
   absent.~~ **Done (harness):** FINRA short interest (C1), gov-contract flow (USAspending),
   lobbying (Senate LDA), and WSB attention (ApeWisdom) are wired. **Still open:** 13F
   institutional flow (C3); congressional trades evaluated and **rejected as a scored
   signal** (`PREDICTIVE_SIGNALS_RESEARCH.md` → deferred/rejected).
6. **No news/event awareness.** ~~An 8-K, a 13D activist stake, or a tone collapse in the news
   can invalidate a fundamentals snapshot the day after we take it.~~ **Partially closed (harness):**
   8-K material events, SC 13D activist stakes, 13G passive stakes, and Form 144 planned insider
   sales are now detected from EDGAR's filing stream and surfaced as soft advisory flags (see A1
   events below). GDELT/news-tone sentiment remains open.

The sources below close these in roughly that priority order.

---

## 3. Proposed sources

Format for each: **what · why (investment rationale) · access/tier · pull · wire-in**.
"Wire-in" names the exact field/sub-score/gate to add so this isn't abstract.

### Tier A — free & authoritative (no key, validated in `scratch/`)

#### A1. SEC EDGAR XBRL — `companyfacts` / `frames` / `submissions`  ★ highest leverage

**Financials half ✅ DONE** — `EdgarSource` populates `Statements` directly from XBRL 10-K
filings (revenue, net income, operating cash flow, FCF, diluted EPS for the latest ~3 fiscal
years). Symbols with no XBRL financials (foreign Form 20-F issuers, recent spin-offs) degrade
gracefully to `None`. EDGAR is priority-1 in the harness merge so XBRL statements override
FMP's where present.

**Events half ✅ DONE** — `EdgarSource` now also emits an `events` section on `TickerSnapshot`
(`recent_8k` / `activist_13d` / `passive_13g` / `planned_insider_sale_144` boolean flags plus a
`recent` list of the matching filings). These are surfaced as **soft advisory flags** in
`ScoreCard.flags` — rendered in the screener table and in the structured `--json` `events` block
— and injected into the research brief for analyst context. They are enrichment signals only,
not a new sub-score or hard gate.

- **What:** `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` returns *every* standardized
  US-GAAP fact a company has ever reported; `frames` pulls one concept across all filers for
  a period (peer ranking); `submissions/CIK{cik}.json` is the full filing index.
- **Why:** This is the **ground truth** for fundamentals — point-in-time, as-reported, free,
  and immune to the per-symbol gating that makes FMP's free tier drop coverage (e.g. GEV).
  It lets us (a) recompute any ratio ourselves with full provenance, (b) build the
  earnings-quality and bankruptcy composites in Tier D, and (c) detect material events from
  the filing stream. In the live pull, AAPL's last 10 filings already surfaced a 10-Q, an 8-K,
  a 13G, and three Form 144s — all now captured.
- **Access:** free, keyless; requires a descriptive `User-Agent` with a contact email
  (SEC fair-access). ~10 req/s. We already set `SEC_IDENTITY`.
- **Pull:** validated — `scratch/raw/sec/companyfacts.json` (3.7 MB), `submissions.json`.
  Latest annual revenue extracted: **$416.2B (FY end 2025-09-27)**.
- **Still open from A1:** `frames` cross-filer peer ranking; Tier D composites
  (F/Z/M scores). (Point-in-time historical *consumption* for backtesting now
  exists — `providers/_xbrl_facts.py` + `backtest/xbrl.py` + `XbrlSignalSource`
  (`--source xbrl`), see ASSESSMENT_GAPS §2.1.)

#### A2. FRED (St. Louis Fed) — macro & credit-regime overlay  ✅ SHIPPED (display + advisory)
- **What:** the official FRED API `api.stlouisfed.org/fred/series/observations` (free key).
  Key series: `DGS10` (10y), `T10Y2Y` (2s10s curve), `BAMLH0A0HYM2` (**HY credit OAS**),
  `VIXCLS` (VIX), `FEDFUNDS`. (The keyless `fredgraph.csv` host is IP-blocked on the VPS.)
- **Why:** The single best free **risk-regime gauge**. High-yield OAS is the market's
  real-time price of corporate distress; the 2s10s curve is the canonical recession lead.
  This is *not* per-stock — it's a run-level overlay that should **tilt or gate the whole
  shortlist**: widen gates / down-weight leveraged & cyclical names when spreads blow out.
  This is exactly what the scaffolded `FredProvider` docstring already anticipates.
- **Access:** the keyless CSV graph host (`fred.stlouisfed.org/graph/fredgraph.csv`) is
  **IP-blocked on datacenter ranges** (it times out from the deploy VPS, same as Yahoo), so
  the overlay uses the **official FRED API** (`api.stlouisfed.org/fred/series/observations`)
  with a **free `FRED_API_KEY`** (instant signup at `fredaccount.stlouisfed.org/apikeys`).
  The API host is fast and unblocked; the key rides in the query string and is scrubbed by
  `redact_secrets` on any error.
- **Pull:** validated — latest: **HY OAS 2.72%, 2s10s +0.47, VIX 15.7, 10y 4.45%, FFR 3.64%**
  (a benign regime → no macro haircut today).
- **Shipped (display + advisory):** `data/macro.py:fetch_macro` builds a run-level `MacroContext`
  (official FRED API + `FRED_API_KEY`, day-cached under `.cache/fred/`, never-raises; returns
  `None` when unkeyed). Surfaced as the report
  `_MacroHeader` regime line and the soft `risk_off_regime` advisory flag (fires on
  leveraged/cyclical names in a risk-off regime; never affects composite/gates/ranking).
  `score(m, config, macro=None)` is byte-identical when `macro=None`. The scored
  `regime_multiplier` gate tilt remains future work — and note this flag is **not**
  XBRL-backtest-validatable (the backtest path passes no macro/SIC context).

#### A3. Yahoo Finance chart — own price history → real momentum & risk  ✅ DONE
- **Shipped** as the harness `YahooSource` (keyless `query1.finance.yahoo.com/v8/finance/chart`,
  day-cached under `.cache/yahoo/`). We compute 6m **relative strength vs SPY**, realized
  **volatility**, **max drawdown**, and the **200dma** ourselves — auditable and immune to
  FMP's per-symbol gating. It leads the harness merge priority for price fields and
  populates `Price.rel_strength_6m` / `realized_vol` / `max_drawdown` (the latter two are
  surfaced but not yet scored; a volatility risk gate is a tracked follow-up).
- The `snapshot_to_metrics` bridge (`data/bridge.py`) routes the harness snapshot into the
  scorer; see `HARNESS.md` for the walkthrough.

#### A4. Wikimedia pageviews — attention / demand proxy
- **What:** keyless `wikimedia.org/api/rest_v1/metrics/pageviews/per-article/...` — daily
  views of a company/product article.
- **Why:** A free, daily, **alternative-data attention proxy**. Rising pageviews for a
  consumer brand or product often lead retail demand and sentiment; fundamentals miss it
  entirely. Best as a *confirmation* signal, not a driver — weight low until validated.
- **Access:** free, keyless; descriptive `User-Agent` required.
- **Pull:** validated — "Apple Inc." page **+5.0%** recent-vs-prior attention trend.
- **Wire-in:** a low-weight `attention` input. Honest caveat: article-name → ticker mapping
  is fuzzy (disambiguation pages, product vs company); keep a curated ticker→article map.

#### A5. GDELT — global news volume & tone
- **What:** keyless `api.gdeltproject.org/api/v2/doc/doc?query=...&mode=tonechart` returns
  article tone distribution and volume across worldwide news.
- **Why:** Free, broad **news-sentiment & event-density** signal. A sudden tone collapse or
  volume spike flags that a fundamentals snapshot may be stale — a good companion to A1's
  8-K detection. Use for direction + divergence, not precision.
- **Access:** free, keyless, but **rate-limited** (we got a `429`; the puller retries with
  backoff). Don't hammer it in a universe run.
- **Pull:** validated — `scratch/raw/gdelt/apple_tonechart.json`.
- **Wire-in:** feed a `news_tone` / `news_volume_z` into the same low-weight sentiment input
  as A4, or surface as a research-layer note rather than a hard score.

### Tier B — free *tier* but key required (call out the key)

> These need a free signup key; all have a usable free quota. Load keys the same way as
> existing providers (env / `.env`, redacted on error).

#### B1. Alpha Vantage — forward EPS estimates, revisions & news sentiment  ★ fills the estimate gap
- **What:** `EARNINGS_ESTIMATES` / `EARNINGS` (history + estimates), `NEWS_SENTIMENT`
  (ticker-tagged sentiment), and economic-indicator endpoints.
- **Why:** Directly fills the two biggest momentum gaps: **forward EPS estimates** (a real
  PEG denominator) and **estimate-revision trend** (`eps_revision`, the cleanest momentum
  factor and currently unfilled). Its news-sentiment endpoint is cleaner to consume than GDELT.
- **Access:** **free key**, 25 requests/day (strict) — fine for a small watchlist, not a
  universe; cache aggressively. `export ALPHAVANTAGE_API_KEY=...`.
- **Wire-in:** populate `StockMetrics.eps_revision` and a new `eps_growth_fwd`; the momentum
  sub-score already references `eps_revision` (currently dead). New `AlphaVantageSource`.

#### B2. Finnhub & FMP — untapped *free* endpoints we already have keys for
- **What we don't yet call:** Finnhub `stock/earnings` (surprise history), `company-news`;
  FMP `earnings-surprises`, `analyst-estimates`, `earnings-calendar`.
- **Why:** Lowest-friction wins — no new vendor, keys already configured. **Earnings-surprise
  history** is a strong quality+momentum signal (consistent beats); the **earnings calendar**
  lets us avoid taking a snapshot right before a print.
- **Access:** already wired vendors; these specific endpoints are on the free tiers.
- **Pull:** validated via `scratch/pull_keyed_examples.py` — Finnhub `stock/earnings`
  (AAPL beat by **+1.1%** and **+4.2%** the last two quarters) and `company-news`
  (245 articles) both return on the free tier; FMP `earnings-calendar` works, while
  `earnings-surprises`/`analyst-estimates` returned 404/400 (the `/stable/` slugs differ
  from the legacy API — confirm the exact path before wiring, per the FMP gotchas in CLAUDE.md).
- **Wire-in:** add `earnings_surprise_streak` to `Fundamentals` (Finnhub `stock/earnings` is
  the validated source); add a `days_to_earnings` field so the scorer/research layer can flag
  event risk.

#### B3. Tiingo — fundamentals + news (free tier)
- **What:** EOD prices, fundamentals (statements/ratios), and a curated news feed.
- **Why:** A clean **redundancy/cross-check** source for FMP fundamentals (catch one vendor's
  bad field) and a decent free news feed. Free tier ~50 symbols/hr, 1000 req/day.
- **Access:** **free key**, `export TIINGO_API_KEY=...`.
- **Wire-in:** another `Source` in the harness; field-by-field merge means it transparently
  backfills gaps FMP/Finnhub leave (esp. for the symbols FMP gates).

### Tier C — differentiated / alternative data

#### C1. FINRA short interest — squeeze & skeptic signal  — **Shipped (harness)**
- **Status:** **Shipped (harness):** `FinraSource` → `ShortInterest` snapshot section →
  bridge (`snapshot_to_metrics`) → `crowded_short` soft flag.
- **What:** FINRA publishes consolidated short-interest (bi-monthly settlement) as a free,
  keyless bulk dataset covering NMS-listed securities.
- **Why:** **Short interest as % of shares outstanding** and **days-to-cover** are a direct
  read on bear positioning. High + rising short interest into improving fundamentals =
  squeeze candidate *or* a credible skeptic case worth respecting — either way it's signal
  the current stack is blind to.
- **Access:** free, keyless. The live, NMS-covering dataset is **`ConsolidatedShortInterest`**
  (POST `https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest`); the
  latest cycle is discovered via the
  `https://api.finra.org/partitions/group/otcMarket/name/ConsolidatedShortInterest` endpoint
  (`settlementDate` is a partition key). The older **`EquityShortInterest`** dataset is
  **frozen (last cycle 2022-09-15) and OTC-only — do not use it.**
- **Wire-in:** `ShortInterest` snapshot section; the bridge derives `short_pct_outstanding`,
  `days_to_cover`, `short_interest_rising`, `short_data_age_days` on `StockMetrics`. The `%`
  is of **shares-outstanding** (derived `market_cap / price`), labeled `short_pct_outstanding`
  — conservative vs. float. Feeds the `crowded_short` soft flag (advisory; never changes
  `composite` / `passed`).

#### C2. Quiver Quant — congressional trades, gov contracts, lobbying  (largely superseded)
- **Status (2026-07-01):** three of Quiver's four datasets have since shipped **keyless**:
  gov contracts (`GovContractsSource` → USAspending `spending_by_transaction`), lobbying
  (`LobbyingSource` → Senate LDA API), WSB mentions (`WsbSource` → ApeWisdom) — see
  `CLAUDE.md` for each. Quiver's only remaining net-new feed is **congressional trades**,
  and the copy-trade evidence is **contested, not positive**: the cited alpha is
  pre-STOCK-Act (Ziobrowski 2004/2011); on disclosed post-2012 trades the aggregate result
  is null-to-negative (Eggers-Hainmueller 2013; Belmont-Sacerdote et al. 2020). Full
  verdict: `PREDICTIVE_SIGNALS_RESEARCH.md` → "Deferred / rejected".
- **What:** `api.quiverquant.com/beta/` — congressional & senate trading, government
  contract awards, lobbying, WSB mentions.
- **Access:** **paid**, with a limited free tier for some endpoints; `QUIVER_API_KEY` already
  in `.env.example`. The raw congressional disclosures are free (House Clerk PTR / Senate
  eFD) but PDF/HTML-shaped — free-source feasibility unverified.
- **Wire-in (revised):** ~~`gov_contract_momentum` and `congress_net_buy` → a new low-weight
  sub-score in `scoring.py`~~ — off the table on the evidence above. If congressional trades
  are ever wired: a bot **discovery originator** on the FINRA short-interest pattern
  (contested prior, ships disabled, cluster-buys only, selection-ledger-measured), never a
  scored leg and never auto-execution.

#### C3. SEC 13F — institutional / smart-money ownership
- **What:** 13F-HR filings (from the same EDGAR pipeline as A1) aggregated per security:
  number of institutional holders, quarter-over-quarter change, concentration.
- **Why:** **Smart-money confirmation.** A name being *accumulated* by quality institutions
  (or a new activist 13D) is corroboration; broad institutional exit is a warning. Free,
  authoritative, and a natural extension of the EDGAR work in A1.
- **Access:** free via EDGAR (13F datasets / `data.sec.gov`).
- **Wire-in:** `inst_holder_change` on `StockMetrics`; pairs with C2 as an "ownership" view.

#### C4. Google Trends (pytrends) — search-demand proxy
- **What:** keyless (unofficial) Google Trends interest-over-time for brand/product terms.
- **Why:** Like A4 but often higher-signal for consumer/retail names — search interest leads
  same-store-sales surprises in several published studies. Confirmation, not driver.
- **Access:** keyless via `pytrends`, but **unofficial and easily rate-limited/blocked** —
  treat as best-effort. (Not pulled in `scratch/` for that reason; documented for honesty.)
- **Wire-in:** same low-weight `attention` input as A4/A5.

### Tier D — derived composites (no new vendor; compute from A1 + existing statements)

These are not feeds but **named, literature-backed signals** an analyst expects. All are
computable from the 5y `Statements` we already merge + the XBRL facts from A1.

#### D1. Piotroski F-Score (0–9) — fundamental momentum / quality — PARTIALLY SHIPPED (Core-6)
- **Why:** Nine binary tests of profitability, leverage, and efficiency trend. A high F-Score
  on a cheap stock is the classic "value that's actually improving" filter — separates value
  *traps* from value *opportunities*. Pairs perfectly with our `value` axis.
- **Shipped (Core-6, asset-free):** `piotroski_f` / `piotroski_f_legs` on `StockMetrics`
  (`stats.piotroski_f`), populated on the harness + the XBRL backtest panel, surfaced in
  JSON/CSV, and used (config-gated, OFF by default) to refine the `value_trap` flag — see
  `ASSESSMENT_GAPS.md` §2.2. We implement **6 of the 9** tests, using **revenue-normalized**
  trends (Δnet-margin, Δdebt/revenue, Δgross-margin) and profitability/cash/accrual **levels**
  (NI>0, OCF>0, OCF>NI) — deliberately **asset-free and equity-free** because total assets
  isn't extracted on either stack and equity denominators distort/darken on buyback-heavy
  firms. Standalone axis IC is currently ~0 on the survivorship-biased large-cap set (unfitted
  prior; see `...-xbrl-piotroski-results.md`).
- **Still open (full 9):** the omitted tests (Δcurrent-ratio, no-new-shares, Δasset-turnover,
  true ROA) need a total-assets concept + a shares-outstanding *series* added to BOTH the XBRL
  panel and harness `Statements`. Promote to a one-directional quality/value *tilt* only if a
  conditional, appropriate-universe backtest earns it — never before.

#### D2. Altman Z-Score — bankruptcy distance
- **Why:** A standard solvency early-warning. Cheaper than gating only on raw D/E because it
  blends working capital, retained earnings, EBIT, and market cap. A low Z is a hard red flag.
- **Wire-in:** `altman_z`; new gate `distress_risk` when Z < threshold (config-driven).

#### D3. Beneish M-Score & accruals ratio — earnings-quality / manipulation flag
- **Why:** The missing **"are the earnings real?"** check (gap #3). High accruals and an
  elevated M-Score are well-documented predictors of restatements and poor forward returns —
  the difference between a quality screen and a quality *illusion* screen.
- **Wire-in:** `accruals_ratio` / `beneish_m`; soft gate `earnings_quality_flag`.

---

## 4. How this lands in the architecture

The repo's two-layer split (see `CLAUDE.md`) tells you where each source goes:

- **Per-ticker fundamentals/price/insider/news** → a new `Source` module under
  `data/sources/`, registered in `_REGISTRY` (`data/sources/__init__.py`). The harness is the
  **only** production data layer — the legacy synchronous screener and its `Provider`s were
  retired, so do **not** add one there. Normalized into
  `TickerSnapshot` / `StockMetrics`, merged by priority. Add new fields to the dataclasses;
  unavailable fields stay `None` and the redistribution logic in `scoring.py` handles it.
- **Run-level macro** (A2 FRED — shipped) → `data/macro.py:fetch_macro` builds a `MacroContext`
  once per run (keyless, day-cached) and passes it into `scoring.score()` and `build_report()` as a
  display + advisory overlay — *not* a per-ticker provider. The scored `regime_multiplier` gate
  tilt is still future work.
- **Derived composites** (Tier D) → pure functions in `scoring.py` over `Statements`; no I/O.
- **Events/news** (A1 8-K/13D, A5 GDELT) → a new `events`/`news` section on `TickerSnapshot`,
  surfaced to the research layer and as soft flags.

Recommended sequencing (highest leverage first):

0. ✅ **Harness scoring bridge + A3 Yahoo price history** — *done.* `snapshot_to_metrics`
   makes the harness scoreable; the Yahoo source fills 6m rel-strength / vol / drawdown /
   200dma, keyless and gating-immune.
1. **Close the harness parity gaps** — add an annual `ratios` fetch to `FMPSource` for
   `pe_median_5y` (restores the 4th `value` leg) and a 5y `roic_5y_avg`; then the harness
   can fully replace the screener fetch path.
2. ✅ **A1 EDGAR XBRL + events** — **done.** Financials (10-K XBRL statements) and filing-stream
   events (8-K / SC 13D / 13G / Form 144 advisory flags) both shipped in `EdgarSource`.
   See A1 above.  ✅ **C1 short interest** — also shipped (`FinraSource` → `crowded_short` flag).
   Next alt-data additions per the events design decision record (§10): Tier D composites
   (F/Z/M scores), then B1 Alpha Vantage forward estimates.
3. ✅ **A2 FRED macro overlay** — **done.** Keyless `fetch_macro` builds a run-level `MacroContext`; threaded into `run_harness` + `build_report`; surfaces `_MacroHeader` + `risk_off_regime` advisory flag. Display + advisory only; scored `regime_multiplier` tilt is future work.
4. **D1–D3 composites** — pure analytical upgrade (F/Z/M scores); Altman/Beneish need A1's
   extra balance-sheet fields first.
5. **B1 Alpha Vantage** — forward estimates + revisions (free key) to light up `eps_revision`.
6. **C3 13F institutional ownership**, then **C2 Quiver** for the alt-data edge.

Every addition must respect two house rules from `CLAUDE.md`: route any error string that
could contain a URL through `env.py:redact_secrets()`, and keep coverage **honest** —
a thin source should lower `coverage()`, never silently zero a sub-score.

## 5. Reproduce the pulls

```bash
python3 scratch/pull_samples.py         # Tier A keyless sources → scratch/raw/ + scratch/derived/
python3 scratch/pull_keyed_examples.py  # Tier B/C keyed endpoints (uses .env keys; no-ops without)
```

`scratch/` is gitignored. Outputs are verbatim raw payloads (point-in-time audit) plus small
derived-signal JSONs the wiring code can target. The keyed script exercises the FMP/Finnhub
endpoints we already have keys for and leaves runnable, env-guarded stubs for Alpha Vantage
and Tiingo (add a free key and they light up).

## 6. Scale hardening — the caching layer (SHIPPED 2026-06)

**Status: shipped.** Implemented in `src/shortlist/cache.py` (this section is the
canonical design reference). This was the top scale-hardening item and the unblocker
for full-universe / sector-relative work (`ASSESSMENT_GAPS.md` §2.3, §4).

### As built
- **Module:** `src/shortlist/cache.py` — `HttpCache` (SQLite, default **rollback journal**,
  not WAL: the process is short-lived/ad-hoc and may sit on NFS), plus a `NoOpCache` and a
  configured **process-global singleton** (`configure_default_cache` / `get_default_cache`).
- **Store:** `.cache/http.sqlite` (gitignored). Persists between ad-hoc CLI invocations.
- **Scope:** FMP + Finnhub on **both** stacks (the four HTTP-JSON `_get` chokepoints).
  Yahoo/FINRA keep their own per-day / per-settlement disk caches; EDGAR (edgartools, free,
  uncapped) is intentionally uncached.
- **Key:** `(provider, endpoint, params)` SHA-256, secrets stripped by name (key rotation
  doesn't fragment the cache; no secret enters the store).
- **TTL by data half-life**, keyed on `(provider, path)`: quote 6h, fundamentals 1d,
  analyst 1d, statements 7d, profile 7d. Config-driven under `config.yaml: cache.ttl.<bucket>`.
- **Never cache soft failures:** a payload-level predicate skips empty `[]`/`{}`/`None` and
  `{"error": …}` bodies — FMP/Finnhub return 200-OK on gating/no-coverage, so
  `raise_for_status()` alone is insufficient. Errors that raise are never written.
- **On by default**; `--no-cache` disables, `--refresh-cache` bypasses reads and repopulates.
  `--demo` runs offline so the cache is disabled there.
- **Honesty rule honoured:** a cache hit returns the same parsed object as a live fetch, so
  `coverage()` cannot tell them apart. A corrupt/unopenable DB degrades to `NoOpCache` (never
  breaks a screen).

### Acceptance — met (see `tests/test_cache.py`)
- Re-running the same basket within TTL makes **zero** upstream calls for cached buckets
  (call-counting fakes assert this for the sync and async paths).
- `--refresh-cache` repopulates; TTL expiry triggers a single re-fetch; errors and empty
  soft-failures are never cached.

### Why it was needed (the symptom we kept hitting)
FMP's free plan allows ~250 calls/day and ~5/min. The screener spends ~8 calls/ticker (was 9
before the insider call was gated off), the harness ~13 — so a handful of names exhausts the
**daily** quota and a 5-ticker burst trips the **per-minute** throttle. Both surface as `429`s.
We already made the failure *honest and self-healing* (2025/2026 work: `FMPProvider._get` retries
429s with `Retry-After`-aware backoff; `fetch()` keeps partial legs; coverage reports a distinct
`rate_limited_429` status with a "budget exhausted, not gated" note) — but retry/backoff cannot
manufacture quota. The only thing that makes **repeated** runs cheap is not re-fetching what we
already pulled. That is caching — now shipped (see "As built" above).

### Sequencing note
This sits alongside the **FMP paid Starter tier (~$14–20/mo)** unlock: caching cuts *repeat* cost
to zero, the paid tier raises the *cold* ceiling and lifts per-symbol `402` gating. Do caching
first (it's free and benefits every source); add the paid tier when a true daily full-universe
run is the goal. See `ASSESSMENT_GAPS.md` §2.3 — sector-relative scoring depends on this landing.

## 7. Research-layer filing coverage — 10-K only; foreign-issuer 20-F deferred

The Claude research brief (`research/filings.py:fetch_bundle`) is built from **domestic-filer
documents only**: the latest **10-K**, the latest **10-Q** MD&A, and a YoY 10-K Item-1A risk
diff. Foreign private issuers (ADRs) file **Form 20-F** annually, not a 10-K, so `fetch_bundle`
returns `None` and `/deep` / `--research` report a skip (NVO/Novo Nordisk, ASML, TSM, SAP, …).

- **Skip message is ADR-aware (SHIPPED 2026-06).** `filings.no_10k_reason()` does a cheap 20-F
  *filings-index* lookup (no document download) and distinguishes a foreign issuer
  ("no 10-K — files Form 20-F (foreign issuer); research briefs currently cover 10-K filers only")
  from a name with no annual report at all ("no 10-K"). Best-effort and never-raise — falls back
  to the generic reason on any error. Wired into `research/_enrich_card` via an injectable
  `reason_fn` (hermetic in tests).

- **Full 20-F support is feasible but BLOCKED ON MEMORY, not the API.** edgartools exposes a
  first-class `TwentyF` object with the matching accessors (`risk_factors`,
  `management_discussion`, `business`, `operating_review`, `financials`, `key_information`), so a
  brief is structurally buildable. BUT actually extracting any narrative — `obj.risk_factors`, or
  even raw `filing.text()` / `.attachments` — **OOM-kills (exit 137)** on oracle-prod (1.9 GB RAM,
  ~690 MB free with the bot running). Novo's 20-F is a very large filing; the document parse
  materializes far more than the box can hold. The filings-*index* metadata lookup (what
  `no_10k_reason` uses) is cheap and safe — only the full-document parse blows up.

- **Wire-in (DEFERRED).** Add a `form="20-F"` fallback in `fetch_10k`/`fetch_bundle` that maps
  `TwentyF` sections onto `FilingText` — but **only behind memory-bounded / section-targeted
  extraction**: pull individual sections, cap chars *before* materializing the whole document, and
  measure peak RSS against the VPS budget before wiring it into the live bot. A naive port of the
  10-K path will crash `shortlist-bot`. (10-K parsing currently fits; 20-F as-built does not.)
