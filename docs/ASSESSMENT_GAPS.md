# Assessment gaps & scoring roadmap

**Audience:** whoever improves *how we score*, not *what we pull*.
**Companion doc:** [`DATA_SOURCES.md`](DATA_SOURCES.md) covers **data** gaps (new feeds,
earnings-quality composites, macro, short interest). This doc covers **methodology**
gaps: the scoring model, its validation, and the Claude qualitative layer. Where a gap
needs a new feed, it links there instead of duplicating.

The bar: what separates a credible decision-support tool from a plausible-looking one is
(a) whether the score is *validated* against forward returns, (b) whether it measures the
*right* factors, and (c) whether the qualitative read actually reconciles with the numbers.
Today the architecture is clean and the wiring is honest about coverage — the gaps are in
those three areas.

---

## 1. The growth sub-score — full spec  ✅ SHIPPED

> **Status:** implemented. `stats.cagr` / `stats.growth_persistence`, the four
> `StockMetrics` growth fields, `scoring.growth_score`, the `config.yaml` bands +
> weight rebalance, the harness `bridge.py` wiring, and the screener `FMPProvider`
> legs (revenue/EPS/persistence — `fcf_cagr` stays `None` there to avoid an extra
> cash-flow API call) are all live, with tests in `tests/test_stats.py` and
> `tests/test_scoring.py`. The spec below is retained as the design record.

### Why it's missing and why it matters
The composite has four axes — **quality** (profitability + balance sheet), **moat**
(margins + ROIC), **opportunity** (`max(momentum, value)`), **insider** — and **no growth
axis at all**. A durable compounder and a stagnant cash-cow with identical margins, ROIC,
and leverage score *identically* today. Growth is the most-studied driver of long-run
equity return and it's the largest single hole in the factor set.

The raw material is already merged: `Statements` (`data/models.py:46`) carries 5y series
for `revenue`, `gross_profit`, `net_income`, and `free_cash_flow`, and
`Statements.revenue_cagr()` (`:61`) already exists and is **computed but never consumed by
the scorer**. So this is mostly wiring, not new fetching.

**Overlap to respect (don't double-count):** `momentum` is *price-based* growth (the market
already paying up) and `value`'s PEG leg *conditions* value on a growth rate. Neither
rewards **durable fundamental top-line / FCF compounding** directly — which is what this
axis adds. Keep growth strictly fundamental (revenue/FCF/earnings series); leave price to
momentum.

### Components (all None-safe, averaged like the other sub-scores)

| Leg | Definition | Source | Default band `[lo, hi]` | Notes |
|---|---|---|---|---|
| `revenue_cagr` | 3–5y revenue CAGR | `Statements.revenue_cagr()` | `[0.00, 0.20]` | always-positive series → plain CAGR is safe |
| `fcf_cagr` | 5y FCF CAGR | new `stats.cagr()` over `free_cash_flow` | `[0.00, 0.20]` | **only when both endpoints > 0**, else `None` |
| `eps_cagr` | 5y net-income CAGR (EPS proxy) | new `stats.cagr()` over `net_income` | `[0.00, 0.20]` | net income, not EPS — we don't carry a share-count series yet (see gap #2.5); proxy until we do |
| `revenue_growth_persistence` | fraction of YoY periods with positive revenue growth, 0..1 | new `stats.growth_persistence()` | `[0.50, 1.00]` | robust to sign flips; rewards *consistency*, penalizes lumpiness |

Rationale for the mix: a single CAGR is gameable by one outlier endpoint year, so we pair
**rate** (revenue/FCF/EPS CAGR) with **consistency** (`persistence`). FCF and net income can
go negative, where CAGR is mathematically meaningless — those legs return `None` on a
non-positive start/end and the scorer's existing weight-redistribution (`scoring.py:106`)
handles the gap, exactly as it does elsewhere.

### New code (single source of truth, matching the `stats.py` pattern)

`stats.py` — two new pure helpers, reused by both stacks (mirrors `median_pe` / `avg_roic`):

```python
def cagr(series: list[Optional[float]], most_recent_first: bool = True,
         min_points: int = 3) -> Optional[float]:
    """CAGR over a series. Returns None unless both endpoints are present and > 0
    (CAGR is undefined/misleading across a sign change), or with < min_points."""

def growth_persistence(series: list[Optional[float]], most_recent_first: bool = True,
                       min_points: int = 3) -> Optional[float]:
    """Fraction of consecutive YoY periods with positive growth, 0..1. Sign-safe."""
```

