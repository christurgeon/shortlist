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

## 1. The growth sub-score  ✅ SHIPPED

> **Status:** implemented. `stats.cagr` / `stats.growth_persistence`, the four
> `StockMetrics` growth fields, `scoring.growth_score`, the `config.yaml` bands +
> weight rebalance, and the harness `bridge.py` wiring are all live, with tests in
> `tests/test_stats.py` and `tests/test_scoring.py`. The spec below is retained as
> the design record.

### Design rationale (retained; the implementation is the source of truth)
The composite needed a **durable-compounding** axis: two names with identical margins, ROIC,
and leverage but different growth trajectories used to score identically. The axis is strictly
*fundamental* — revenue/FCF/EPS CAGR paired with YoY **persistence** (a single CAGR is gameable
by one outlier endpoint year). It deliberately does **not** overlap `momentum` (price-based
growth) or `value`'s PEG leg (which only *conditions* value on a growth rate).

Legs are None-safe and averaged like the other sub-scores: `cagr` returns `None` across a sign
change (where it is undefined) and `growth_persistence` is sign-safe, so the existing
weight-redistribution handles missing legs with no silent zero. `eps_cagr` uses net income as
an EPS proxy until a share-count series exists (gap #2.5). The harness populates all four legs
from `Statements`.

**Weights remain an unfitted prior** — the default growth weight funds itself by trimming the
slowest-moving axes, but should be *fit* by the backtest harness (gap #1), not hand-asserted.

---

## 2. The rest of the methodology gaps

Ranked by impact on a decision-grade tool. Each names the concrete fix and the file it
touches.

### Tier 1 — credibility blockers

#### 2.1 No validation that the score predicts anything  ★ highest leverage  ✅ SHIPPED (v1: momentum axis)

> **Status:** the backtest harness is implemented — `src/shortlist/backtest/`
> (`shortlist-backtest` CLI), walkthrough in
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
> **Fundamental weight-fitting (✅ WIRED, XBRL path):** `--source xbrl --fit`
> (`backtest/fit.py` + `backtest/fit_data.py`) now fits the **fundamental** sub-weights
> (quality/moat/growth/value) walk-forward (coordinate ascent + 50% shrinkage toward the
> `config.yaml` prior), scoring each fold out-of-sample and emitting a **proposal** — it
> never writes config. An endorsement gate evaluates the per-fold **paired** (shrunk-fit
> vs prior) OOS difference (≥36 periods, ≥5 OOS folds, mean edge ≥ +0.02, ≥4/5 folds
> positive, t-stat ≥ 2) → PROPOSE or NO-CHANGE. On the survivorship-biased bundled
> largecap (2026-06) the verdict is **NO-CHANGE** at 3m and 6m: the fit reproduces the
> directional first-run finding (down-weight quality, up-weight growth) but the shipped
> weights beat the prior OOS by only +0.005 (vs the +0.02 bar), positive in just 2/5
> folds. Honest evidence-of-record, not a config change — exactly what this gap was for.
> Numbers + recommendation: `2026-06-03-xbrl-weight-fit-results` (local working notes, not committed).
>
> **Built, tested, guarded (Phase 2, blocked on data):** the *composite* (all-7-axis)
> weight-fit and snapshot-replay (`SnapshotSignalSource`) activate once point-in-time
> multi-axis history accumulates (XBRL reaches only 4 of 7 axes; momentum/insider/risk
> need the snapshot path). The **accumulation mechanism now exists** —
> `shortlist-accumulate` (idempotent, point-in-time, free-tier-aware; walkthrough in
> `HARNESS.md` → "Feeding the snapshot path") — but its
> daily schedule is **dormant/opt-in** (`deploy/`), so the 24-date clock starts only
> when an operator enables it (or runs it manually). The EDGAR XBRL source below is
> the other path to that history. The plug sketch below is retained as rationale.
>
> **Validated without waiting (XBRL path, ✅ SHIPPED):** `XbrlSignalSource`
> (`backtest/signals.py`, `--source xbrl`) reconstructs the **fundamental**
> sub-scores — quality / moat / growth / value (2-of-4 legs: `fcf_yield`,
> `pe_vs_history`) — point-in-time from SEC `companyfacts` (`filed ≤ as_of`,
> restatement-aware; aliases resolved by **priority, not merge**), reusing the real
> `scoring.*_score` functions. This activates fundamental-axis IC **today**,
> independent of the 24-date snapshot clock. The extractor is `providers/_xbrl_facts.py`
> (pure leaf) + `backtest/xbrl.py` (keyless companyfacts fetch, `.cache/sec_xbrl`).
> **First results** (largecap, 79 names, excess-of-SPY; 3m/6m horizons clear the
> engine's trust gates of ≥24 periods and ≥30 names/date, 12m is flagged EXPLORATORY
> at 16 periods; all are early, survivorship-biased directional evidence — not
> significant): **growth** cross-sectional IC is positive and rises with
> horizon (+0.035 / +0.052 / +0.072 at 1q/2q/4q; 12m quantile spread +0.097);
> **value** time-series IC is positive (+0.075 / +0.094 / +0.107); **quality** TS IC
> is negative (~ -0.06 to -0.11); **moat** is weak/negative. Unfitted priors, now
> measurable — exactly what this gap was for. **Limitations:** sub-score level (no
> sector masking, matching the momentum source); `value`'s `peg`/`upside_to_target`
> legs and the `insider` axis aren't reconstructable from XBRL; **IFRS 20-F foreign
> issuers (data under `ifrs-full`) are skipped**.

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

#### 2.2 The `value` axis is mostly relative/sentiment-anchored — TRAP-DETECTION HALF ADDRESSED
Three of four value legs (`scoring.py`) are relative/sentiment: upside-to-**analyst-target**,
**PE-vs-own-5y-median**, PEG. (`fcf_yield` is the exception — a *fixed*-band absolute
cash-flow yield, not relative; an earlier draft of this section wrongly called all four
relative.) Two structural failure modes:
- **Analyst targets lag and skew bullish** — anchoring "upside" to them imports sell-side
  optimism as if it were signal.
- **PE-vs-own-history is a value-trap magnet** — a structurally declining business looks
  "cheap vs its own history" *forever* as it de-rates, with no absolute floor to catch
  "cheap for a reason."

> **SHIPPED — trap-detection half (config-gated, OFF by default):** a **Core-6
> Piotroski-inspired fundamental-quality score** (`stats.piotroski_f`; asset-free &
> equity-free: NI>0, OCF>0, accruals OCF>NI, Δnet-margin, Δdebt/revenue, Δgross-margin) now
> refines the existing `value_trap` flag — **suppressing** it on cheap-but-*improving* names
> and **confirming** it on cheap-but-*deteriorating* ones. It is surfaced as first-class
> `piotroski_f`/`piotroski_f_legs` output (JSON/CSV) on the harness, **sector-masked**
> (financials/insurers/REITs), and reconstructable point-in-time → backtestable as a
> standalone `piotroski` axis (`--source xbrl`). The scorer is **byte-identical** when the
> `flags.value_trap.piotroski` block is absent (it ships commented out). Crucially this does
> **NOT** touch `value_score` — an absolute *cheapness* multiple (EV/FCF) was rejected because
> it double-counts the already-absolute `fcf_yield` and, being cheap on a trap too, cannot
> *detect* traps. **Validation (evidence-of-record):** the standalone axis IC is
> ~0 / insignificant on the 79-name survivorship-biased large-cap set (does NOT clear the
> §2.1 bar — but large-cap is the wrong universe for a Piotroski score, and the IC validates
> the axis, not the suppress/confirm mechanism); the `value` axis IC is unchanged, confirming
> `value_score` was preserved. Bands/thresholds remain **unfitted priors**. Design + numbers
> are summarized in this section; the `2026-06-03-piotroski-value-trap` working notes are local, not committed.

- **Absolute-multiple half — measurement shipped, production leg gated:** add an absolute
  valuation leg proper — **EV/EBIT** (fresh numerator vs `fcf_yield`; EV/FCF rejected as
  collinear), or a reverse-DCF. The leg ships **OFF behind a config flag** and **first**
  extends the backtest to emit per-leg `value`-IC attribution so it cannot silently degrade
  the validated `value` average. The 5y `Statements` make a crude reverse-DCF feasible with no
  new feed; EV/EBIT needs no new feed either (the XBRL panel already carries `cash`).
- **MEASUREMENT SHIPPED (2026-06-13), production leg DEFERRED:** the EV/EBIT
  earnings-yield metric (`StockMetrics.ebit_ev_yield`, derived on both the harness
  and the XBRL panel) plus backtest instrumentation — standalone `ebit_ev_yield`
  axis, per-leg `value_fcf_yield` / `value_pe_vs_history` attribution, a
  `value_plus_evebit` combined axis, and a `corr(ebit_ev_yield, fcf_yield)`
  collinearity diagnostic (`--source xbrl`). The production scoring leg ships
  ONLY IF `IC(value_plus_evebit) > IC(value)` AND the leg correlation is materially
  < 0.5 (spec `2026-06-13-absolute-valuation-leg-ev-ebit-design.md` §9/§11).
- **MEASURED (2026-06-13) → production leg STAYS OFF.** First `--source xbrl` run
  (16 large-caps — the oracle-prod VPS RAM ceiling, XS-IC below the ~30-name trust
  floor, so directional only) returned `corr(ebit_ev_yield, fcf_yield) = +0.724` —
  well above the 0.5 kill-switch (and squarely in the PM-skeptic's predicted
  0.6–0.8 band). `value_plus_evebit` TS-IC only marginally edged `value`
  (h3 0.153 vs 0.143; h6 0.180 vs 0.164; h12 0.230 vs 0.212), on the
  anti-conservative metric. The enable rule is an AND; the collinearity half fails
  decisively, so the leg is **not** wired into production. A real trial needs a
  small/mid-cap universe (EV/EBIT's natural habitat; large-cap is the wrong set per
  §10) and a sector-relative home (§2.3) — on a bigger box than the VPS.
- **Reverse-DCF — SHIPPED to the RESEARCH layer (2026-06-13), NOT a scored leg.**
  The EV/EBIT review routed reverse-DCF out of the composite (free knobs, no clean
  backtest) and into the brief instead. `research/reverse_dcf.py` now emits one
  deterministic QUANT-CONTEXT line — *"price implies ~X%/yr perpetual FCF growth"*
  (single-stage Gordon `g = R − F0/P`, normalized median-FCF base) — for Claude to
  reconcile against the realized revenue/FCF CAGR. Research-only, scorer
  byte-identical, no feed, no rank impact. A three-agent review hardened it:
  single-stage (not two-stage — proven a monotone 1:1 transform of FCF yield, so
  two-stage was false precision); a **symmetric** prompt nudge (high implied growth
  is NOT "expensive" on a durable compounder — guards the canonical reverse-DCF
  misread); a deterministic run-rate caveat; NaN/inf guards; honest base-count label.
  Spec `2026-06-13-reverse-dcf-implied-growth-design.md`. **Unfalsifiable framing aid,
  not a signal** — never backtested, never feeds the score; its job is forcing a
  price-vs-realized-growth reconciliation the brief otherwise lacks.

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

#### 2.4 Coverage isn't folded into ranking confidence — **SURFACED (tilt deferred to §2.1)**
Weight-redistribution (`scoring.py:106`) is elegant but it lets a name with **only**
`momentum` present score 80 on that one axis and rank *above* a fully-covered 78. Thin data
buys false confidence.
- **Plug:** compute an effective-coverage / confidence factor (how many axes had real
  inputs) and either tilt the rank by it or surface it as a first-class column so a sparse
  80 reads differently from a complete 78. The `coverage` diagnostic already knows what's
  missing — this just feeds it into rank, not just the stderr note.
- **Shipped (the "surface it as a column" branch):** `confidence` is now a first-class
  column (tables/CSV/JSON) plus a `thin` advisory (`confidence < ranking.thin_below`), and
  a single `rank_key (scored, composite, confidence)` makes confidence an **exact-tie
  breaker** at every sort site.
- **Deliberately NOT done:** the *ranking tilt* (reordering a sparse 80 below a complete
  78). A continuous tilt double-counts absence (composite already redistributes), is
  confounded by FMP-402 gating (penalizes large-caps for subscription tier), and risks
  burying strong-but-thin deep-value names in a human pre-screen. Reordering on coverage is
  deferred until a coverage-vs-forward-returns backtest justifies it (§2.1). For now we
  surface and let the human judge; composite still dominates the sort (no-bury guarantee).

#### 2.5 No earnings-quality / dilution checks — DILUTION HALF SHIPPED (config-gated)
`quality_score` (`scoring.py`) is blind to whether earnings are *real* or whether
shareholders are being diluted:
- **Share-count trend / SBC dilution** was entirely invisible — a serial diluter with a
  flattered per-share story scored like a buyback compounder.

> **SHIPPED — dilution half (config-gated, OFF by default):** a **diluted weighted-avg
> share-count CAGR** (`StockMetrics.share_count_cagr`; + = net issuance, − = buybacks) is
> now derived from already-fetched data on the harness (`Statements.diluted_shares` via the
> new `_edgar_facts._row_diluted_shares` row matcher) and the `--source xbrl` backtest (the
> previously-scaffolded `WeightedAverageNumberOfDilutedSharesOutstanding`). It is
> **surfaced** first-class (JSON/CSV `share_count_cagr`) and drives a soft `dilution`
> advisory flag (`flags.dilution`; ON by default, never affects `passed`/`composite`/
> `scored` — like `value_trap`). The **scoring** impact ships behind the opt-in
> `quality.dilution` config block (**commented out** → scorer byte-identical to the
> pre-feature version, like `insider.conviction`): when enabled, `quality_score` gains an
> inverted `share_count_cagr` leg (diluters score below buyback compounders) and the growth
> `eps_cagr` leg switches from the net-income proxy to **genuine per-share** diluted-EPS CAGR
> (`StockMetrics.eps_cagr_ps`), closing the §1 `eps_cagr` proxy gap. None-safe redistribution
> means the leg is bit-identical wherever the share series is absent. The band, the flag
> threshold, and the leg are **UNFITTED priors** — `XbrlSignalSource` now emits a standalone
> **`share_count`** axis (`--source xbrl`, alongside `piotroski`) so the share-count rank IC
> is measurable (§2.1) before either is trusted. **Caveats:** (a) **not masked** for
> financials/REITs (share count is universally defined; REIT equity issuance is normal, so
> the advisory will fire there — sector-aware *calibration* of the leg is deferred, like the
> other masked-leg recalibration in §2.3); (b) the signal reads **as-reported** share counts
> — within a single 10-K's comparative columns these are split-restated, but a reverse split
> or an un-restated cross-filing window (XBRL companyfacts) can still inject a spurious jump
> (the FINRA short-interest path carries an explicit `split_flag`; this one does not yet); (c)
> on a sign-crossing diluted-EPS series (loss-year inflection) `eps_cagr_ps` is undefined and
> the growth leg falls back to the dilution-blind net-income proxy.

- **Still open — accruals / solvency half:** **Net-income-vs-FCF divergence / accruals** —
  see Beneish M-Score & accruals ratio in `DATA_SOURCES.md` D3, and Altman Z (D2) for a
  solvency early-warning that's smarter than the raw D/E gate (§2.7).

#### 2.6 Insider scoring throws away signal it already parses
`insider_score` (`scoring.py:61`) nets 6m dollars + Finnhub MSPR, but `providers/_form4.py`
parses the granular trades to do far better:
- **Cluster buys** (several insiders buying at once) — among the strongest single signals.
- **Role weighting** — a CEO/CFO purchase ≫ a director's.
- **10b5-1 planned-sale filtering** — routine scheduled sells shouldn't read as bearish and
  drag the score (or trip the `heavy_insider_selling` gate) the way an opportunistic dump
  should.
- **Plug:** enrich the `_form4.py` aggregation (the shared leaf module behind the harness
  `EdgarSource` and the XBRL backtest) with cluster/role/10b5-1 fields; fold into
  `insider_score`.

> **SHIPPED (config-gated, OFF by default):** cluster + role-weighting + 10b5-1 forgiveness
> are now folded into `insider_score` as a third `_avg` leg, gated by the `insider.conviction`
> config block (`config.yaml`). The block ships **commented out** — `yaml.safe_load` yields no
> `insider.conviction` key, so the scorer is **bit-identical** to the pre-feature scorer when
> absent. The `heavy_insider_selling` gate is **deliberately untouched** (10b5-1 detection
> forgives the score but never the gate — a regression test pins this). Two new advisory flags
> (`insider_cluster_buy`, `planned_sale`) fire when conviction fields are populated; both are
> soft and never affect `passed`/`composite`. All threshold bands and weights are **unfitted
> priors** — backtest the standalone cluster/role rank IC (§2.1) before trusting them.
> `EdgarSource` accepts the conviction config and passes it through to
> `providers/_form4.py`, which extracts role strings and 10b5-1 footnote heuristics from the
> already-fetched edgartools objects. The conviction design is summarized in this section;
> the `2026-06-02-insider-conviction-design` working notes are local, not committed.

#### 2.7 The gates can flag *good* businesses — ✅ SHIPPED (config-gated, default ON)
`check_gates` (`scoring.py`) had two dangerous rules:
- `over_leveraged` = `debt_to_equity > 5` misfired on quality compounders with **negative
  book equity from buybacks** (AZO, MCD, HD, SBUX) — D/E goes negative or explosive and the
  gate is meaningless.
- `negative_fcf` gated legitimate heavy-capex / hyper-growth names outright. A gate that
  removes good businesses is worse than no gate.

> **SHIPPED — both gates, config-gated (`gates.leverage` / `gates.fcf`, default ON; remove a
> block for the byte-identical pre-feature gate). Spec: `docs/superpowers/specs/2026-06-08-gate-fixes-design.md`.**
>
> - **`over_leveraged`** now trips on **net-debt / EBITDA** (`> max_net_debt_to_ebitda`, prior 4.0)
>   when EBITDA is usable (present, >0, EBITDA-margin ≥ `min_ebitda_margin`). When EBITDA is
>   unavailable it falls back to an **artifact-guarded, coverage-corroborated D/E** rule: abstain
>   on the equity-distortion artifact (D/E ≤ 0 or > `dte_artifact_ceiling` 20×), and within the
>   plausible-leverage window (D/E in (max, ceiling]) trip only when interest coverage is
>   weak/absent (< `min_interest_coverage_for_gate` 2.0). So buyback compounders with thin/negative
>   equity are spared; genuinely distressed levered names are still caught.
> - **`negative_fcf`** is now **stage-aware**: a negative-FCF name is excused when growth is strong
>   AND sustained (`revenue_cagr ≥ 0.15` AND `revenue_growth_persistence ≥ 0.70`); otherwise it
>   gates. A soft **`cash_burn`** flag fires on *any* negative FCF regardless (the burn is always
>   surfaced even when the gate excuses it).
> - **Data:** new `StockMetrics` fields `revenue` / `ebitda` / `cash_and_equivalents` /
>   `net_debt_to_ebitda` (signed; display-floored to net-cash in JSON/CSV). Derived on the harness
>   from a **new EDGAR balance-sheet + cash-flow extraction** (`_edgar_facts`: total_debt =
>   LT+current+short, cash, D&A from the cash-flow statement, operating income; interest coverage
>   backfilled when FMP gated), and on the XBRL backtest panel. Verified against a live AAPL filing
>   (`tests/test_edgar_leverage_live.py`).
> - **Validation:** all thresholds are **UNFITTED priors** — a standalone `net_debt_to_ebitda` axis
>   (`--source xbrl`) makes the rank IC measurable (§2.1) before they are trusted.
> - **Masking + back-compat:** gate names unchanged, so financials/insurers/REITs keep both gates
>   masked and `research.screening_call.gate_clamp` is unaffected. With the config blocks absent the
>   scorer is byte-identical to the pre-feature gate (pinned by `tests/test_gate_backcompat.py`).
>
> **Still deferred:** Altman-Z solvency early-warning (`DATA_SOURCES.md` D2); FCF-series
> persistence (sustained vs one-off burn — `fcf_positive` is single-point today); sector
> *recalibration* of the leverage bands (§2.3); fitting any of the four thresholds.
>
> **Follow-up (validation — shipped ON with UNFITTED priors):** the thresholds (net-debt/EBITDA
> 4.0, EBITDA-margin floor 0.03, D/E ceiling 20, coverage 2.0; FCF excuse 0.15 / 0.70) are
> reasoned priors, not measured. **Next step:** run `uv run shortlist-backtest --source xbrl` and
> read the standalone `net_debt_to_ebitda` axis rank IC before tightening/loosening the 4.0 gate
> (the axis is wired so this is a one-command check; cf. §2.1). The **`negative_fcf` excuse is still
> unmeasured, but the field-level blocker is cleared:** `_xbrl_facts.panel_to_metrics` now sets
> `fcf_positive` (sign of `latest(p.fcf)`, abstaining to None when the latest FY's FCF isn't
> computable — newest year tags OCF but no capex — so it mirrors `bridge.py`'s abstention rather
> than reporting a stale older-year sign), so the gate is exercisable on XBRL-derived metrics. What
> remains is a *measurement path* — `XbrlSignalSource` emits sub-score
> axes only and never calls `check_gates`, so validating whether the stage-aware excuse improves
> forward returns needs a new gate-impact backtest diagnostic (cohort comparison of excused vs.
> gated negative-FCF names). The thresholds stay unfitted priors until then.

#### 2.8 `opportunity = max(momentum, value)` discards a real signal
`max()` (`scoring.py:97`) is the right call for "qualify on either axis," but when momentum
is **down** and value is **up**, that disagreement *is* the classic falling-knife /
value-trap pattern — informative — and `max()` silently takes the high side.
- **Plug:** keep `max()` for the score, but emit a `momentum_value_divergence` flag when the
  two axes strongly disagree, so the deep dive knows to ask "value trap or mispricing?"

#### 2.9 Risk metrics computed but never scored — **SHIPPED (vol+drawdown axis)**
`realized_vol` and `max_drawdown` are bridged into `StockMetrics` (`bridge.py:91-92`) and
were explicitly marked unscored; `beta` lives in `Profile` and isn't mapped. There was no
risk overlay and no risk-adjusted ranking.
- **Plugged:** a 7th **`risk` axis** now scores `realized_vol` + `max_drawdown` as a
  **composite-only tilt** (weight 0.10, other five weights ×0.9; sector-neutral; excluded
  from the confidence/scored/coverage accounting so those stay bit-identical when it is absent).
- **Still open (deferred):** map `beta` through the bridge and add it as a third risk leg;
  **backtest the 0.10 weight and the bands** — trailing vol/drawdown peak at the bottom and
  can be anti-predictive at turning points, so the prior is unfitted and elevated for
  validation (§2.1). The volatility/drawdown **soft-gate** alternative (`DATA_SOURCES.md`
  A3) and the FRED macro regime overlay (A2) remain available.

---

## 3. Making Claude's analysis more effective (`research/`)

The qualitative layer is well-built — grounded, quote-verified, cached by accession, prompt
hardened against injection — but **narrow**.

### 3.1 Only the 10-K is read — 10-Q HALF SHIPPED
`assess.py` ingests Item 1 / 1A / 7 of a single 10-K, which can be ~11 months stale and omits
the richest qualitative sources:
- **Earnings-call transcripts** — management tone, *fresh* guidance, analyst Q&A. The single
  biggest win; it's the most current management signal available.
- **Latest 10-Q** — the current quarter the annual filing can't reflect.
- **DEF 14A proxy** — comp alignment, governance, related-party transactions (capital
  allocation red flags the 10-K won't volunteer).

> **SHIPPED — latest-10-Q half:** `research/filings.py:fetch_bundle` now returns a
> `FilingBundle` carrying the latest **10-Q's MD&A** (Part I Item 2 — extracted via
> `TenQ.get_item_with_part`, **not** the TenK-only `management_discussion` attribute;
> validated by a live-EDGAR integration test) alongside the 10-K, fed to the prompt as a
> labeled `=== LATEST 10-Q — MD&A ===` section. The brief caches on a **composite key**
> (`<10-K-acc>+<10-Q-acc>`) so a new quarter invalidates. **Still deferred:** the DEF 14A
> proxy (the `FilingBundle` leaves room to add it as a third section) and earnings-call
> transcripts (no keyless source). The prompt already treats filing text as DATA, so each
> remaining source is additive.


### 3.2 Reconcile the narrative against the numbers — ✅ SHIPPED
The original gap: `assess()` took the `ScoreCard` but never used it, so Claude couldn't say
"the screen flags this as cheap-value — here's whether the 10-K explains why the market
disagrees," or "management's growth story vs the actual 3% revenue CAGR." That reconciliation
is the most decision-useful thing the layer could produce.

> **SHIPPED — score-aware reconciliation + 5y series:** `assess.py` builds a
> `=== QUANT CONTEXT (screener facts; NOT from the filing) ===` block from the `ScoreCard`
> (sub-scores, composite, confidence, sector, gates, flags) and the derived fundamental
> scalars, and asks Claude for an explicit `reconciliation` (confirms/contradicts/silent,
> each grounded in a verbatim filing quote) plus a falsifiable bull/bear thesis (`9e833b9`,
> §3.3). The remaining half — the raw **5-year financial series** (revenue / gross-profit /
> net-income / OCF / FCF / diluted-EPS / total-debt / diluted-shares, newest-first) — is now
> threaded too: `data/bridge.py:_financial_series` collects it from the harness `Statements`
> into `StockMetrics.financial_series` (a **scorer-inert** list-of-dicts, pinned
> byte-identical by a scoring-invariance test), and
> `assess._render_series` renders it as a compact $M table inside the QUANT CONTEXT block so
> reconciliation can weigh the **trajectory** (does a single CAGR mask a recent decline, a
> one-off spike, or NI-vs-cash-flow divergence), not just a point value. The QUANT CONTEXT is
> excluded from `bundle.haystack()`, so a computed number can never pass as a verified filing
> quote. `total_equity` is **consciously omitted** (only feeds `debt_to_equity`, and goes
> negative on buyback compounders — §2.7). The brief cache is keyed by filing accession, so an
> already-cached name needs `--refresh` to pick up the series.

- **Still deferred:** DEF 14A proxy + earnings-call transcripts as quant/narrative inputs (no
  keyless source — tracked in §3.1).

### 3.3 No bull / bear / pre-mortem structure — ✅ SHIPPED
Risks + red-flags + synthesis is a *summary*, not a decision aid.

> **SHIPPED (#22, `9e833b9` — alongside §3.2):** `research/models.py` gained a `Thesis`
> dataclass (`bull_case`, `bear_case`, `what_would_change_my_mind`, `takeaway` — the traveling
> TL;DR that replaced the old flat `synthesis`), carried on every `QualitativeAssessment` and
> parsed by `_thesis()` (presence is enforced there — a missing/non-dict `thesis` raises, like
> the moat dict-check; `what_would_change_my_mind` is truncated to `research.max_falsifiers`,
> default 3). The system prompt (`assess.py`) instructs Claude to build the thesis **from the
> grounded risks/red_flags/reconciliation** and introduce NO new filing facts there — so the
> quote-grounding discipline is preserved (the thesis itself carries no quotes BY DESIGN, since
> it is interpretive judgment, not a filing fact). `report.py` renders a `## Thesis (analyst
> judgment — not filing facts)` section (Bull / Bear / What would change my mind / Takeaway) and
> threads the first falsifier into the screening-call badge as a *"but watch:"* line.
- **Original plug (now done):** restructure `QualitativeAssessment` toward a falsifiable
  thesis — explicit **bull case**, **bear case**, and **"what would change my mind"** — keeping
  the quote-grounding requirement on every factual claim.

### 3.4 No year-over-year risk-factor diff — ✅ SHIPPED
Newly-*added* 10-K Item 1A risk factors are a documented alpha signal. Briefs are already
cached by accession, so last year's filing is one fetch away.

> **SHIPPED:** `research/riskdiff.py` (pure, stdlib `difflib` leaf) diffs the current 10-K's
> Item 1A against the **prior fiscal year's** (selected by `period_of_report`, `10-K/A`
> amendments excluded), surfacing only the *newly added* risk blocks. The diff is
> **deterministic** (Python finds the literally-new blocks; v1 splits on blank-line blocks and
> matches a normalized prefix — digits/currency stripped so cosmetic re-wording isn't flagged),
> and Claude then *interprets* only those blocks into a distinct `added_risks` brief section
> (`## Newly disclosed risks (vs prior year)`), each entry verbatim-quoted and grounded. The
> prior-year filing is a diff **input only** — never shown to the model and excluded from the
> grounding haystack. Bands (`similarity_threshold` 0.5, `max_blocks` 4, `max_chars`) are
> **unfitted priors** in `config.yaml: research.risk_diff`. **Deferred refinement:** dedicated
> bold-heading detection (the prefix key approximates it today).
- **Original plug (now done):** diff this year's Item 1A against the prior year's; surface
  *added* risks as a distinct, high-attention section. Low effort, high signal.

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
6. **Absolute valuation leg (§2.2), insider granularity (§2.6), risk overlay (§2.9)** — as
   capacity allows. (Bull/bear thesis §3.3 and YoY risk diff §3.4 are now ✅ SHIPPED.)

Two house rules from `CLAUDE.md` apply to every change here: route any error string that may
contain a URL through `env.py:redact_secrets()`, and keep coverage **honest** — a missing
input must lower coverage / redistribute weight, never silently zero a sub-score.

---

## 5. Competitive-feature breadcrumbs (UNVETTED — verify before building)

**These are clues, not commitments. Read this whole preamble before picking one up.**

This section records features seen in comparable products — open-source screeners
(OpenBB, FinanceToolkit) and commercial tools (GuruFocus, Zacks, Simply Wall St, Stock
Rover, Koyfin, Portfolio123, Unusual Whales) — so the ideas aren't lost. They are **not**
validated as worth building. Each line is a one-sentence prompt written from the outside,
with **zero** of the scrutiny every shipped feature in this doc was forced through.

### Trust but verify — the rule for any session that picks one of these up

Do **not** read a breadcrumb and start implementing. The EV/EBIT and reverse-DCF specs
both had their naive version **killed or de-scoped by adversarial review** (EV/EBIT's
scored leg measured `corr = 0.724` and stayed OFF; reverse-DCF was routed out of the
composite entirely). Assume yours will too. Before writing any code you MUST:

1. **Vet it adversarially.** Is there a free/keyless feed, or is it paid/absent? Does the
   signal clear a rank-IC bar (§2.1), or is it un-backtestable framing that belongs in the
   research layer only? Does it re-derive something we already have (collinearity — the
   EV/EBIT and reverse-DCF traps)? What decision does it actually change?
2. **Run the full pipeline:** `superpowers:brainstorming` → spec in
   `docs/superpowers/specs/` → **three-agent review** (implementation fact-checker /
   signal-value PM skeptic / red-team, as in the EV/EBIT §11 and reverse-DCF §12) →
   `superpowers:writing-plans` → TDD. The one-liner is a starting hypothesis, not a design.
3. **Check the cross-reference first** — most of these already have a real home below or in
   `DATA_SOURCES.md`; the breadcrumb just points there so the prior thinking isn't redone.

### Already homed (start here — don't re-spec from scratch)

| Competitive feature | Where it already lives |
|---|---|
| Reverse-DCF / fair value | **SHIPPED** research-layer line (§2.2; spec `2026-06-13-reverse-dcf-implied-growth-design.md`); EV/EBIT scored-leg measurement also in §2.2 |
| Piotroski F-score | **SHIPPED** (`DATA_SOURCES.md` D1; §2.2 value-trap refinement) |
| Altman Z (bankruptcy distance) | `DATA_SOURCES.md` D2 (proposed); §2.7 (smarter than the raw D/E gate) |
| Beneish M / accruals (earnings quality) | `DATA_SOURCES.md` D3; §2.5 accruals half (still open) |
| Estimate-revision / earnings-surprise momentum | `DATA_SOURCES.md` §2 + Tier B — needs an estimates feed (the cleaner momentum signal; Zacks' whole edge) |
| 13F institutional / smart-money flow | `DATA_SOURCES.md` C3 (free via EDGAR) |
| Congressional / gov-contract trades | `DATA_SOURCES.md` C2 (Quiver scaffold; the highest-leverage *alt-data* add) |
| Sector-relative / peer-percentile scoring | §2.3 — highest-leverage **scoring** fix; subsumes the cross-sector band misfire most absolute metrics inherit |
| Earnings-call transcripts + DEF 14A proxy | §3.1 (deferred) — **highest-leverage research-layer next step** per the reverse-DCF red-team |
| News / event tone | `DATA_SOURCES.md` A5 (GDELT); EDGAR 8-K/13D/13G/144 events already SHIPPED |
| Portfolio-level exposure/concentration | scout `_Portfolio` section SHIPPED (see `CLAUDE.md`) |

### Net-new clues (no home yet — feed AND value both unproven)

- **Options-implied signals** — IV rank, put/call skew, unusual-options flow (cf. Unusual
  Whales). *Vet:* no keyless feed known (mostly paid); likely noise for a fundamental
  pre-screen. Justify hard before building.
- **Dividend-safety / coverage** — payout ratio vs FCF, streak, cut-risk (cf. Simply Wall
  St, Stock Rover). *Vet:* derivable from existing statements (no feed), but only relevant
  to an income tilt the screener doesn't currently have — scope the use-case first.
- **Backtested portfolio simulation with turnover + transaction costs** — a real equity
  curve net of costs, not just rank IC (cf. Portfolio123). *Vet:* this is the genuine
  "does the composite make money" test and the most valuable net-new item — but it is
  largely the §2.1 Phase-2 composite work, **blocked on snapshot-accumulation history**,
  not a new feature. Pursue via §2.1, not as a bolt-on.

**Strategic steer (from the reverse-DCF red-team):** before reaching for any new *signal*,
the two highest-leverage items already in this doc are **§2.3 sector-relative scoring** and
**§3.1 earnings-call transcripts**. A new feature should justify why it outranks those two.
