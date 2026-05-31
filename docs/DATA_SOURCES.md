# Data sources for stock assessment — current state and a roadmap

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
| **SEC EDGAR** (Form 4) | free (≤10 req/s) | both | authoritative insider transaction flow |
| **Mock** | offline | harness | demo fixtures |
| Quiver / FRED | scaffolded, not wired | screener | congress/gov-contracts; macro overlay |

Scored signals today: **quality** (ROE, net margin, interest coverage, D/E),
**moat** (gross margin, margin stability, ROIC), **momentum** (price vs 200dma, 6m rel
strength, EPS revision), **value** (upside-to-target, FCF yield, PE vs own history, PEG),
**insider** (sentiment + net-flow/mktcap).

## 2. Gaps a professional would flag

These are the holes that matter, ranked by how much they distort an assessment:

1. **Momentum is half-populated.** `rel_strength_6m` and `eps_revision` are *defined in
   the model but nothing fills them* — momentum currently leans on a single vendor's
   precomputed `price-change`. No volatility, no drawdown, no beta. We can't even compute
   relative strength vs a benchmark.
2. **No forward estimates / revisions breadth.** Everything is trailing (TTM). PEG needs a
   growth rate; `eps_revision` needs an estimate trend. Analyst *target* ≠ analyst
   *estimate revisions*, which is the cleaner momentum signal.
3. **No earnings-quality red-flag.** Accruals, restatements, and going-concern language are
   how you avoid value traps. We score profitability but never ask if the earnings are *real*.
4. **No macro/risk regime.** A 9/10 cyclical in a widening-credit-spread regime is a
   different bet than in a calm one. We assess names in a vacuum.
5. **No smart-money or alt-data confirmation.** 13F institutional flow, short interest,
   congressional/gov-contract activity, and attention proxies are all absent — the
   differentiated layer where edge actually lives.
6. **No news/event awareness.** An 8-K, a 13D activist stake, or a tone collapse in the news
   can invalidate a fundamentals snapshot the day after we take it.

The sources below close these in roughly that priority order.

---

## 3. Proposed sources

Format for each: **what · why (investment rationale) · access/tier · pull · wire-in**.
"Wire-in" names the exact field/sub-score/gate to add so this isn't abstract.

### Tier A — free & authoritative (no key, validated in `scratch/`)

#### A1. SEC EDGAR XBRL — `companyfacts` / `frames` / `submissions`  ★ highest leverage
- **What:** `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` returns *every* standardized
  US-GAAP fact a company has ever reported; `frames` pulls one concept across all filers for
  a period (peer ranking); `submissions/CIK{cik}.json` is the full filing index.
- **Why:** This is the **ground truth** for fundamentals — point-in-time, as-reported, free,
  and immune to the per-symbol gating that makes FMP's free tier drop coverage (e.g. GEV).
  It lets us (a) recompute any ratio ourselves with full provenance, (b) build the
  earnings-quality and bankruptcy composites in Tier D, and (c) detect material events from
  the filing stream: **8-K** (material event), **SCHEDULE 13D** (activist), **13G** (passive
  5%), **Form 144** (planned insider sale). In the live pull, AAPL's last 10 filings already
  surfaced a 10-Q, an 8-K, a 13G, and three Form 144s — all event signals we ignore today.
- **Access:** free, keyless; requires a descriptive `User-Agent` with a contact email
  (SEC fair-access). ~10 req/s. We already set `SEC_IDENTITY`.
- **Pull:** validated — `scratch/raw/sec/companyfacts.json` (3.7 MB), `submissions.json`.
  Latest annual revenue extracted: **$416.2B (FY end 2025-09-27)**.
- **Wire-in:** new harness `Source` (`EdgarFactsSource`) populating `Statements` directly
  from XBRL (no vendor dependency) and a new `events: list[FilingEvent]` section on
  `TickerSnapshot`. Add a `recent_8k` / `activist_13d` flag the scorer can read. EDGAR is
  already priority-1 in the merge, so XBRL statements would override FMP's where present.

#### A2. FRED (St. Louis Fed) — macro & credit-regime overlay
- **What:** keyless CSV at `fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}`. Key series:
  `DGS10` (10y), `T10Y2Y` (2s10s curve), `BAMLH0A0HYM2` (**HY credit OAS**), `VIXCLS` (VIX),
  `FEDFUNDS`.
- **Why:** The single best free **risk-regime gauge**. High-yield OAS is the market's
  real-time price of corporate distress; the 2s10s curve is the canonical recession lead.
  This is *not* per-stock — it's a run-level overlay that should **tilt or gate the whole
  shortlist**: widen gates / down-weight leveraged & cyclical names when spreads blow out.
  This is exactly what the scaffolded `FredProvider` docstring already anticipates.