`models.py` — four scalar fields on `StockMetrics` (kept flat so the screener path can also
populate them without carrying series):

```python
    # Growth
    revenue_cagr: Optional[float] = None
    fcf_cagr: Optional[float] = None
    eps_cagr: Optional[float] = None
    revenue_growth_persistence: Optional[float] = None
```

`scoring.py` — new sub-score, identical shape to the others:

```python
def growth_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.revenue_cagr, *t["revenue_cagr"]),
        _norm(m.fcf_cagr, *t["fcf_cagr"]),
        _norm(m.eps_cagr, *t["eps_cagr"]),
        _norm(m.revenue_growth_persistence, *t["revenue_growth_persistence"]),
    ])
```

…added to `parts` in `score()` with its weight, so it flows through the existing
numerator/denominator redistribution unchanged.

`bridge.py` (harness — the natural home, since `Statements` is right there):

```python
    if st:
        m.revenue_cagr = st.revenue_cagr()
        m.fcf_cagr = cagr(st.free_cash_flow)
        m.eps_cagr = cagr(st.net_income)
        m.revenue_growth_persistence = growth_persistence(st.revenue)
```

Screener path: `FMPProvider` already pulls statements for `gross_margin_stability`; populate
the same four scalars there so `--engine screener` reaches parity (or accept it as a known
harness-only signal initially, the way `eps_revision` is an accepted screener-only gap).

### Config + weights

`config.yaml` thresholds block:

```yaml
  # Growth
  revenue_cagr:                [0.00, 0.20]
  fcf_cagr:                    [0.00, 0.20]
  eps_cagr:                    [0.00, 0.20]
  revenue_growth_persistence:  [0.50, 1.00]
```

