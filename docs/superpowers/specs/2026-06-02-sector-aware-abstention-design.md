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

This is **not** a dramatic "NOT RANKED" for SCHW. After masking the structurally
undefined/invalid legs and dropping the `over_leveraged` false-positive gate, SCHW
**still retains genuinely-applicable, sector-neutral signal**: momentum, insider,
growth, and ROE/net-margin quality.

Expected v1 outcome for SCHW: **`over_leveraged` removed; `moat` abstained**
(all three moat legs — gross_margin, gross_margin_stability, roic — are masked for
financials, so moat has zero applicable legs → it genuinely abstains, matching the
intent); **`value` abstained** (fcf_yield masked, and PEG/upside need FMP which
gates SCHW); **`quality` scored on roe+net_margin**; **opportunity = momentum**
(value gone); insider/growth scored where data present. Net: a *better and more
honest* composite with the false-positive gate gone, moat correctly silent.

Whether the composite is `scored` or `not_scored` depends on the **validity floor
applied over APPLICABLE sub-scores** (§4.3). SCHW's applicable sub-scores
(quality, growth, opportunity, insider — moat excluded as inapplicable) are mostly
present → expected **`scored`, with `confidence` reflecting data completeness over
applicable components**. Full `not_scored` is the **backstop** for a financial that
is *also* data-starved.

The masking + the `confidence`/`abstentions` signals do most of the "never mislead"
work; the validity floor is the safety net, expected to fire rarely.

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

Add `sic: Optional[str] = None` to `StockMetrics`. A **shared, exception-swallowing
extractor** (in `sectors.py`) is used by both call sites so a SIC lookup can never
regress an otherwise-successful fetch and both stacks normalize identically:

```python
def extract_sic(company) -> Optional[str]:
    """Best-effort SIC off an edgartools Company; returns a 4-digit string or None.
    Swallows all exceptions; coerces missing/empty/'None'/non-numeric -> None."""
```

- **Screener** `EdgarProvider.fetch` already builds `Company(ticker)` (edgar.py:39)
  — set `m.sic = extract_sic(company)` from metadata edgartools has already loaded
  (no new HTTP request) and **add `sic` to the `_tag(...)` call** (edgar.py:46)
  so merge's `contributed`/coverage audit sees it. The lookup must not raise out of
  `fetch` — `extract_sic` guarantees this (the screener `EdgarProvider.fetch`, unlike
  the harness, has no surrounding try/except).
