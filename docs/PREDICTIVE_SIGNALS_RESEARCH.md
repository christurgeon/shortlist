# Predictive-Signal Research: 5 Free-Data Additions to shortlist

**Status:** research / proposal only — *no code in this PR.* Each idea below is a
candidate to **spec + implement later**, and every one is designed to first ride
as a **measurement-only axis** in the existing `--source xbrl` / snapshot-replay
backtest (the same discipline already used for `share_count`,
`net_debt_to_ebitda`, and `ebit_ev_yield`) so its rank IC and collinearity vs the
axes we already ship can be checked **before** any production wiring. See
`backtest/signals.py:96` (`XbrlSignalSource`) and `backtest/cli.py:28`
(`_COLLINEARITY_PAIRS`).

**Date:** 2026-06-21. **Method:** parallel literature + free-data-availability
sweep across five signal families (accounting anomalies, analyst/earnings
dynamics, price/technical, alternative free data, value/quality composites),
grounded against the axes shortlist already scores.

## Goal & selection criteria

The brief: find ways to better predict (a) whether a stock is **undervalued** and
(b) whether it is **likely to rise** — using **free** data sources only. Each
candidate was scored on four axes:

1. **Evidence** — peer-reviewed, replicated, with a real effect size (not a blog backtest).
2. **Free-data feasibility** — a *confirmed* free/keyless source, point-in-time backtestable. Many "free" estimate feeds are paywalled; we flag those honestly.
3. **Orthogonality** — adds information the current composite does **not** already have. shortlist is rich (quality / moat / growth / value / momentum / insider / risk + Piotroski, dilution, news, WSB, gov-contracts, lobbying, short interest, FRED macro, reverse-DCF). A signal that just re-expresses FCF yield or raw 12-1 momentum is rejected.
4. **Fit** — leverages infrastructure we already have (XBRL extraction, the backtest harness, the 10-K/10-Q research bundle, the daily accumulator).

### What shortlist already has (so these don't duplicate it)

Quality (gross margin / ROIC / net margin), moat proxies, growth (rev/EPS CAGR),
value (FCF yield, PE-vs-history, PEG, analyst-target upside), **raw 12-1
momentum** (`scoring.py:58`), **realized-vol + drawdown risk** (`scoring.py:117`),
insider Form-4 flows, Piotroski F (`scoring.py:135`, off by default),
share-count/dilution, **EV/EBIT yield (measured — ~0.73 corr with FCF yield, so
deliberately not shipped)**, earnings-surprise *history as a research line only*,
news-flow volume, WSB social hype, FINRA short interest, USAspending
gov-contracts, Senate-LDA lobbying, FRED macro overlay, reverse-DCF research line.

---

## The 5 recommendations (ranked)

| # | Signal | Family | Free? | Effort | Why it's new |
|---|--------|--------|-------|--------|--------------|
| 1 | **Standardized earnings surprise + PEAD drift (SUE)** | Earnings dynamics | ✅ data already fetched | Low–Med | We fetch the surprises but only *display* them — never score the drift |
| 2 | **Residual (idiosyncratic) momentum** | Price/return | ✅ keyless OHLCV | Med | Strictly dominates the raw-momentum leg we ship; ~2× Sharpe, far fewer crashes |
| 3 | **Investment & earnings-quality fundamentals (asset growth + accruals)** | Accounting | ✅ SEC XBRL | Low | Two of the most robust anomalies in finance; currently absent entirely |
| 4 | **"Lazy Prices" filing-text-change signal** | Alt-data / text | ✅ SEC EDGAR | Med | Extends the riskdiff infra we already have on 10-K/10-Q |
| 5 | **Shareholder yield (total payout)** | Value/quality | ✅ SEC XBRL | Low | Adds the buyback + net-debt-paydown legs FCF yield misses |

Plus two **near-zero-cost scoring fixes** surfaced by the research (see
"Quick wins" at the end): the existing `upside_to_target` leg is likely
*wrong-signed*, and we already fetch — then discard — the analyst-recommendation
history needed for a rating-change signal.

---

## 1. Standardized earnings surprise (SUE) + post-earnings-announcement drift