Adding a fifth weighted axis means the weights must re-sum to 1.0. **Proposed default
(must be backtested — see gap #1):**

```yaml
weights:
  quality:      0.20   # was 0.25
  moat:         0.20   # was 0.25
  growth:       0.15   # new
  opportunity:  0.30   # unchanged — still the dominant axis
  insider:      0.15   # was 0.20
```

This keeps `opportunity` dominant (Chris's momentum-OR-value brief), funds growth by
trimming the two slowest-moving axes, and leaves insider as a meaningful-but-secondary
overlay. **These numbers are a starting point, not a result** — they should be fit by the
backtest harness (gap #1), not hand-asserted. Until that exists, this is a defensible prior.

### Tests to add (`tests/test_scoring.py`, `tests/test_stats.py`)
- `cagr` endpoints, negative-endpoint → `None`, `<min_points` → `None`, ordering flag.
- `growth_persistence` all-up → 1.0, all-down → 0.0, mixed, sign-safe.
- `growth_score` None-redistribution (e.g. only `revenue_cagr` present).
- A `score()` case proving the composite denominator drops growth's weight when the whole
  axis is `None` (no silent zero).
- Update `ScoreCard` / table / `_card_dict` / CSV to surface `growth` (it's a new column).

### Acceptance
A high-growth name (rising revenue + FCF, consistent YoY) scores materially higher than a
flat-but-profitable peer with the same margins/ROIC; a name with one spike year scores
*lower* than a steady compounder with the same CAGR (persistence leg working); and a name
with negative/lumpy FCF degrades to the legs that are defined rather than erroring.

---

## 2. The rest of the methodology gaps

Ranked by impact on a decision-grade tool. Each names the concrete fix and the file it
touches.

### Tier 1 — credibility blockers

#### 2.1 No validation that the score predicts anything  ★ highest leverage  ✅ SHIPPED (v1: momentum axis)

> **Status:** the backtest harness is implemented — `src/shortlist/backtest/`
> (`shortlist-backtest` CLI), design record in
> `docs/superpowers/specs/2026-06-01-backtest-design.md`, walkthrough in
> `HARNESS.md` → "Backtesting". It reports **rank IC** (time-series and
> cross-sectional, with mean/std/ICIR/**t-stat**/hit-rate) and **quantile
> forward-return spreads** per signal × horizon, with no look-ahead (closes
> truncated at *T*, returns only `> T`), non-overlapping per-horizon grids,
> excess-over-SPY returns, and an explicit survivorship caveat. It is
> **signal-agnostic** (`Observation(as_of, ticker, {signal: sub-score})`) and
> reuses the **real** scoring chain, so it does not validate a reimplementation.
>
> **Validated today:** the **momentum** axis, on a real ~80-name large-cap
> universe (price-only, keyless). First result (excess of SPY, full daily
> history): cross-sectional IC is *insignificant* across 1/3/6/12m (|t| < 1.1),
> while short-horizon **time-series IC is negative and directionally consistent**
> (1m ≈ −0.023, 3m ≈ −0.031; only 30%/37% of names show a positive IC) — large-cap
> short-term mean-reversion. (The TS t-stat assumes name-independence and is
> anti-conservative, so lean on the mean IC + hit-rate.) Honest evidence that the
> raw momentum bands deserve scrutiny — exactly what this gap was for. **Tracked as a
> concrete action in §2.3** (score momentum sector-/universe-relative, then re-backtest).
>
> **Built, tested, guarded (Phase 2, blocked on data):** weight-fitting
> (`backtest/fit.py`, walk-forward + 50% shrinkage toward the prior) and
> snapshot-replay (`SnapshotSignalSource`) activate once point-in-time multi-axis
> history accumulates. The **accumulation mechanism now exists** —
> `shortlist-accumulate` (idempotent, point-in-time, free-tier-aware; design record
> `docs/superpowers/specs/2026-06-01-snapshot-accumulation-design.md`) — but its
> daily schedule is **dormant/opt-in** (`deploy/`), so the 24-date clock starts only
> when an operator enables it (or runs it manually). The EDGAR XBRL source below is
> the other path to that history. The plug sketch below is retained as rationale.

Every weight (`0.25/0.25/0.30/0.20`) and every band in `config.yaml` is **asserted, never
measured**. There is no backtest, no information coefficient (IC), no decile/quintile
forward-return spread. For a tool meant to guide capital this is the gap that makes every
other improvement *unmeasurable* — including the growth weights proposed above.

The substrate already exists: `data/store.py` persists point-in-time `TickerSnapshot`s.
- **Plug:** a `backtest.py` that replays stored snapshots against forward N-month returns
  (Yahoo gives the price history, keyless), and reports **rank IC** and
  **top-vs-bottom-quintile spread** per sub-score and for the composite. Then *fit* weights
  instead of guessing. This converts `config.yaml` from taste to evidence and makes growth
  (§1), sector-relative scoring (§2.3), and every band tweak A/B-testable.
- **Honest caveat:** real backtesting needs point-in-time *fundamentals* (as-reported, not
  restated) to avoid look-ahead bias — that's the EDGAR XBRL work in `DATA_SOURCES.md` A1.
  Start with a price/momentum IC (data already on hand) and expand as XBRL history lands.

#### 2.2 The `value` axis is entirely relative and sentiment-anchored
All four value legs (`scoring.py:52`) are relative: upside-to-**analyst-target**, FCF yield,
**PE-vs-own-5y-median**, PEG. Two structural failure modes:
- **Analyst targets lag and skew bullish** — anchoring "upside" to them imports sell-side
  optimism as if it were signal.
- **PE-vs-own-history is a value-trap magnet** — a structurally declining business looks
  "cheap vs its own history" *forever* as it de-rates, with no absolute floor to catch
  "cheap for a reason."
- **Plug:** add ≥1 absolute valuation leg — EV/FCF, or a **reverse-DCF implied growth rate**
  checked against actual growth (the 5y `Statements` make a crude reverse-DCF feasible with
  no new feed). Pairs naturally with the growth axis: cheap **and** growing ≠ cheap **and**
  shrinking. See also the Piotroski F-Score (`DATA_SOURCES.md` D1) as a value-trap filter.

#### 2.3 Absolute threshold bands misfire across sectors
A 90%-margin software name and a 25%-margin industrial can't share one `gross_margin: [0.20,
0.70]` band, so cross-sector ranking is distorted (the docs already flag this for
financials, but it's general). Hand-tuned absolute bands are also brittle.
- **Plug:** **cross-sectional / sector-relative percentile scoring** — rank each metric
  within sector (or the run universe) instead of mapping to a fixed band. Highest
  quality-per-effort fix; structurally subsumes the financials problem. Needs a universe run
  (caching + paid tier, `DATA_SOURCES.md`) to have enough peers to rank against.
- **Concrete follow-up — momentum bands (from the §2.1 backtest result, ★ actionable):**
  the first backtest found momentum's **cross-sectional IC insignificant** and its
  **short-horizon time-series IC negative** across a large-cap universe (§2.1). The
  most likely cause is exactly this gap: the *absolute* momentum bands
  (`price_vs_200dma [-0.10, 0.30]`, `rel_strength_6m [-0.15, 0.25]`) **saturate** when
  most mega-caps sit above the band in a bull tape, collapsing cross-sectional rank
  variance. **Action:** score momentum **universe-/sector-relative** (the same
  percentile approach proposed above), then re-run `shortlist-backtest` to check
  whether a relative momentum IC emerges. Validate against the accumulation store once
  it clears the 24-date threshold (`shortlist-accumulate status`). This ties the
  finding (§2.1) → the remedy (§2.3 percentile scoring) → its data dependency
  (snapshot accumulation) in one place.

### Tier 2 — measuring the wrong thing / missing checks

#### 2.4 Coverage isn't folded into ranking confidence
Weight-redistribution (`scoring.py:106`) is elegant but it lets a name with **only**
`momentum` present score 80 on that one axis and rank *above* a fully-covered 78. Thin data
buys false confidence.
- **Plug:** compute an effective-coverage / confidence factor (how many axes had real
  inputs) and either tilt the rank by it or surface it as a first-class column so a sparse
  80 reads differently from a complete 78. The `coverage` diagnostic already knows what's
  missing — this just feeds it into rank, not just the stderr note.

#### 2.5 No earnings-quality / dilution checks
`quality_score` (`scoring.py:24`) is blind to whether earnings are *real* or whether
shareholders are being diluted:
- **Share-count trend / SBC dilution** is entirely invisible — a serial diluter with a
  flattered per-share story scores like a buyback compounder. Add a share-count series to
  `Statements` and an `eps_cagr` that's genuinely per-share (it's a net-income proxy today,
  see §1).
- **Net-income-vs-FCF divergence / accruals** — see Beneish M-Score & accruals ratio in
  `DATA_SOURCES.md` D3, and Altman Z (D2) for a solvency early-warning that's smarter than
  the raw D/E gate (§2.7).

#### 2.6 Insider scoring throws away signal it already parses
`insider_score` (`scoring.py:61`) nets 6m dollars + Finnhub MSPR, but `providers/_form4.py`
parses the granular trades to do far better:
- **Cluster buys** (several insiders buying at once) — among the strongest single signals.
- **Role weighting** — a CEO/CFO purchase ≫ a director's.
- **10b5-1 planned-sale filtering** — routine scheduled sells shouldn't read as bearish and
  drag the score (or trip the `heavy_insider_selling` gate) the way an opportunistic dump
  should.
- **Plug:** enrich the `_form4.py` aggregation (it's the shared leaf module, so both stacks
  benefit) with cluster/role/10b5-1 fields; fold into `insider_score`.

#### 2.7 The gates can flag *good* businesses
`check_gates` (`scoring.py:74`) has two dangerous rules:
- `over_leveraged` = `debt_to_equity > 5` misfires on quality compounders with **negative
  book equity from buybacks** (AZO, MCD, HD, SBUX) — D/E goes negative or explosive and the
  gate is meaningless. Prefer **net-debt / EBITDA** with sector-aware exemptions.
- `negative_fcf` gates legitimate heavy-capex / hyper-growth names outright. A gate that
  removes good businesses is worse than no gate.
- **Plug:** replace D/E gate with net-debt/EBITDA + Altman Z (`DATA_SOURCES.md` D2); make
  `negative_fcf` sector/stage-aware or downgrade it to a soft flag.

#### 2.8 `opportunity = max(momentum, value)` discards a real signal
`max()` (`scoring.py:97`) is the right call for "qualify on either axis," but when momentum
is **down** and value is **up**, that disagreement *is* the classic falling-knife /
value-trap pattern — informative — and `max()` silently takes the high side.
- **Plug:** keep `max()` for the score, but emit a `momentum_value_divergence` flag when the
  two axes strongly disagree, so the deep dive knows to ask "value trap or mispricing?"

#### 2.9 Risk metrics computed but never scored
`realized_vol` and `max_drawdown` are bridged into `StockMetrics` (`bridge.py:45`) and
explicitly marked unscored; `beta` lives in `Profile` and isn't even mapped. There is no
risk overlay and no risk-adjusted ranking.
- **Plug:** either a `risk` axis or a volatility/drawdown soft gate (already a tracked
  follow-up in `DATA_SOURCES.md` A3), and map `beta` through the bridge so it's at least
  available. Pairs with the FRED macro regime overlay (`DATA_SOURCES.md` A2).

---

## 3. Making Claude's analysis more effective (`research/`)

The qualitative layer is well-built — grounded, quote-verified, cached by accession, prompt
hardened against injection — but **narrow**.

### 3.1 Only the 10-K is read
`assess.py` ingests Item 1 / 1A / 7 of a single 10-K, which can be ~11 months stale and omits
the richest qualitative sources:
- **Earnings-call transcripts** — management tone, *fresh* guidance, analyst Q&A. The single
  biggest win; it's the most current management signal available.
- **Latest 10-Q** — the current quarter the annual filing can't reflect.
- **DEF 14A proxy** — comp alignment, governance, related-party transactions (capital
  allocation red flags the 10-K won't volunteer).
- **Plug:** extend `research/filings.py` to fetch the latest 10-Q and proxy via EDGAR;
  add transcripts when a source is available. Feed alongside the 10-K with clear section
  labels (the prompt already treats filing text as DATA, so this is additive).

### 3.2 Claude is flying blind to the numbers
`assess(card, filing, ...)` (`assess.py:75`) takes the `ScoreCard` but the docstring says it
is *"unused today; reserved for score-aware prompting."* So Claude can't reconcile narrative
with quant. It can't say "the screen flags this as cheap-value — here's whether the 10-K
explains why the market disagrees," or "management's growth story vs the actual 3% revenue
CAGR." That reconciliation is the most decision-useful thing the layer could produce.
- **Plug:** pass the sub-scores **and** the 5y `Statements` series into the user prompt, and
  ask Claude to explicitly reconcile the narrative against the numbers (and flag where they
  conflict). This is the cheapest high-impact change in the whole layer — the plumbing
  (`card`) is already threaded through.

### 3.3 No bull / bear / pre-mortem structure
Risks + red-flags + synthesis is a *summary*, not a decision aid.
- **Plug:** restructure `QualitativeAssessment` (`research/models.py`) toward a falsifiable
  thesis: explicit **bull case**, **bear case**, and **"what would change my mind."** Keep
  the quote-grounding requirement on every factual claim.

### 3.4 No year-over-year risk-factor diff
Newly-*added* 10-K Item 1A risk factors are a documented alpha signal. Briefs are already
cached by accession, so last year's filing is one fetch away.
- **Plug:** diff this year's Item 1A against the prior year's; surface *added* risks as a
  distinct, high-attention section. Low effort, high signal.

---

## 4. Recommended sequencing

Methodology and data interleave — this orders both against the gaps above and the data work
in `DATA_SOURCES.md`:

1. **Growth sub-score (§1)** — closes the biggest factor hole; data already merged; mostly
   wiring. Ship behind the proposed weights as a defensible prior.
2. **Backtest harness (§2.1)** — start with price/momentum IC over stored snapshots; makes
   the growth weights, the bands, and everything below *measurable* instead of asserted.
3. **Score-aware + multi-filing Claude (§3.1, §3.2)** — cheap plumbing changes that sharply
   raise the qualitative layer's usefulness.
4. **Gate fixes + dilution in quality (§2.5, §2.7)** — stop penalizing good businesses;
   needs a share-count series and (ideally) Altman Z from EDGAR XBRL (`DATA_SOURCES.md` A1).
5. **Cross-sectional / sector-relative scoring (§2.3)** — biggest accuracy gain, but needs a
   universe run, so it follows the caching + paid-tier unlock in `DATA_SOURCES.md`.
6. **Absolute valuation leg (§2.2), insider granularity (§2.6), risk overlay (§2.9),
   bull/bear + YoY risk diff (§3.3, §3.4)** — as capacity allows.

Two house rules from `CLAUDE.md` apply to every change here: route any error string that may
contain a URL through `env.py:redact_secrets()`, and keep coverage **honest** — a missing
input must lower coverage / redistribute weight, never silently zero a sub-score.
