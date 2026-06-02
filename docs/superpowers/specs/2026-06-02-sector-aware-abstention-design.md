# Sector-Aware Applicability & Abstention — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorm checkpoint A); pending spec + plan review
**Worktree/branch:** `worktree-sector-aware-abstention`

## 1. Problem

The scorer is equity-centric and silently produces misleading composites for
businesses whose metrics don't apply. Worked example **SCHW** (Charles Schwab,
a broker) screens at composite 52.7 for the *wrong* reasons:

- `over_leveraged` gate fires on deposit funding that is normal for a financial
  (false-positive hard filter — `scoring.py:check_gates` is not sector-aware).
- Quality/Moat legs (gross margin, ROIC, interest coverage, debt/equity) are
  **undefined or inverted** for a brokerage. `_avg` does not abstain — it
  silently drops the undefined legs and averages the survivors, which can read
  misleadingly high *or* low.
- Its top sub-score (Opportunity 77.9, all "value") is driven by an FCF-yield
  computed from a bank's wildly volatile operating cash flow. FCF-yield is not a
  valid cheapness signal for a financial.

The composite pretends SCHW is comparable to operating companies when the model
literally cannot judge it. The same failure mode hits REITs (FCF understates
owner earnings; FFO/AFFO is the real metric) and insurers (leverage structurally
high, interest-coverage/gross-margin undefined).

## 2. Goal & non-goals

**Goal (this deliverable): never mislead.** The model must never print a
confident-looking composite built from structurally-inapplicable legs. Where a
metric does not apply to a sector, the leg **abstains** (explicitly, not silently
drops), and the sub-score / composite / gates reflect that honestly.

**Decided during brainstorming:**

- Abstain trigger = **applicability map + missing data** (a leg abstains if it is
  marked inapplicable for the detected sector *or* its input is `None`).
- Sector detection = a **single shared resolver fed by SEC SIC** (keyless, in both
  default provider chains), so both engines produce an identical bucket — including
  `unknown` — for any ticker. This is the hard "two stacks must not diverge"
  constraint made structural.
- Top-level behavior = **mark not-scored, keep the number for audit** (mirrors the
  existing gate pattern: composite still computed from surviving valid legs;
  `passed` reflects the new state). `composite` stays a `float` — no type break.
- v1 applicability maps ship for **financials (banks/brokers), REITs, insurers**.
- **Gate applicability masking is in scope** (disable `negative_fcf` /
  `over_leveraged` for these sectors).

**Non-goals (explicitly deferred):**

- **No new sector thresholds / calibration (mode 2).** We *mask* inapplicable
  legs; we do **not** recalibrate the bands of legs that survive. A bank's ROE is
  still scored on the global `roe` band. Calibration (NIM/ROTCE/efficiency-ratio
  bands, software gross-margin bands, capital-light FCF-yield re-banding) is a
  separate, validation-gated future phase. This avoids repeating the repo's
  "defensible prior, not fitted" trap with invented per-sector numbers we cannot
  validate today.
- **No cross-sector comparability rework.** Because we add no fitted thresholds,
  the backtest's pooled rank-IC is unaffected. Within-sector ranking / percentile
  normalization belongs to the calibration phase.
- **Claude stays out of the scoring path.** Sector detection and applicability are
  deterministic (SIC + config). Claude remains the labeled, non-scoring research
  overlay. (Pressure-tested: using Claude to detect sector or decide applicability
  was rejected — it would inject a non-reproducible, non-backtestable component
  into ranking math.)
- **Software / capital-light names are not addressed here.** Their legs are
  *applicable*; their problem is miscalibrated bands (mode 2) → deferred.

## 3. Honest expectation (what SCHW actually becomes)

This is **not** a dramatic "NOT RANKED" for SCHW. After masking the misleading
legs (FCF-yield, debt/equity, interest-coverage, gross-margin) and dropping the
`over_leveraged` false-positive gate, SCHW **still retains genuinely-applicable,
sector-neutral signal**: momentum, insider, growth, and ROE/net-margin quality.

Expected v1 outcome for SCHW: **scored, reduced-confidence, `over_leveraged`
removed, moat (and likely value) abstained** — a *better and more honest* number,
with the false-positive gate gone. Full `not_scored` is the **backstop** for names
where too little valid signal survives (a financial that is also data-starved).

The masking + a `confidence` signal do most of the "never mislead" work; the
validity floor is the safety net, expected to fire rarely.

## 4. Architecture

A new **applicability resolution** concern sits between merge and score. Crucially,
both engines already converge on a single call — `score(metrics, config)`
(`screen.run` line ~45 and `run_harness` line ~66) — so if the resolved sector
rides on `StockMetrics` and `score()` applies the config map, **there is exactly
one scoring code path and the two stacks cannot diverge.**

### 4.1 New module: `src/shortlist/sectors.py`

Pure, dependency-free leaf (pattern: `providers/_form4.py`). One public function:

```python
def resolve_bucket(sic: Optional[str | int], config: dict) -> str:
    """Map a SEC SIC code to a canonical applicability bucket
    ('financials' | 'reit' | 'insurer' | 'unknown') via config sic_ranges.
    None / unmatched SIC -> 'unknown'. Deterministic, no I/O."""
```

Helper predicates also live here, keyed off the resolved bucket + config, so
`scoring.py` stays thin and the applicability policy has one home:

```python
def leg_applicable(bucket: str, leg: str, config: dict) -> bool
def gate_applicable(bucket: str, gate: str, config: dict) -> bool
```

### 4.2 SIC acquisition (keyless, symmetric, ~zero extra cost)

Add `sic: Optional[str] = None` to `StockMetrics` (and carry it on the harness
`Profile`/snapshot so the bridge can copy it).

- **Screener** `EdgarProvider.fetch` already builds `Company(ticker)` — set
  `m.sic = str(company.sic)` from the metadata edgartools has already loaded. No
  new HTTP request.
- **Harness** `EdgarSource` builds `Company` too; surface SIC onto the profile so
  `bridge.snapshot_to_metrics` copies `m.sic`.
- EDGAR is in both default chains. If EDGAR is dropped from a run, `m.sic` is
  `None` on **both** stacks → `unknown` → symmetric. Foreign issuers (20-F) with
  no SIC → `unknown` symmetrically.

`resolve_bucket` is called inside `score()` from `m.sic` (no network in `score`),
so screener and harness apply identical logic. (Optionally also stash the resolved
bucket on the card for output — see §7.)

### 4.3 Scoring changes (`src/shortlist/scoring.py`)

Refactor the per-sub-score helpers to operate over **named, typed legs** instead
of an anonymous list, so applicability can be applied per leg. Sketch:

```python
# Each sub-score declares its legs as (leg_name, raw_value, threshold_key).
# A leg CONTRIBUTES iff: gate_applicable(...)  AND  value is not None.
# Partition applicable-legs into present vs missing; abstain the sub-score when
# too few applicable legs are present.

def _score_subscore(bucket, legs, t, config) -> tuple[Optional[float], list[Abstention]]:
    applicable = [lg for lg in legs if leg_applicable(bucket, lg.name, config)]
    present    = [lg for lg in applicable if lg.value is not None]
    if not applicable:
        return None, [<all inapplicable>]
    frac = len(present) / len(applicable)
    if frac < config["validity"]["min_valid_leg_fraction"]:
        return None, [<abstain: insufficient valid legs>]
    return mean(_norm(lg.value, *t[lg.tkey]) for lg in present), [<per-leg reasons>]
```

This replaces `_avg`'s silent-drop with an explicit partition. `unknown` bucket =
no legs inapplicable → behavior reduces to **today's** for present legs, *plus*
the new `min_valid_leg_fraction` floor (so even unknown-sector names stop letting
"1 survivor of 4" masquerade). The `min_valid_leg_fraction` default is chosen so
the unknown-sector path is a no-op for fully-populated names (see §6 acceptance).

**Composite validity floor.** After computing the five components and the existing
weight-redistribution, define:

```python
confidence = (sum of weights of present sub-scores) / (sum of weights of
             sub-scores that are APPLICABLE for this bucket)
scored = confidence >= config["validity"]["min_scored_weight"]
```

Denominator uses *applicable* sub-score weight, not all weight, so a sector that
legitimately has fewer applicable sub-scores is not unfairly penalised. `composite`
is still computed from present components (unchanged math).

**Gate masking.** `check_gates` skips any gate where
`not gate_applicable(bucket, gate, config)`.

### 4.4 `passed` semantics

```python
@property
def passed(self) -> bool:
    return not self.gates and self.scored
```

A monotonic, safe tightening: a not-scored name can never pass or rank. `scored`
defaults `True`, so the change is a no-op for every currently-passing name whose
`confidence` clears the floor.

## 5. Config schema (`config.yaml`)

New top-level blocks. Default-inheritance rule: a bucket inherits the global
`thresholds`/`weights`/`gates`; it only *removes* legs/gates via its lists (v1
adds no overrides — masking only).

```yaml
sectors:
  # SIC -> bucket. Ranges are inclusive [lo, hi]. First match wins; order matters
  # (reit 6798 must be tested before the broad 6000-6799 financials range).
  buckets:
    reit:
      sic_ranges: [[6798, 6798]]
      inapplicable_legs:  [gross_margin, gross_margin_stability, fcf_yield, net_margin, interest_coverage, debt_to_equity]
      inapplicable_gates: [negative_fcf, over_leveraged]
    insurer:
      sic_ranges: [[6300, 6411]]
      inapplicable_legs:  [gross_margin, gross_margin_stability, fcf_yield, interest_coverage, debt_to_equity]
      inapplicable_gates: [negative_fcf, over_leveraged]
    financials:
      sic_ranges: [[6000, 6299], [6500, 6799]]   # banks, brokers, holdings (ex-6798 reit, ex-insurer)
      inapplicable_legs:  [gross_margin, gross_margin_stability, fcf_yield, interest_coverage, debt_to_equity]
      inapplicable_gates: [negative_fcf, over_leveraged]

validity:
  min_valid_leg_fraction: 0.5   # a sub-score needs >= half its APPLICABLE legs present, else abstain
  min_scored_weight:      0.5   # composite is 'scored' only if present applicable-sub-score weight >= this
```