> **Status: IMPLEMENTED (task-001).** Folded into `momentum_score` as an opt-in,
> **OFF-by-default** STRAIGHT leg (the `momentum.sue` config block, byte-identical when
> absent — the `quality.dilution` / `value.shareholder_yield` precedent). `SUE =
> last_surprise_pct / dispersion`, **time-decayed** over ~60 trading days since the last
> announcement. Two prerequisite bridge derivations were added: **`earnings_surprise_dispersion`**
> (`stats.surprise_dispersion`, the SUE denominator — the bridge previously collapsed the
> surprise list to a mean only) and **`earnings_days_since_last_report`** (the decay anchor).
> **Announcement-date APPROXIMATION:** Finnhub `stock/earnings` carries only the fiscal
> quarter-END, so `_earnings` derives `last_report_date` from the most recent PAST
> `calendar/earnings` entry with an `epsActual`, falling back to the quarter-end (over-states
> staleness) — documented in code + `CLAUDE.md`. **Mandatory σ-guard:** abstains (None) on
> dispersion None/below floor (the all-equal / 4-equal σ≈0 case) or <~3 quarters — never
> divides by ~0. Decay is keyed off the PAST report, NEVER days-to-next (look-ahead).
> **Measurement gating (deferred, honest):** SUE is NOT a live-price backtest axis (price-only
> snapshots carry no earnings; surprises aren't in companyfacts), so `scoring.sue_score` + the
> `sue~momentum` collinearity pair ride ONLY the guarded **snapshot-replay** path
> (`SnapshotSignalSource` emits a `sue` axis) and no-op until accumulation exists — no live-price
> SUE axis was fabricated. See `CLAUDE.md` → "SUE / post-earnings-announcement-drift leg".

**The idea.** Stocks that beat earnings keep drifting *up* for weeks; stocks that
miss keep drifting *down*. The magnitude that matters is the surprise
**standardized** by its own dispersion (SUE), not the raw percentage. shortlist
**already fetches** the inputs (Finnhub `stock/earnings` actual-vs-estimate, free,
deep history; `calendar/earnings` for the next date) and surfaces them only as a
*research context line* — the drift is **never scored**. This is the single
biggest "data already in hand, alpha left on the floor" gap.

**Formula.** `SUE = (actual_EPS − consensus_EPS) / σ`, where σ is either the
trailing std-dev of the firm's own surprises (we have `earnings_quarters`) or the
cross-sectional surprise dispersion. Decay the leg over the ~60-trading-day
post-announcement window, keyed off days-since-last-report (we already derive
`earnings_days_to_next`).

**Free-data feasibility.** ✅ **Fully free and point-in-time.** Inputs are already
on `StockMetrics` (`earnings_last_surprise_pct`, `earnings_avg_surprise_pct`,
`earnings_quarters`); the drift is measured on the keyless Yahoo OHLCV that already
leads our price merge. Backtestable today as an `Observation(as_of, ticker, {sue:
subscore})` on live prices — exactly the momentum-axis pattern.

**Evidence.**
- Ball & Brown (1968) — founding result; earnings information is impounded with a lag.
- **Bernard & Thomas (1989, 1990)**, *Journal of Accounting & Economics* — top-minus-bottom SUE decile drifts **~18% annualized** over the 60 days post-announcement; positive in 41 of 48 quarters.
- Chan, Jegadeesh & Lakonishok (1996), *J. Finance* — SUE portfolios ≈ **+7.5% / 6 months**, and earnings surprise predicts drift *after controlling for* price momentum (the two are not redundant).
- **Novy-Marx (2015)**, *"Fundamentally, Momentum is Fundamental Momentum"* — earnings-surprise measures **subsume price momentum** in cross-sectional regressions.

**Orthogonality.** High — currently unscored, and it is the *fundamental* signal
that price momentum only weakly proxies. Complements the raw-momentum and
news-flow axes; pairs naturally with the existing earnings research line.

**Caveats.** Drift decays fast and is strongest right after the print, so the leg
must be time-decayed (a stale beat is not a signal). Finnhub free tier
occasionally lacks the estimate; effect concentrates in smaller, less-covered
names (McLean–Pontiff post-publication decay applies). A close cousin —
**earnings-announcement premium** (Frazzini–Lamont 2007 ~+7–18%/yr; Savor–Wilson
2016 ~+9.9%/yr for firms scheduled to report) — needs only the scheduled date we
already have and could ride as an advisory "reports within N days" flag.

---

## 2. Residual (idiosyncratic) momentum

> **Status: IMPLEMENTED (task-004).** Folded into `momentum_score` as an opt-in,
> **OFF-by-default** STRAIGHT leg (the `momentum.residual` config block, byte-identical when
> absent — the `momentum.sue` precedent). The real work was **price plumbing**: the live
> merge discards dates (scalars like `rel_strength_6m`), so `PriceHistory.through(d)` (dated
> truncation), a `snapshot_from_closes_dated` seam, and a `Price.residual_momentum` field were
> added (carried to `StockMetrics` by the bridge). The signal **DATE-INNER-JOINS** the stock
> and SPY closes on shared dates (`stats.join_on_dates`) BEFORE computing returns — position-
> pairing `closes[i]` vs `spy_closes[i]` garbles beta under different listing dates / halts /
> lengths. `stats.residual_momentum` then estimates the CAPM beta **point-in-time** (stdlib OLS,
> no numpy) on the truncated window, takes residuals over **t-12..t-2 (the 12-1 skip preserved)**,
> and standardizes by `sd(resid)`. **Vol-guard:** `sd==0` / flat-market / too-few-points → None
> (never divide by 0). UNLIKE SUE, it IS price-reconstructable, so it rides the **LIVE-price**
> `MomentumSignalSource` as a real `residual_momentum` axis (backtest-only `scoring.residual_
> momentum_score` + the `residual_momentum~momentum` collinearity pair). **Honest caveat:** some
> recent replications report residual momentum UNDERPERFORMING, so it ships OFF as a measured
> candidate, NOT a replacement for raw momentum. See `CLAUDE.md` → "Residual (idiosyncratic)
> momentum leg".

**The idea.** Our momentum leg (`scoring.py:58`,
`avg(price_vs_200dma, rel_strength_6m, eps_revision)`) is **raw total-return**
trend — the version that crashes hardest at turning points. Residual momentum
strips out the factor-beta exposure first: regress the stock's returns on the
market (and optionally size/value), then rank on the **residual** trend. It is the
same anomaly, de-risked.

**Formula.** Rolling regression of monthly excess returns on a market proxy
(CAPM) — we already fetch an index series for `rel_strength`. Take residuals over
the 12-1 window (skip the most recent month), standardize by residual vol:
`iMOM = mean(resid_{t-12..t-2}) / sd(resid)`. CAPM version is **OHLCV-only**; an
FF3 version would need Ken French's free SMB/HML files (an extra, non-PiT-trivial
input).