- **Harness** `EdgarSource` emits **no `Profile` today** (only FMP sources.py:93 and
  Finnhub sources.py:229 do; `_FLAT` merges profile field-by-field). So EdgarSource
  must emit a **partial `Profile(sic=…)`** (all other fields `None`). `_merge_flat`
  picks each field from the highest-priority source that has it non-`None`, so SIC
  flows from EdgarSource's partial profile **regardless of whether FMP/Finnhub
  supplied a profile** (critical: FMP-gated financials like SCHW are exactly where
  FMP's profile is absent). `bridge.snapshot_to_metrics` then copies
  `m.sic = p.sic`. Add `sic` to `Profile` (data/models.py). **Do NOT** make SIC
  depend on FMP/Finnhub Profile presence.
- **Two-stack symmetry & contingency:** `resolve_bucket` is called inside `score()`
  from `m.sic` (no network in `score`), so both engines apply identical logic. SIC
  is sourced **only** from EDGAR on both stacks — never from the free-text
  `Profile.sector` (which already differs between stacks: screener mock "Financials"
  vs harness mock "Financial Services"). Sector-awareness is therefore **contingent
  on EDGAR being in the run's chain and `SEC_IDENTITY` being set**; because both
  stacks share one process/env/config, they are either both EDGAR-enabled (→ same
  bucket) or both not (→ `m.sic = None` → `unknown` on both). This contingency is
  documented and covered by an explicit "EDGAR absent → both `unknown`" parity test.
  Foreign issuers (20-F) with no SIC → `unknown` symmetrically.

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

This replaces `_avg`'s silent-drop with an explicit partition.

**The sub-score floor is BUCKET-GATED — this is the key fix for the regression
risk the reviewer flagged.** A naive `min_valid_leg_fraction: 0.5` applied to the
`unknown` bucket would *newly abstain* partially-covered operating companies that
score today — because the harness path legitimately runs some sub-scores at 1–2 of
N legs (e.g. `eps_revision` is an accepted permanent `None` on the harness bridge,
so momentum is ≤2/3; FMP-gated value runs on the 2 recovered legs). Abstaining
those would be a silent screener regression across the entire free-tier universe.

Therefore:

- **`unknown` bucket → "any present applicable leg scores"** (fraction floor
  effectively `> 0`). This is **bit-identical to today** for any present leg — the
  no-op guarantee. No new abstention for operating companies.
- **Known bucket (financials/reit/insurer) → apply `min_valid_leg_fraction`** over
  the *applicable* legs. This new floor only ever touches the masked sectors.
- **Single-applicable-leg sub-scores:** when a sub-score has exactly one applicable
  leg, the fraction rule is degenerate (1/1 always scores). For known buckets, an
  absolute `min_present_legs` is **not** what makes moat abstain — instead v1
  *masks all three moat legs* for financials/insurers (incl. `roic`), so moat has
  **zero** applicable legs → abstains structurally (no floor needed). The masked
  leg set is the lever, chosen so each sub-score's surviving legs are genuinely
  defined (see §5).

**Composite validity floor.** After computing the five components and the existing
weight-redistribution:

```python
# applicable component = a composite component with >=1 applicable leg for the bucket.
# opportunity = max(momentum, value); it is APPLICABLE if EITHER momentum or value
#   has an applicable leg (momentum legs are never masked, so opportunity is always
#   applicable), and PRESENT if either constituent produced a number.
confidence = (sum of weights of PRESENT applicable components)
           / (sum of weights of APPLICABLE components)

# The composite floor is BUCKET-GATED, exactly like the leg floor:
if bucket == "unknown":
    scored = True                                   # pure back-compat: never withhold
else:
    scored = confidence >= config["validity"]["min_scored_weight"]
```

`scored` is **always `True` for the `unknown` bucket** — this is the hard no-op
guarantee that an operating company which gets *any* composite today (e.g. a
momentum-only name scoring on opportunity alone) can never flip to `not_scored`.
`min_scored_weight` therefore only ever affects the masked sectors.

`confidence` is still computed for every bucket (and emitted in `--json`) as a
transparency signal, but it only *gates* scoring for known buckets. Its denominator
uses *applicable* component weight, not all weight, so a sector that legitimately
has fewer applicable components (financials: moat excluded) is not unfairly
penalised — it measures **data completeness over what is measurable**, not breadth.
Breadth is visible separately via `abstentions` (§7). `composite` is still computed
from present components (unchanged math).

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

**`buckets` is an ORDERED LIST, not a dict** (the reviewer correctly flagged that
"first match wins" must not depend on YAML dict-key order, which a formatter could
reorder). Ranges are inclusive `[lo, hi]`; the resolver returns the first bucket
whose ranges contain the SIC.

**Conservative scope (the other half of "never mislead"): we mask ONLY where a leg
is structurally undefined/invalid, and we bucket ONLY clearly deposit-/spread-/
property-funded institutions.** Exchanges (SIC 6231), investment advisers / asset
managers (6282), and other capital-light 62xx financials have *meaningful* leverage
and returns — they are left `unknown`/unmasked in v1 (over-abstaining a name we can
measure is as dishonest as over-scoring one). This narrows the earlier blanket
`6000–6799`.

```yaml
sectors:
  buckets:                       # ORDERED: first matching range wins
    - name: reit
      sic_ranges: [[6798, 6798]]
    - name: insurer
      sic_ranges: [[6300, 6399], [6410, 6411]]   # carriers (life/health/P&C/title/surety) + agents
    - name: financials           # depository banks, holdings, broker-dealers, mortgage/credit
      sic_ranges: [[6020, 6099], [6120, 6179], [6199, 6199], [6211, 6211], [6712, 6712]]
  # All three buckets share the SAME masked set (the legs that are structurally
  # undefined or non-representative for spread/deposit/property businesses):
  masked_legs:  [gross_margin, gross_margin_stability, roic, fcf_yield, fcf_cagr, interest_coverage, debt_to_equity]
  masked_gates: [negative_fcf, over_leveraged]

validity:
  # Bucket-gated (see §4.3): the fraction floor applies ONLY to known buckets.
  min_valid_leg_fraction: 0.5   # known-bucket sub-score needs >= half its APPLICABLE legs present
  unknown_min_present_legs: 1   # unknown bucket: any present leg scores (today's behavior, no-op)
  min_scored_weight:      0.34  # composite 'scored' iff present-applicable-component weight / applicable
                                # weight >= this. 0.34 chosen so a single 0.30 opportunity component
                                # alone is NOT enough, but any two components are; tuned in §6 census.
```

Notes on the masked set / ranges (resolved from review findings #9–#11):

- **`net_margin` is NOT masked** anywhere. It is *defined* for banks/insurers/REITs
  (it just sits on a different revenue base / is depreciation-distorted) → that is
  **miscalibration (mode 2), deferred**, not undefinedness. Masking it would be
  scope creep into calibration. (Resolves the asymmetry the reviewer flagged.)
- **`roic` and `fcf_cagr` ARE masked** for all three buckets — `roic` so `moat`
  genuinely abstains (§3), `fcf_cagr` because it inherits the same FCF-invalidity as
  `fcf_yield`. `growth` therefore survives on revenue/EPS CAGR + persistence.
- **SIC ranges are explicit and non-overlapping**; 6798 (REIT) is its own first
  entry and is *not* inside the financials ranges (defense-in-depth vs ordering).
  6200/6231 (exchanges) and 6282 (advisers) are deliberately **excluded** → `unknown`.
- These exact ranges are validated against the SEC SIC table in the plan; a unit
  test pins each boundary (incl. ICE-like 6231 → unknown, adviser 6282 → unknown).

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

Additive only (no removed/renamed fields → scout & `/run` keep working). **New
`ScoreCard` fields are appended AFTER `coverage`** (models.py:118), all with
defaults, so positional construction in `scoring.score` (scoring.py:149) is
unaffected and the "non-default after default" dataclass rule holds:

- `ScoreCard.sic_bucket: Optional[str] = None` — resolved bucket. **Named
  `sic_bucket`, NOT `sector_bucket`**, to keep it unmistakably distinct from the
  pre-existing free-text `StockMetrics.sector` (source-dependent, divergent across
  stacks). A guard test asserts scoring **never reads `m.sector`** for applicability.
- `ScoreCard.confidence: float = 1.0` — present-applicable-component weight ÷
  applicable-component weight (1.0 = every measurable component present).
- `ScoreCard.scored: bool = True` — `confidence >= min_scored_weight`.
- `ScoreCard.abstentions: list = field(default_factory=list)` — diagnostic,
  parallel to `coverage`: per leg / sub-score, `{name, reason: "inapplicable" |
  "missing"}`. Emitted in `--json` only when non-empty, summarized on stderr.

**Coverage / abstention reconciliation (review #13).** `build_coverage`
(coverage.py:71) currently lists every `None` sub-score in `unavailable` and, when
FMP gated, attaches the "needs Starter tier" note — which would *contradict* an
abstention that says "inapplicable." Fix: a sub-score that is `None` because it was
**masked-inapplicable** is excluded from `coverage.unavailable` (it is not a
coverage gap). Only *missing-data* `None`s remain in coverage. The two diagnostics
must never tell opposite stories about the same field; a test asserts a masked leg
appears in `abstentions(reason=inapplicable)` and **not** in `coverage.unavailable`.

**Ranking / sort safety (review #8).** `composite` alone is **no longer a safe
sort key** — a `not_scored` card can carry a high composite from one surviving leg.
CLI ranking and `scout` sort must demote not-scored cards: sort key becomes
`(scored, composite)` desc so a not-scored name can never top a list. Documented;
`scout/report.py` rendering updated to skip / visibly demote `not scored`.

Sub-score fields stay `Optional[float]`; `None` now means "abstained or no inputs,"
with the *why* in `abstentions` (keeps the existing null-vs-number semantics).
`composite` stays `float`. The only `passed` change is it now also requires
`scored` — a monotonic tightening, documented and covered by the no-op regression
test (every currently-passing name must stay `scored=True`).

## 8. Open items to finalize in the plan

Resolved in this revision (post spec-review): `roic`/`fcf_cagr` masking (now masked,
§5), the floor regression (now bucket-gated, §4.3), SIC ranges (now explicit
ordered list, §5), Profile SIC plumbing (EdgarSource partial Profile, §4.2), field
naming (`sic_bucket`, §7), coverage reconciliation (§7). Remaining for the plan:

1. **Coverage census of the test universe** to pin `min_valid_leg_fraction` /
   `min_scored_weight` so the unknown-sector no-op is *provably* bit-identical:
   enumerate, across a representative basket on both engines, how many legs each
   sub-score actually carries today, and confirm no currently-scored name flips to
   `not_scored`. This is the empirical backstop for the back-compat guarantee.
2. **Confirm `edgartools` `Company.sic` shape** (int vs zero-padded str vs missing)
   on real tickers so `extract_sic` normalization is correct; pin in a test using a
   recorded fixture (no live key needed in CI).
3. **Final SIC range audit** against the SEC SIC code list — verify 6020–6099
   (depositories), 6120–6179 (S&Ls/credit/mortgage), 6211 (broker-dealers), 6712
   (bank holding cos), 6300–6411 (insurers), 6798 (REIT); confirm exchanges (6231)
   and advisers (6282) fall through to `unknown` as intended.

## 9. Files touched (anticipated)

- **new** `src/shortlist/sectors.py` — `resolve_bucket`, `extract_sic`,
  `leg_applicable`, `gate_applicable` (the only interpreter of the config maps).
- `src/shortlist/models.py` — `StockMetrics.sic`; `ScoreCard` new fields appended
  after `coverage`; `passed = not gates and scored`.
- `src/shortlist/scoring.py` — named-leg refactor, bucket-gated abstention, floor,
  gate masking; must **never read `m.sector`**.
- `src/shortlist/providers/edgar.py` — `m.sic = extract_sic(company)` + add `sic`
  to `_tag(...)`.
- `src/shortlist/merge.py` — ensure `sic` survives merge (priority/`_pick`); it is
  a plain `StockMetrics` field so pick-first applies, but confirm + test.
- `src/shortlist/data/models.py` — `Profile.sic`.
- `src/shortlist/data/sources.py` (EdgarSource) — emit partial `Profile(sic=…)`.
- `src/shortlist/data/bridge.py` — copy `m.sic = p.sic`.
- `src/shortlist/providers/mock.py` + `src/shortlist/data/mockdata.py` — add `sic`
  to fixtures (SCHW → broker-dealer 6211; a bank; a REIT; an operating co) so the
  SCHW golden + parity tests run **without live keys** (review #17).
- `config.yaml` — `sectors`, `validity` blocks.
- `src/shortlist/coverage.py` — exclude masked-inapplicable sub-scores from
  `unavailable`; reconcile with abstentions.
- CLI (`screen.py`) `--json` + stderr — emit `abstentions`, `sic_bucket`,
  `confidence`, `scored`; ranking sort key `(scored, composite)`.
- `src/shortlist/scout/report.py` — demote/skip `not scored` in the report.
- `tests/` — new `test_sectors.py`; extend `test_scoring.py`; **two-stack parity
  test**; SCHW golden test; unknown-sector no-op (bit-identical) test; EDGAR-absent
  parity test; coverage-vs-abstention non-contradiction test.
- Docs: `CLAUDE.md` (sector-aware section), `README.md`/`HARNESS.md` notes.
```