- **Access:** free, keyless via the CSV endpoint (the JSON API needs a free key; CSV doesn't).
- **Pull:** validated — latest: **HY OAS 2.72%, 2s10s +0.47, VIX 15.7, 10y 4.45%, FFR 3.64%**
  (a benign regime → no macro haircut today).
- **Wire-in:** a `MacroContext` object built once per run (not per ticker), passed into
  `scoring.score()`. Concretely: a `regime_multiplier` on the leverage gate, and a small
  penalty to `value`/cyclicals when `hy_oas` exceeds a configurable threshold in `config.yaml`.

#### A3. Yahoo Finance chart — own price history → real momentum & risk
- **What:** keyless `query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d`
  returns raw OHLCV. (Stooq's free CSV now requires a captcha-gated key — Yahoo is the
  better keyless option.)
- **Why:** Lets us compute momentum/risk **ourselves, auditable**, instead of trusting a
  vendor's opaque precomputed field — and crucially fills the momentum gaps: 6m **relative
  strength vs SPY**, realized **volatility**, **max drawdown**, distance to 200dma. These are
  table-stakes momentum/risk inputs the model defines but can't currently populate.
- **Access:** free, keyless (send a browser `User-Agent`). Be polite; cache by day.
- **Pull:** validated — AAPL: **6m rel-strength vs SPY +0.6%, annualized vol 22.1%,
  1y max drawdown −13.8%, +18.6% above 200dma**.
- **Wire-in:** populate `Price.rel_strength_6m` (currently always `None`) and add
  `realized_vol` / `max_drawdown` to `Price`; feed `rel_strength_6m` into the existing
  momentum sub-score. Add an optional `volatility` input to a risk gate.

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

#### C1. FINRA + Nasdaq short interest — squeeze & skeptic signal
- **What:** FINRA publishes consolidated short-interest (bi-monthly settlement) as free bulk
  files; Nasdaq/exchanges publish per-symbol short interest & days-to-cover.
- **Why:** **Short interest as % of float** and **days-to-cover** are a direct read on
  bear positioning. High + rising short interest into improving fundamentals = squeeze
  candidate *or* a credible skeptic case worth respecting — either way it's signal the
  current stack is blind to.
- **Access:** free (FINRA bulk download); Finnhub also exposes it (premium on free tier).
- **Wire-in:** `short_pct_float` / `days_to_cover` on `StockMetrics`; either a low-weight
  input or a soft gate ("crowded short — investigate").

#### C2. Quiver Quant — congressional trades, gov contracts, lobbying  (already scaffolded)
- **What:** `api.quiverquant.com/beta/` — congressional & senate trading, **government
  contract awards**, lobbying, WSB mentions.
- **Why:** The scaffold's rationale stands: **gov-contract flow is directly material to
  defense/industrial names (LMT, GEV)** and is captured by no fundamentals feed — this is
  genuine edge. Congressional-trade clustering is a softer but real sentiment signal.
- **Access:** **paid**, with a limited free tier for some endpoints; `QUIVER_API_KEY` already
  in `.env.example`. (Flagged honestly: the richest endpoints are paid.)
- **Wire-in:** the scaffold already specifies `gov_contract_momentum` and `congress_net_buy`
  → a new low-weight sub-score in `scoring.py`, registered via `--provider quiver`.

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

#### D1. Piotroski F-Score (0–9) — fundamental momentum / quality
- **Why:** Nine binary tests of profitability, leverage, and efficiency trend. A high F-Score
  on a cheap stock is the classic "value that's actually improving" filter — separates value
  *traps* from value *opportunities*. Pairs perfectly with our `value` axis.
- **Wire-in:** `piotroski_f` on `StockMetrics`, computed in `scoring.py` from `Statements`;
  fold into the quality sub-score or surface alongside value.

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

- **Per-ticker fundamentals/price/insider/news** → a new `Source` in `data/sources.py`
  (harness) and/or a `Provider` in `providers/` (screener), normalized into
  `TickerSnapshot` / `StockMetrics`, merged by priority. Add new fields to the dataclasses;
  unavailable fields stay `None` and the redistribution logic in `scoring.py` handles it.
- **Run-level macro** (A2 FRED) → a `MacroContext` built once and passed into
  `scoring.score()` as a tilt/gate multiplier — *not* a per-ticker provider. The scaffolded
  `FredProvider` docstring already says exactly this.
- **Derived composites** (Tier D) → pure functions in `scoring.py` over `Statements`; no I/O.
- **Events/news** (A1 8-K/13D, A5 GDELT) → a new `events`/`news` section on `TickerSnapshot`,
  surfaced to the research layer and as soft flags.

Recommended sequencing (highest leverage first):

1. **A3 Yahoo price history** — fills the momentum gap immediately, keyless, ~1 file.
2. **A1 EDGAR XBRL + events** — authoritative financials + event flags; biggest single win.
3. **A2 FRED macro overlay** — cheap, keyless, makes every score regime-aware.
4. **D1–D3 composites** — no new I/O, pure analytical upgrade (F/Z/M scores).
5. **B1 Alpha Vantage** — forward estimates + revisions (free key) to light up `eps_revision`.
6. **C1/C3 short interest + 13F**, then **C2 Quiver** for the alt-data edge.

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