**Free-data feasibility.** ✅ Keyless — computed entirely from the daily OHLCV we
already hold. Medium effort (rolling OLS + residual-vol scaling).

**Evidence.**
- **Blitz, Huij & Martens (2011)**, *Journal of Empirical Finance* 18(3):506–521 — residual momentum earns roughly **2× the risk-adjusted profit** of total-return momentum, with a **Sharpe ~double**, mostly from lower variance; robust out-of-sample and globally.
- Gutierrez & Pirinsky (2007), *J. Financial Markets* — idiosyncratic-return momentum **persists for years** while raw relative-return momentum **reverses**.
- Honest caveat: some recent replications report residual momentum underperforming in specific samples — so **measure its rank IC and its correlation vs our existing momentum axis** (watch the `_COLLINEARITY_PAIRS` ≳0.5 threshold) before wiring.

**Orthogonality.** This *is* momentum, so by construction it correlates with the
leg we ship — the point is that it **dominates** it (higher Sharpe, fewer
crashes). Likely a replacement/augmentation of `rel_strength_6m`, not a 7th axis.

**Bundle (related price-signal refinements, all OHLCV-trivial):**
- **52-week-high proximity** (George & Hwang 2004, *J. Finance* 59(5)) — `close / max(high, 252d)`. Empirically *beats* raw 12-1 return and, crucially, **does not reverse long-term**. One `max()`; check corr vs `price_vs_200dma`.
- **MAX effect** (Bali, Cakici & Whitelaw 2011, *JFE* 99(2)) — invert `max(daily return, last 21d)`. Lottery/blow-off names underperform (4-factor alpha ~1.18%/mo) and MAX **subsumes the idiosyncratic-vol puzzle**. A great defensive flag that pairs with WSB-hype / crowded-short.
- **Vol-scaled (risk-managed) momentum** (Barroso & Santa-Clara 2015, *JFE* 116(1)) — weight momentum by `σ_target/σ_realized`; **Sharpe 0.97 vs 0.53**, nearly eliminates crashes. A near-free interaction of the two axes we already compute (momentum × `realized_vol`).

