# Absolute valuation leg — EV/EBIT earnings yield (§2.2) — design

**Date:** 2026-06-13
**Status:** Draft — REVISED per three-agent review (see §11). Production leg
de-scoped to a measurement-first slice; full enablement gated behind §2.3.
**Author:** brainstormed with Claude
**Closes:** `docs/ASSESSMENT_GAPS.md` §2.2 "still open — absolute-multiple half"

## 1. Goal

Add a genuine **absolute** valuation leg to the `value` sub-score so the axis is
not purely relative/sentiment-anchored. Today three of four value legs are
relative (`upside_to_target` = sell-side target, `pe_vs_history` = own 5y median,
`peg`) and only `fcf_yield` is absolute. The relative legs share a structural
failure: a de-rating business looks "cheap vs its own history" forever, with no
absolute floor to catch "cheap for a reason."

The new leg is the **EBIT/EV earnings yield** (Greenblatt "Magic Formula"
cheapness; higher = cheaper = better), scored like `fcf_yield`. It is chosen over
the alternatives for signal reasons set out in §3.

### Why EV/EBIT specifically (the PM case)

- **Strongest single-factor value record.** In long-horizon factor work
  (O'Shaughnessy, Greenblatt) EBIT/EV repeatedly beats P/E and P/B — the EV
  denominator is **capital-structure-neutral**, so levered and unlevered versions
  of the same business get comparable valuations.
- **Orthogonal to the existing `fcf_yield`.** Different numerator (accrual
  operating profit vs. post-everything cash) and denominator (whole-firm EV vs.
  equity market cap). Where they diverge is the new information: a capex-heavy
  name has thin FCF yield but can screen on EV/EBIT; a debt-levered name has a
  flattering *equity* FCF yield while EV/EBIT exposes the debt.
- **Charges for capital intensity → catches value traps.** EBIT is *after* D&A
  (a crude maintenance-capex charge), so a structurally declining
  capital-intensive business shows a genuinely low EBIT yield as EBIT erodes — an
  absolute floor the relative legs lack. This is the §2.2 failure mode by name.

### Rejected alternatives

- **EV/EBITDA** — capex-blind (the wrong blindness for a quality-tilted screen;
  flatters capital-intensive rollups) and *more* collinear with what we already
  have (EBITDA ≈ cash proxy → `fcf_yield`, and EBITDA-based `net_debt_to_ebitda`
  already drives the `over_leveraged` gate).
- **EV/FCF** — collinear with the existing absolute `fcf_yield` (same numerator
  family); rejected in §2.2 already.
- **Reverse-DCF** — assumption-heavy (discount rate, fade, terminal growth — each
  a free knob), hard to backtest cleanly, doesn't slot into the `_norm` band
  machinery. Belongs in the **research/brief layer** (a "the price implies X%
  growth — is that beatable?" line, §3.3), not as a scored scalar leg.

## 2. Non-goals (YAGNI)

- **No new data feed.** Every input (EBIT = operating income, total debt, cash,
  market cap) is already extracted on **both** the harness and the XBRL backtest
  panel. The doc's note "EV/EBIT needs a `cash` concept added to the XBRL panel"
  is **stale** — the `net_debt_to_ebitda` axis added `cash` after that note
  (`_xbrl_facts.py:203,319`).
- **No on-by-default behavior change.** The production leg ships **commented out**
  (OFF). When absent, the scorer is **byte-identical** to today (regression-pinned,
  mirroring `test_gate_backcompat.py` and the dilution-off guard).
- **No weight fitting / no `config.yaml` edits to weights.** Bands are unfitted
  priors; the backtest *measures* the leg, it does not auto-tune it.
- **No reverse-DCF, no sector recalibration of other legs, no EV/EBITDA variant.**
- **No change to `value_score()` function-form math in the backtest baseline** —
  see §4.1 (the validated `value` average is structurally protected).

## 3. Metric definition

```
EBIT       = operating income (latest fiscal year)
net_debt   = total_debt − cash_and_equivalents          (signed; net cash ⇒ negative)
EV         = market_cap + net_debt
ebit_ev_yield = EBIT / EV                                (higher = cheaper = better)
```

New field `StockMetrics.ebit_ev_yield: Optional[float]`. We store and surface the
**yield** (not the EV/EBIT ratio) so it is band-consistent with `fcf_yield`;
EV/EBIT = `1 / ebit_ev_yield` for any display that wants the familiar ratio.

**Two PM abstention guards** — the field stays `None` (never a misleading score),
so `_eval_subscore` redistributes weight honestly:

1. **EBIT ≤ 0 → None.** A loss-making firm is judged by growth/quality, not by a
   "valuation" of negative earnings. (Without this, EBIT<0 with EV>0 yields a
   negative yield that floors the band — conflating *unprofitable* with
   *expensive*.)
2. **EV ≤ 0 → None.** Net-cash-exceeds-market-cap artifact; mirrors the D/E
   `dte_artifact_ceiling` abstention in the `over_leveraged` gate.

Edge cases covered by tests: `market_cap` missing, `cash`/`debt` missing (net_debt
falls back to debt-only or abstains — see §6), exactly-zero EBIT/EV.

## 4. Architecture

Three independently testable changes plus shared derivation. Build order is
**derivation → backtest attribution → production leg** (the doc requires the
attribution to exist *before* the leg can ship).

### 4.0 Shared derivation (two sites, one formula)

- **Harness** — `data/bridge.py` (~line 181, immediately after the
  `net_debt_to_ebitda` derivation): `oi0` (EBIT), `debt0`, `m.cash_and_equivalents`,
  `m.market_cap` are all already in local scope. Compute `m.ebit_ev_yield` with the
  two guards. Keep an `if m.ebit_ev_yield is None` priority guard for symmetry with
  the other legs (no current source provides it, so this is the sole producer).
- **Backtest** — `providers/_xbrl_facts.py:panel_to_metrics` (~line 320, beside the
  existing `net_debt = total_debt − cash` block): same formula from
  `p.operating_income` (latest), `p.total_debt`, `m.cash_and_equivalents`,
  `m.market_cap`, point-in-time. No new XBRL concept.

A single pure helper is preferred to avoid drift, e.g.
`scoring.ebit_ev_yield_from(ebit, market_cap, total_debt, cash) -> Optional[float]`
(or a small leaf in `bridge`/`_xbrl_facts` if a scoring-module dependency is
unwanted). Both call sites use it so the formula + guards live in exactly one place.

### 4.1 Backtest attribution (built FIRST)

The backtest's combined `value` axis is `scoring.value_score(m, t)` (the 4-leg
function-form). **It is left untouched.** Because the new leg is *not* folded into
it, the validated `value` average **cannot be silently degraded** by this work —
the protection is structural, not a promise. Per-leg attribution comes from new
**standalone single-leg axes** that reuse the existing per-axis IC machinery (the
`share_count` / `net_debt_to_ebitda` precedent — zero new report code):

- `scoring.ebit_ev_yield_score(m, t)` — single leg → 0–100, backtest-only (NOT a
  production sub-score), mirroring `share_count_score` / `net_debt_to_ebitda_score`.
- `scoring.fcf_yield_score(m, t)` and `scoring.pe_vs_history_score(m, t)` — expose
  the two XBRL-reconstructable existing value legs the same way.
- Add `"ebit_ev_yield"`, `"value_fcf_yield"`, `"value_pe_vs_history"` to
  `backtest/signals.py:XbrlSignalSource._AXES`.

The report then shows each value leg's standalone rank IC beside the combined
`value` IC, so a reader sees whether EV/EBIT's IC is **additive or dilutive**
relative to the legs already in the average — *before* the production leg is ever
enabled. This is the "per-leg `value`-IC attribution" §2.2 demands.

(`peg` / `upside_to_target` are not XBRL-reconstructable — they need analyst data
absent from companyfacts — so per-leg attribution covers the three reconstructable
legs. This is the same documented XBRL limitation that already applies to the
`value` axis.)

### 4.2 Production scoring leg (gated OFF, byte-identical when absent)

- Thread config into `_value_legs(m, config)` (mirrors `_quality_legs(m, config)` /
  `_growth_legs(m, config)`); update the one call site `score()` line 424.
- Append the EV/EBIT-yield leg gated by `_value_ev_on(config)` (mirrors
  `_dilution_on`) **and** a `"ebit_ev_yield" in thresholds` guard (None-safe if a
  config enables the block without the band). `None` signal ⇒ `_eval_subscore`
  redistributes — honest coverage preserved.
- `value_score(m, t)` function-form is **left as the 4-leg baseline** (it is the
  backtest baseline and has no `config` parameter). The intentional, documented
  divergence: production `_value_legs` can carry 5 legs when enabled; the function
  carries 4. The backtest measures the 5th leg via the standalone axis, never by
  folding it into `value_score`.

### 4.3 Config (`config.yaml`)

- `thresholds.ebit_ev_yield: [0.04, 0.12]` — unfitted prior. ~4% earnings yield →
  0, ~12%+ → 100 (Greenblatt-cheap territory). Labeled unfitted; the backtest
  emits its standalone IC.
- New `value.ev_ebit: {enabled: true}` block shipped **commented out** (OFF),
  mirroring the `quality.dilution` block. Document byte-identical-when-absent.
- Add `ebit_ev_yield` to `sectors.masked_legs` for financials / insurers / REITs
  (EV/EBIT is undefined for banks). Inert while the leg is off (a masked entry for
  a leg that isn't produced is a no-op); correct once enabled.

### 4.4 Surfacing

- `ebit_ev_yield` into JSON and CSV output beside `fcf_yield` / `share_count_cagr`
  (`screen.py`). Display-floored / rounded consistently with the other yields.

## 5. Data flow

```
EDGAR 10-K (operating income, debt, cash)  ┐
Finnhub/Yahoo/FMP (market cap)             ┘→ bridge.snapshot_to_metrics
                                              → m.ebit_ev_yield  (guards: EBIT>0, EV>0)
                                              → _value_legs(m, config)  [if value.ev_ebit ON]
                                              → value sub-score → composite
                                              → JSON/CSV surfacing

SEC companyfacts (point-in-time)           → _xbrl_facts.panel_to_metrics
                                              → m.ebit_ev_yield
                                              → scoring.ebit_ev_yield_score  → XBRL backtest IC
```

## 6. Error handling & honesty

- Every missing input ⇒ `m.ebit_ev_yield = None` ⇒ leg excluded, weight
  redistributed (never silently zeroed). Matches the house rule.
- `net_debt` when `cash` is missing: use `total_debt` alone (conservative — higher
  EV, lower yield) only if `total_debt` is present; if both debt and cash are
  missing, EV reduces to `market_cap` (an upper bound on yield) — **decision in §8
  open question O1**: default to abstaining unless at least debt OR cash is known,
  to avoid a market-cap-only EV that silently ignores leverage.
- No error string handling needed (pure arithmetic; no new HTTP path), so the
  `redact_secrets` rule is not triggered.

## 7. Testing (TDD)

- **Byte-identical regression:** scorer output unchanged when `value.ev_ebit` is
  absent (mirrors `test_gate_backcompat.py`). The strongest single guard.
- **Leg fires when enabled:** EBIT>0/EV>0 with a cheap yield raises `value`; an
  expensive yield lowers it; band endpoints clamp.
- **Abstention guards:** EBIT≤0 → None; EV≤0 → None; each ⇒ redistribution, not a
  zero.
- **Derivation correctness:** `EV = market_cap + debt − cash`, `yield = EBIT/EV`,
  on both the bridge and the XBRL panel (one shared helper → one test + two
  call-site smoke tests).
- **Backtest axes emit:** `ebit_ev_yield`, `value_fcf_yield`, `value_pe_vs_history`
  appear in an `XbrlSignalSource` observation; a live XBRL test on a known name if
  the existing live-test pattern fits.
- **Financials masking:** with the leg enabled, a financial-bucket name abstains
  `ebit_ev_yield`.

## 8. Open questions

- **O1 — missing-leverage fallback.** When `total_debt` and `cash` are both
  missing, abstain (recommended, conservative) or fall back to `EV = market_cap`
  (treats the firm as unlevered)? Recommendation: **abstain** — a market-cap-only
  EV silently drops the whole point of using EV. Confirm in review.
- **O2 — band prior.** `[0.04, 0.12]` is a first guess. The backtest's standalone
  IC + quantile spread should inform a revision before anyone turns the leg on.
- **O3 — `ebitda`-margin-style usability floor.** The `over_leveraged` gate gates
  EBITDA usability on a margin floor. Should EV/EBIT gate EBIT on an
  operating-margin floor (drop near-zero-margin EBIT as noise)? Likely **no** for
  v1 (the EBIT>0 guard suffices; a margin floor is a tunable refinement), but flag
  it.

## 9. Build order (honors "backtest first")

1. Shared derivation helper + metric field + bridge + XBRL-panel wiring (+ tests).
2. Backtest attribution: `ebit_ev_yield_score` / `fcf_yield_score` /
   `pe_vs_history_score` + `_AXES` entries (+ tests). **Measurable here.**
3. Production gated leg: `_value_ev_on`, `_value_legs(m, config)`, config block,
   masking, surfacing (+ byte-identical regression + fires-when-on tests).

## 10. Is this providing value? (honest self-assessment)

**The case for:** the value axis's biggest documented weakness is that it is
mostly relative/sentiment-anchored with one absolute leg; EV/EBIT is the
best-evidenced absolute value factor and is genuinely orthogonal to that one leg.
The cost is low (no new feed, ~tens of lines + tests), the risk is near-zero
(ships OFF, byte-identical), and the work is *measurable* before it is trusted.

**The case against / honest caveats (for the reviewers to stress):**

- **Collinearity is asserted, not yet measured.** "Orthogonal to `fcf_yield`" is a
  prior. If their cross-sectional correlation is high, the leg adds cost without
  signal. → Mitigation: the per-leg attribution exists precisely to measure this;
  the leg stays OFF until it clears.
- **Large-cap survivorship-biased XBRL set is the wrong universe** for a value
  factor (same caveat that sank the Piotroski axis IC). EV/EBIT's edge is
  historically strongest in small/mid-cap. The backtest may show ~0 IC here and
  *still* be worth shipping-gated — but we must not over-read a null.
- **Single-year EBIT is noisy / cyclical.** One trough/peak year distorts the
  yield. v1 uses latest-year EBIT for simplicity; a normalized (multi-year mean)
  EBIT is a possible refinement, noted not built.
- **Does it change any ranking decision?** With default weights (value 0.22 split
  across 4–5 legs), one new leg moves the composite only marginally. The honest
  value is **trap-avoidance at the tails** (catching de-raters the relative legs
  miss), not broad re-ranking. Reviewers should confirm this is worth the
  surface-area.

**Disposition:** ship the derivation + backtest attribution regardless (pure
measurement infrastructure, always useful); ship the production leg **OFF**; only
advocate turning it on after the standalone IC + collinearity read justify it.

## 11. Three-agent review findings (2026-06-13)

Reviewed by three independent agents: an implementation fact-checker, a
signal-value PM skeptic, and an adversarial red-team. Verdicts:

- **Implementation:** IMPLEMENTABLE-AS-SPEC. Every technical claim verified true
  (locals in scope at `bridge.py:165-181`; XBRL panel already has `cash` at
  `_xbrl_facts.py:203,319` — "no new feed" holds; `_value_legs` has no config param;
  `value_score()` is backtest-only; gating/masking/byte-identical patterns all
  precedented). **Two fixes required:** (1) surfacing reads the *ScoreCard*, not
  metrics — so it needs a new `ScoreCard.ebit_ev_yield` field (`models.py:145`) + a
  copy line in `ScoreCard(...)` (`scoring.py:~534`), which §4.4 omitted; (2) the
  byte-identical regression mirror is `test_scoring.py:test_dilution_leg_absent_is_byte_identical`.

- **Signal-value PM:** SHIP-BUT-CHANGE — ship §4.0+§4.1 (derivation + standalone
  axes) ONLY; do **not** build the production leg (§4.2/4.3). EBIT/EV vs
  FCF/market-cap rank-correlate **~0.6–0.8**; folding a correlated 5th leg into an
  unweighted average **double-weights the crowded cash-earnings-yield factor and
  mutes the orthogonal legs** (dilutive by construction). Single-year EBIT is
  anti-predictive at turning points for the cyclicals the leg targets. Net
  re-ranking ≈ 0 (value 0.22 ÷ 5 ≈ 4.4% correlated weight). **Measure the
  cross-sectional rank correlation first; if >0.5 on this universe, the production
  half is permanently DON'T-SHIP.**

- **Red-team:** NOT a sensible direction *right now*. Top objections: (1) **wrong
  priority** — §4 ranks EV/EBIT last; §2.3 sector-relative scoring is higher-leverage
  *and a prerequisite* (this leg's absolute band misfires across sectors, the disease
  §2.3 cures); (2) **un-enable-able by design** — the 4-leg/5-leg divergence means the
  standalone-axis IC never measures the *production* combined-average IC, and a null
  is pre-excused, so the OFF leg has no honest path to ON (measurement theater); (3)
  **noisy/misaligned numerator** — single-year EBIT (income flow) over a
  balance-sheet-instant net-debt, alignment asserted not proven. Recommendation:
  **build §2.3 first.**

### Resolution (revised plan)

1. **Measurement-design fix (addresses the central flaw).** Standalone-axis IC
   cannot answer "additive-or-dilutive *to the value average*." So the backtest must
   also emit a **`value_plus_evebit` axis** = the combined value average *with* the
   EV/EBIT leg folded in, alongside the existing 4-leg `value` axis and a
   **`corr(ebit_ev_yield, fcf_yield)`** cross-sectional statistic. The enable rule
   becomes concrete: turn the production leg ON only if `value_plus_evebit` IC >
   `value` IC **and** the leg correlation is materially < 0.5.

2. **De-scope to measurement-first.** Build §4.0 (derivation, with the
   date-alignment guard below) + §4.1 (standalone axes) + the `value_plus_evebit`
   and correlation diagnostics. **Do NOT build §4.2/§4.3 (the production gated leg)**
   in this pass — it ships only if the diagnostics clear, and ideally after §2.3
   gives it a sector-relative home.

3. **Date-alignment guard (new).** EBIT (income-statement FY flow) and net_debt
   (balance-sheet instant) must come from the same fiscal period. Abstain
   (`ebit_ev_yield = None`) when the operating-income period end and the balance-sheet
   instant date disagree by more than a quarter, rather than blindly pairing `[0]`
   with `[0]`.

4. **Surfacing fix.** Add `ScoreCard.ebit_ev_yield` + the copy line per the
   implementation review (only when/if the production leg is built).
```