(Exact SIC range partitioning of financials vs reit vs insurer is finalized in the
plan against SEC SIC tables; `roic` is intentionally *not* masked for financials in
v1 — see §8 open item.)

All thresholds config-driven — no hardcoded sector logic in code. The maps are pure
data; `sectors.py` is the only interpreter.

## 6. Testing & validation (TDD)

Abstention requires **no forward-return validation to be an improvement** — not
scoring a thing we cannot measure is provably more honest. So acceptance is
behavioral, not statistical:

**Unit (`sectors.py`):** SIC→bucket boundaries (6798→reit; 6021→financials;
6311→insurer; None→unknown; 7372 software→unknown), range ordering.

**Unit (`scoring.py`):**
- Per-leg abstention: a financial with present-but-inapplicable `fcf_yield` does
  **not** include it in `value`; `value` abstains when all its applicable legs are
  inapplicable/missing.
- `min_valid_leg_fraction`: 1-of-4 present → abstain; 2-of-4 → score.
- Composite floor: confidence below floor → `scored=False`, `passed=False`,
  composite still a float.
- Gate masking: `over_leveraged` not in `gates` for a financial with high D/E.
- **Unknown-sector no-op:** a fully-populated operating company scores **bit-
  identically** to pre-change (guards against regressions on AAPL/MSFT/LMT).

**Two-stack parity:** the *same* `StockMetrics`/`sic` scored via the screener path
and via a bridged harness `TickerSnapshot` yield identical `scored`/`composite`/
`gates`/abstentions. This is the divergence-landmine regression test.

**Golden worked example (SCHW):** assert the *direction* — `over_leveraged` gone,
`value`/`moat` abstained, `confidence < 1.0`, composite still emitted — using mock
metrics (no live keys), so it runs in CI.

**Full suite** (`uv run pytest`) stays green; the unknown-sector no-op test is the
back-compat guard for scout/`/run`/backtest.

## 7. Output contract (`ScoreCard` / `--json`)

Additive only (no removed/renamed fields → scout & `/run` keep working):

- `ScoreCard.sector_bucket: Optional[str]` — resolved bucket (`None`/`"unknown"`).
- `ScoreCard.confidence: float` — present-applicable-weight fraction (1.0 = fully
  scored).
- `ScoreCard.scored: bool` — `confidence >= min_scored_weight`.
- `ScoreCard.abstentions: list` — diagnostic, parallel to `coverage`: per leg /
  sub-score, `{name, reason: "inapplicable" | "missing"}`. Emitted in `--json`
  only when non-empty (same convention as `coverage`), and summarized on stderr.

Sub-score fields stay `Optional[float]`; `None` now means "abstained or no inputs,"
with the *why* in `abstentions` (keeps the existing null-vs-number semantics).
`composite` stays `float`. The only semantic change is `passed` now also requires
`scored` — documented and covered by the no-op regression test.

## 8. Open items to finalize in the plan

1. **SIC partition** of financials/reit/insurer against the authoritative SEC SIC
   list (6022 state banks, 6311 life insurance, 6798 REIT, 6770 blank-check, etc.).
2. **`roic` for financials:** keep applicable (banks do report returns on capital)
   or mask? Default v1: keep applicable (conservative — masking only the clearly
   undefined legs). Revisit if it reads spuriously.
3. **`min_valid_leg_fraction` exact default** vs the unknown-sector no-op guarantee
   for partially-covered operating companies (gated-FMP names already drop value
   legs — ensure the floor doesn't newly abstain names that score today). May need
   the floor to apply only when a bucket is known, with unknown using `>0` (any
   present leg). To be resolved with a coverage census in the plan.
4. **Harness `Profile` SIC plumbing** — add field vs reuse; confirm `edgar` lib
   exposes `Company.sic` cheaply on both call sites.

## 9. Files touched (anticipated)

- **new** `src/shortlist/sectors.py` — resolver + applicability predicates.
- `src/shortlist/models.py` — `StockMetrics.sic`; `ScoreCard` new fields + `passed`.
- `src/shortlist/scoring.py` — named-leg refactor, abstention, floor, gate masking.
- `src/shortlist/providers/edgar.py` — set `m.sic`.
- `src/shortlist/data/models.py` + `src/shortlist/data/bridge.py` — Profile SIC →
  `m.sic`.
- `src/shortlist/data/sources.py` (EdgarSource) — surface SIC.
- `config.yaml` — `sectors`, `validity` blocks.
- `coverage.py` / CLI `--json` + stderr — emit `abstentions`.
- `tests/` — new `test_sectors.py`, extend `test_scoring.py`, parity test.
- Docs: `CLAUDE.md` (sector-aware section), `README.md`/`HARNESS.md` notes.
```