---

## 3. Investment & earnings-quality fundamentals (asset growth + accruals)

> **Status: IMPLEMENTED (task-002).** Both ride as standalone measurement-only
> backtest axes (`asset_growth`, `accruals` in `XbrlSignalSource._AXES`, with the
> `accruals~piotroski` / `asset_growth~growth` collinearity pairs) AND as opt-in,
> **OFF-by-default** inverted legs in `quality_score` (the `quality.earnings_quality`
> config block, byte-identical when absent). Extraction is on both paths
> (`providers/_edgar_facts.py` standard_concept "Assets"; `providers/_xbrl_facts.py`
> raw us-gaap `Assets`); shared math is `stats.asset_growth`/`stats.accruals` (a
> consecutive ~1yr fiscal-end guard drops gap-spanning ratios). Masked for
> financials/REITs on the production path; the backtest axis stays unmasked. See
> `CLAUDE.md` → "quality.earnings_quality".

**The idea.** Two of the most-replicated cross-sectional anomalies, both **absent
from shortlist** and both pure SEC-XBRL (the path the backtest already validates
point-in-time). They answer "is this 'cheap' name cheap for a *good* reason?" —
the core undervaluation question.

**3a. Asset-growth anomaly.** Firms that aggressively grow total assets
subsequently **underperform**. `asset_growth = (TotalAssets_t / TotalAssets_{t-1}) − 1`,
used as a **negative** predictor.
- **Cooper, Gulen & Schill (2008)**, *J. Finance* 63(4) — a value-weighted low-minus-high asset-growth spread of **~8%/yr** (far larger equal-weighted); survives size, B/M, and momentum controls. One of the most robust anomalies known.
- Free input: a single XBRL concept (`Assets`). Trivial to compute; trivial to backtest.

**3b. Accruals anomaly (earnings quality).** Earnings backed by cash persist;
earnings inflated by accruals reverse. `accruals = (ΔNetWorkingCapital −
Depreciation)`, or the cash-flow form `(NetIncome − CFO) / TotalAssets`. High
accruals → **low** future returns.
- **Sloan (1996)**, *The Accounting Review* — a hedge on accruals earned **~10%/yr** abnormal returns; the market "fixates" on earnings and ignores the cash/accrual split.
- Free input: `NetIncomeLoss`, `NetCashProvidedByUsedInOperatingActivities`, `Assets` — all XBRL concepts we already extract.
- **Note on overlap:** Piotroski's F-score (already in shortlist, off by default) includes a *binary* CFO>NI leg, so a continuous accruals axis partly overlaps it — measure the correlation. Asset growth has **no** overlap with anything we ship and is the higher-priority of the two.

**Free-data feasibility.** ✅ Pure SEC XBRL, point-in-time, keyless. Low effort —
both slot into `XbrlSignalSource` as standalone measurement-only axes alongside
the `net_debt_to_ebitda` / `share_count` axes already there.

**Orthogonality.** High. These are *investment/quality* signals independent of the
valuation multiple. A name can look cheap on FCF yield yet be a value trap because
it is ballooning assets or booking soft accruals — exactly the discrimination the
current composite lacks.

**Caveats.** Asset growth conflates organic capex, M&A, and working-capital swings
(by design — all three predict). Both are quarterly/annual, slow-moving signals;
guard reverse-splits / restatements. Sector-mask financials/REITs as we do for the
other balance-sheet legs. Companion to consider: **Beneish M-score** (manipulation
flag) and **Altman Z-score** (distress) as soft red-flag flags, both XBRL-derivable.

---

## 4. "Lazy Prices" — year-over-year filing-text-change signal

> **Status: IMPLEMENTED (task-005) — ADVISORY FLAG ONLY, research/PiT slice.** Shipped
> the lowest-risk wiring: a config-gated soft **`filing_text_change`** flag (the
> `social_hype`/`news_spike` precedent), **NOT** a scored leg — byte-identical when the
> `flags.filing_text_change` block is absent, never affects `passed`/`composite`/`scored`.
> A NEW stdlib similarity scorer (`research/textsim.py`, bag-of-words **cosine** via
> `collections.Counter`, **no new dependency**) measures YoY similarity over the
> normalized Item-1A + MD&A token bag; it reuses riskdiff's normalization intent
> (lowercase, strip digits/currency/punctuation, collapse whitespace) so boilerplate /
> number / caption-renumber churn does NOT depress the score. LOW similarity (big YoY
> change) trips the flag (`max_similarity` default 0.7). A **point-in-time** filing
> accessor (`research/filings.py:filing_text_change`) compares the current same-type
> filing vs the **immediately-prior** one restricted to acceptance date ≤ `as_of` — the
> look-ahead guard for any replay (it can NEVER compare a historical date against a future
> filing; `fetch_bundle`'s live "latest" is never used in replay). The metric rides on
> `StockMetrics.filing_text_similarity` and surfaces in `--json`. **DEFERRED (honest):**
> full filing-text fetch is NOT bolted onto the per-ticker harness `EdgarSource` (it fetches
> Form 4 + financials + filing-index only — full text lives in the research layer), and the
> snapshot-replay backtest axis is deferred (text isn't in companyfacts and persisting it
> into accumulated snapshots is a separate heavy build). The PiT accessor is built + tested
> so that future accumulation/backtest wiring is correct-by-construction. See `CLAUDE.md` →
> "Lazy-Prices filing-text-change flag".

**The idea.** When a company **changes the language** of its 10-K/10-Q
year-over-year (especially risk factors and MD&A), it is usually hiding bad news —
and the stock subsequently **underperforms**. shortlist is uniquely positioned to
ship this: we **already fetch** the latest 10-K, the latest 10-Q MD&A, and a
YoY risk-factor diff (`research/riskdiff.py`) for the Claude brief. Quantifying
that diff into a *score* is a natural, infrastructure-reusing extension.

**Formula.** Cosine / Jaccard similarity between consecutive same-type filings
(full document, or section-wise on Item 1A risk factors + MD&A). Low similarity
(big change) → negative signal. Optionally layer Loughran–McDonald finance-tuned
sentiment on the *changed* text.

**Free-data feasibility.** ✅ SEC EDGAR full-text, keyless, uncapped (we already
pull these documents). Medium effort — we have the diff machinery; this adds a
similarity metric + scoring, not a new data source.

**Evidence.**
- **Cohen, Malloy & Nguyen (2020)**, *"Lazy Prices," J. Finance* 75(3) — a portfolio shorting firms that *changed* their filings and buying those that didn't earned **~30–60 bps/month** (≈4–7%/yr) risk-adjusted, with the drift concentrated in the *risk-factor* and MD&A sections; changes predicted future negative events (earnings, downgrades, even bankruptcies).
- Loughran & McDonald (2011), *J. Finance* — domain-specific (not generic) sentiment dictionaries are what actually predict on 10-K text.

**Orthogonality.** High and very on-brand — it turns a qualitative artifact we
already generate (the risk-factor diff in the research brief) into a quantitative,
backtestable score, and it is independent of price, valuation, and earnings.

**Caveats.** Boilerplate / template churn creates false positives — normalize
aggressively (we already prefix-normalize in `riskdiff.py`). Needs ≥2 years of a
given filing type per name; English-language 10-K/10-Q only (foreign 20-F issuers
are already excluded from the research path). Backtesting it is snapshot-replay
(text isn't in companyfacts), so it activates on the guarded accumulation path —
but the *live scoring* leg can ship independently of the backtest.

---

## 5. Shareholder yield (total payout)

> **Status: IMPLEMENTED (task-003).** Rides as a standalone measurement-only backtest
> axis (`shareholder_yield` in `XbrlSignalSource._AXES`, with the
> `shareholder_yield~value_fcf_yield` and `shareholder_yield~share_count` collinearity
> pairs — the buyback leg is the dollar-twin of dilution) AND as an opt-in,
> **OFF-by-default** *straight* (non-inverted) leg in `value_score` (the
> `value.shareholder_yield` config block, byte-identical when absent). The four financing
> legs are net-new XBRL extraction on both paths via concept FAMILIES
> (`providers/_edgar_facts.py` reads the raw us-gaap `concept` column — `standard_concept`
> mislabels them — excluding dimensional breakdowns; `providers/_xbrl_facts.py` `sum_family`).
> Shared math is `stats.shareholder_yield` (abs()-normalizes each leg, so it agrees across
> the two source sign conventions; net-debt leg = repayments − issuance, sign preserved, a
> net issuer scores negative). Masked for financials on the production path; the backtest
> axis stays unmasked. See `CLAUDE.md` → "value.shareholder_yield".

**The idea.** FCF yield measures cash *generated*; shareholder yield measures cash
*returned* — dividends **plus net buybacks plus net debt paydown**. The buyback
and debt-reduction legs are exactly what FCF yield misses, and total payout is a
cleaner "management is compounding for owners" signal than dividend yield alone.

**Formula.** `shareholder_yield = (dividends_paid + net_share_repurchases +
net_debt_reduction) / market_cap`, all from the cash-flow / financing section.

**Free-data feasibility.** ✅ SEC XBRL cash-flow statement (`PaymentsOfDividends`,
`PaymentsForRepurchaseOfCommonStock`, `RepaymentsOfDebt` net of issuance) ÷
market cap (already on `StockMetrics`). Low effort.

**Evidence.**
- **Boudoukh, Michaely, Richardson & Roberts (2007)**, *J. Finance* 62(2) — **total payout yield** (dividends + repurchases) dominates dividend yield for predicting returns, because buybacks have replaced dividends as the marginal payout channel.
- Practitioner work (e.g., Meb Faber's shareholder-yield studies) extends it to include net debt paydown and finds the three-component yield outperforms each leg alone.

**Orthogonality.** Partial-to-high. The **net-buyback** component is *related* to
our existing share-count/dilution flag (buybacks shrink share count) but is
dollar-scaled to price rather than a share-count CAGR; the **net-debt-paydown**
component is genuinely new. Expect moderate correlation with FCF yield — so this is
a measure-then-decide candidate: emit it as a value-attribution axis and check the
collinearity, just as we did for `ebit_ev_yield`.

**Caveats.** Buyback *announcements* ≠ executed repurchases (use cash-flow actuals,
not press releases). Debt paydown can signal either discipline or distress-driven
deleveraging — combine with the leverage gate. Sector-mask financials.

---

## Quick wins (near-zero-cost scoring fixes the research surfaced)

These aren't new signals — they're corrections/activations of data we already
have, worth a backtest before the bigger builds:

1. **`upside_to_target` is likely wrong-signed.** Our value leg uses the *level*
   of analyst-target implied upside (`target/price − 1`). **Brav & Lehavy (2003),
   *J. Finance*** show the *level* of implied upside is **negatively** related to
   long-run realized returns (glamour/overoptimism) — it is the *revision* that
   predicts. Consider industry-neutralizing or down-weighting the level leg, and
   backtest the sign. Pure scoring change, no new data.

2. **We fetch analyst-recommendation history, then discard it.** The Finnhub
   `stock/recommendation` call returns ~4 months of dated consensus buy/hold/sell
   counts, but the code keeps only the latest month (`data/sources.py`, `trend[0]`).
   Retaining the history yields a **recommendation-*change* momentum** signal —
   **Jegadeesh, Kim, Krische & Lee (2004), *J. Finance*** found the *change* in
   consensus predicts returns while the *level* does not. One small code change
   away from being computable.

---

## Deferred / rejected (and why) — honest free-data accounting

- **Estimate-revision magnitude & diffusion** — academically the *strongest*
  earnings signal (Chan-Jegadeesh-Lakonishok), but **no free point-in-time
  revision history exists**. Finnhub `eps-estimate`/`price-target` return **403
  (premium)** on our key; FMP analyst-estimates are paid; the only keyless feed is
  Yahoo `earningsTrend`, which is crumb-gated and **IP-blocked on oracle-prod**.
  Reachable only via self-accumulation (build history from day one) — defer.
- **Estimate dispersion** (Diether-Malloy-Scherbina 2002, ~9.5%/yr) — needs the
  per-analyst forecast σ; no free source. Defer.
- **13F institutional breadth / hedge-fund clustering** (Chen-Hong-Stein 2002) —
  free via EDGAR 13F, genuinely additive smart-money confirmation alongside
  insider Form-4, **but** quarterly and lagged 45 days, and 13F parsing is a
  non-trivial new source. Strong Phase-2 candidate; heavier lift than the 5 above.
- **Congressional-trade following** (Quiver / CapitolTrades / Autopilot-style
  copy-trading; evaluated 2026-07-01) — **rejected as a scored signal**. The oft-cited
  alpha is pre-STOCK-Act (Ziobrowski 2004/2011: abnormal returns on Senate/House
  trades, 1990s data); on *disclosed* post-2012 trades — the only data any tracker
  has — the aggregate evidence is null-to-negative (Eggers-Hainmueller 2013 "Capitol
  Losses"; Belmont-Sacerdote-Sehgal-Van Hoek 2020 NBER WP 26975, publ. *J. Public
  Econ.* 2022, find senators' post-STOCK-Act purchases slightly *underperform*
  industry/size-matched stocks at 1/3/6m, with no committee-assignment stock-picking
  ability), and the marquee outliers are a few concentrated spouse-account
  options bets (survivorship, not signal). Structural defects compound the weak base
  rate: the 30/45-day disclosure lag outlives any policy-information half-life;
  amounts are wide ranges ($1,001–$15k…) often on spouse/managed accounts, so
  conviction is unknowable; tracker "politician returns" are reconstructions from
  range midpoints and assumed fills; and the disclosure-day pop is partly *caused* by
  copy-trading flow (reflexive — an auto-copier systematically buys its own pop). If
  ever built: a scout **discovery originator** on the FINRA short-interest pattern
  (contested prior, ships disabled, attention-not-direction — the scorer + gates
  judge, the selection ledger measures), keyed on **cluster buys** (several members,
  same name/window — the only slice the literature leaves plausible), sourced from
  the free House Clerk PTR / Senate eFD disclosures (PDF/HTML-shaped; community JSON
  mirrors are unmaintained — needs a feasibility pass), never Quiver's paid API.
  **Never auto-execution, never a scored leg.** Not XBRL-backtestable (disclosures
  aren't in companyfacts) — ledger-measured only.
- **Google Trends attention** (Da-Engelberg-Gao 2011) — free but the endpoint is
  unofficial, rate-limited, and normalization is fiddly; weaker fit than the picks.
- **Magic Formula / Acquirer's Multiple / EV-EBIT** — we already *measured* EV/EBIT
  (~0.73 corr with FCF yield) and chose not to ship it; these recombine legs we
  have rather than adding new information. Rejected as duplicative.

---

## Recommended sequencing

1. **SUE/PEAD (#1)** and the **two quick wins** — lowest effort, data already in
   hand, immediately backtestable on live prices. Start here.
2. **Asset growth + accruals (#3)** and **shareholder yield (#5)** — pure XBRL,
   drop straight into `XbrlSignalSource` as measurement-only axes; let the rank-IC
   and collinearity diagnostics decide what graduates to a production sub-score.
3. **Residual momentum (#2)** — higher-value but medium effort; prototype the CAPM
   version as a standalone axis and compare it head-to-head against the existing
   momentum leg.
4. **Lazy Prices (#4)** — reuses the riskdiff infra; ship the live scoring leg
   first, validate on the guarded snapshot-replay path as accumulation builds.

Every graduation gate is the one shortlist already enforces: **measure rank IC and
collinearity in the backtest first; only wire a production sub-score once it earns
its weight.**
