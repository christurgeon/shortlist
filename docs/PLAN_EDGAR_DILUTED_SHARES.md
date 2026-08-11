# EDGAR diluted-shares/EPS extraction — concept-matching fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Revision 3 — SIGNED OFF.** Revision 2 was rewritten after adversarial review found three
Critical defects in revision 1 (a regression the design would have introduced, an understated
blast radius, and a mandated-but-missing test); those changes are marked **[R2]**. A second
review of revision 2 verified all three ADDRESSED — running the proposed tests against the
real code (14/14 green after, 27 existing EDGAR tests still green) — and found five smaller
defects, two of them in the new code. Those are fixed here and marked **[R3]**:

1. `_rows_by_concept` used `.loc` with a sorted index, which on a **duplicated index** silently
   returns the cartesian expansion *and inverts the ordering* — reintroducing the child-row bug
   the min-level rule exists to prevent. Now positional.
2. Walking level-sorted candidates could **trade abstention for a wrong-but-complete child
   row**. Now only minimum-level rows are candidates, so a sparse total abstains instead.
3. QCOM was wrongly listed as a value-leg mover (its EPS is already as-reported); DIS and VZ
   were unexplained.
4. The Step 2 expected-fail partition and test count were wrong (7/7, 14 tests).
5. The module-docstring correction had no commit path.

Plus the reviewer's residual-risk item: the go/no-go now hand-checks recovered **values**, not
just their presence.

**Goal:** Recover `diluted_shares` for the 38% of EDGAR-covered issuers where extraction
silently yields `[]`, and replace computed-EPS approximations with as-reported values, by
matching the authoritative raw us-gaap `concept` instead of filer-chosen `label` text.

**Architecture:** Row selection scans the human-readable `label` column. Labels are filer
presentation text and vary wildly; raw `concept` is the authoritative XBRL tag. Add a
**value-aware** concept-first lookup that falls back to the label scan, so no working issuer
regresses.

**Tech Stack:** Python 3.11+, pandas (already present via edgartools), pytest, uv.

## Evidence — measured, reproduced twice (2026-07-31)

From the 42 EDGAR-won tickers in `/opt/shortlist/state/snapshots` (latest snapshot each).
Both figures below were independently reproduced by the plan reviewer.

**Gap 1 — `diluted_shares` empty for 16/42 (38%)**: CMCSA COST CVX GOOGL HON IBM LMT MO MRK
MSFT ORCL PEP PG QCOM VZ XOM. Live-classified into two disjoint root causes:

- **Root cause A — label mismatch (7). THIS PLAN FIXES.** `us-gaap_WeightedAverage
  NumberOfDilutedSharesOutstanding` is present with complete values, but `_row_diluted_shares`
  requires both `"diluted"` and `"shares"` in the label:

  | ticker | actual label | miss |
  |---|---|---|
  | COST MSFT ORCL PEP QCOM | `'Diluted'` | no "shares" |
  | IBM | `'Assuming dilution (in shares)'` | says *dilution* |
  | VZ | `'Weighted-average shares outstanding (in shares)'` | no "diluted" |

  AAPL works because its label is `'Diluted (in shares)'`. The disambiguating context for
  MSFT-style filers sits in a separate all-`NaN` abstract parent row that the flat dataframe
  does not associate with its children.

- **Root cause B — concept genuinely absent (9). OUT OF SCOPE, see §Deferred.** CMCSA CVX
  GOOGL HON LMT MO MRK PG XOM — probed GOOGL/LMT/XOM: only `EarningsPerShareBasic`/`Diluted`
  exist. No share-count tag at any label.

**Gap 2 — root cause C: the same label bug on EPS. [R2] NINE issuers, not four.**
`_row_diluted_eps` requires `"per share"`; COST/MSFT/ORCL/PEP label theirs `'Diluted'`. On a
miss, `extract_financials:283-284` computes:

```python
if not eps and fin.net_income and shares_diluted:
    eps = [ni / shares_diluted for ni in fin.net_income]
```

`shares_diluted` is a **single scalar** (today's count) divided into **every** year's net
income. Detected in the store as any `diluted_eps` with >2 decimal places:

```
COST 18.208060647072973   DIS 6.849254555494202    IBM 11.166097403357094
MCD  11952819.65382468    MSFT 17.94565946598685   ORCL 5.863761153054221
PEP  6.001456664238893    UNH 13.233809001097695   VZ 4.059087686126212
```

**[R2] DIS, MCD and UNH were never examined in revision 1** — their `diluted_shares` extract
fine, so they never appeared in Gap 1.

**[R2] MCD is a live garbage value on a scored surface.** `diluted_eps[0] = 11,952,819.65`,
because `get_shares_outstanding_diluted()` returns MCD's count in **millions** (~716) while
`net_income` is absolute dollars. `bridge.py:242` then yields `pe_ttm = 268.44 / 1.195e7 =
2.25e-05`, which renders in the bot digest and feeds `pe_vs_history`. Fixing the EPS row
pick removes the fallback for MCD and repairs this. **Note the residual:** the units hazard in
`get_shares_outstanding_diluted()` itself is NOT fixed here — see §Deferred.

**[R2] This also falsifies `_edgar_facts.py:7-10`** ("ABSOLUTE USD … No scaling here or
downstream"): MCD's `diluted_shares` series is `[716.4, 721.9, 732.3]`.

## Global Constraints

- **Match raw `concept`, never `standard_concept`** — the repo rule
  (`_concept_family_latest` already does this; `docs/audits/2026-07-12-accruals-leg-disable.md`
  records bucket-name drift silently breaking a leg).
- **No new fetch, no new dependency, no new config block.**
- **[R2] No currently-working issuer may regress — and this must be enforced by VALUE, not by
  row presence.** See Critical 1 below.
- **`_series` stays all-or-nothing.** A half-filled series would corrupt `cagr`.
- **LANDMINE:** a `pd.Series` in a boolean context raises `ValueError: The truth value of a
  Series is ambiguous` (verified, pandas 3.0.3). Never `row = _by_concept(...) or _by_label(...)`.
- Drop `dimension == True` rows before matching (the `_concept_family_latest:186` guard).
- **[R2] Mirror `_row_by_standard_concept`'s min-`level` tie-break** (`_edgar_facts.py:132-137`)
  — that logic exists because `iloc[0]` picked a nested child row twice on real filings.
- CI, in order: `uv run ruff check src tests`, then `uv run pytest -q`. (`E501` is ignored in
  `pyproject.toml`, so line length is not a CI gate.)

## [R2] Blast radius — rebuilt from the store scan

Two distinct effects. **Note "EPS value changes" ≠ "the value leg moves"**: `bridge.py:241,243`
only fire when `m.pe_ttm is None`, i.e. when FMP did **not** supply a PE.

| Effect | Issuers | Surface |
|---|---|---|
| `diluted_shares` `[]` → populated | 7 (COST IBM MSFT ORCL PEP QCOM VZ) | `share_count_cagr` → **`dilution` flag** (ON); `quality.dilution` leg (OFF); JSON/CSV (`screen.py:262,330`) |
| `diluted_eps` computed → as-reported | 9 (COST DIS IBM MCD MSFT ORCL PEP UNH VZ) | `eps_cagr_ps`; research QUANT CONTEXT |
| …of which the **value leg actually moves** | **[R5, corrected] FMP-gated subset: IBM MCD ORCL PEP UNH JNJ QCOM HON MRK XOM (10, not 5)** — live-verified against `/opt/shortlist/state/snapshots/*/2026-07-30.json.gz`: all 10 carry `fundamentals.pe_ttm = None` (no FMP PE), so the EDGAR fallback is live for every one of them; COST, MSFT, DIS and VZ carried an FMP `pe_ttm` on the captured day (47.92 / 25.06 / 15.36 / 12.01), so the fallback stays dormant for those 4. The original row undercounted this two ways: it predates the [R4] finding (JNJ/HON/MRK/XOM weren't in scope yet), and it wrongly called **QCOM NOT a mover** ("its EPS is already as-reported") — [R4] below shows QCOM's EPS *was* wrong-row (continuing-ops) pre-fix, so it is a mover too | `pe_ttm`/`pe_median_5y` → **scored `pe_vs_history`** → `composite`, ranking |

Additional surfaces, all previously unnamed:

- **`value_trap` flag** (`scoring.py:800`) keys off the `value` sub-score, so a `pe_vs_history`
  change can flip it — a second-order flag effect.
- **Research briefs are accession-cached**, so existing briefs will NOT regenerate; those
  tickers keep an LLM screening call reasoned over the old `dEPS`/`shrs` numbers
  (`research/assess.py:294-315`) until `--refresh`.
- **Bot digest** prints "PE (ttm)" (`bot/report/sections.py:148`) — MCD's `2.25e-05`
  becomes a real PE.
- **`eps_cagr_ps` is currently DEGENERATE** for all 9: dividing every year's NI by one constant
  makes `cagr(diluted_eps) == cagr(net_income) == eps_cagr`. Not a regression, but any prior
  measurement of `eps_cagr_ps` on harness data for these names was tautological. Record it.
- **[R2] The exposure is FMP-quota-dependent and therefore non-deterministic.** On a day when
  FMP 429s (recorded in `TODO.md`), COST/MSFT route through the EDGAR fallback too.

## [R4] Blast radius was STILL incomplete — 5 more issuers, found by the go/no-go

The Task 2 live 42-ticker before/after **failed clause 3** (nothing outside the documented
sets may change). Five tickers changed that this plan never named. All five changes are
improvements, but the plan's measurement missed them, so the go/no-go did its job.

**Why the measurement missed them — a methodological failure, not bad luck.** Root cause C
was detected by a `>2 decimal places` heuristic, which finds *computed* EPS. It is
structurally blind to **wrong-row** EPS: a continuing-operations figure is a clean, plausible
2-dp number, indistinguishable from a total by that test. Detecting the class below required
comparing against `net_income / diluted_shares`, which is what the Part B cross-check does.

| ticker | before | after | class |
|---|---|---|---|
| HON, MRK, XOM | `diluted_eps = []` | as-reported | New recovery. Their `diluted_shares` concept is absent (root cause B) **and** `get_shares_outstanding_diluted()` returns `None`, so the computed fallback could not fire either — they had NO EPS at all. Strictly better. |
| JNJ, QCOM | continuing-operations EPS | total EPS | **Pre-existing wrong-row bug, live in production.** The plan's claim "QCOM is NOT a mover" is false on real data. |

**JNJ is a live, sign-flipping corruption of a scored input.** Production carries
`diluted_eps = [11.03, 5.79, 5.20]`, but FY2023 net income $35.2B ÷ 2,560.4M shares =
**$13.75** — the stored 5.20 is post-Kenvue-spinoff *continuing operations*, 2.6× too low.
FY2024/25 are correct, so the series **mixes two different measures**, which is worse than
being uniformly wrong:

```
eps_cagr_ps stored (production) : +0.4564   (+45.6%/yr)
eps_cagr_ps true                : -0.1044   (-10.4%/yr)
```

The sign is inverted. This branch fixes it as a side effect; exact-equality concept matching
cannot select `IncomeLossFromContinuingOperationsPerDilutedShare`.

**Adjudication (controller):** this is NOT a Task 1 code defect — the picker behaves
correctly on all five. It is a blast-radius documentation failure, the third in this project.
The expected-change set for the go/no-go is therefore **7 shares + 9 EPS + these 5 = 21
tickers**, and clause 3 governs the remaining 21. Proceed on that basis; do not change code.

**[R5, corrected] "21" above is a multiset sum, not the distinct ticker count.** 7-shares
(A) and 9-EPS (C) overlap on 6 tickers (COST IBM MSFT ORCL PEP VZ), and QCOM is counted once
in A and again in the 5 [R4] tickers — the true distinct union is **14** (COST DIS HON IBM
JNJ MCD MRK MSFT ORCL PEP QCOM UNH VZ XOM), re-derived from the audit doc's own 42-row table
(14 changed, 28 byte-identical). Clause 3 governs everything outside those 14, not 21.

**Explicitly NOT affected** (verified by the reviewer, and the `extract_financials` hint is a
red herring): `asset_growth`/`accruals` (`:327-333`) and the §5 financing legs (`:338-341`)
never call these pickers. **Coverage is unaffected** — both fields are in `_NON_SIGNAL_FIELDS`
(`data/models.py:302`). **[R2] `confidence`/`scored` do NOT move** — computed EPS already
populated `pe_ttm`/`pe_median_5y`, so no leg changes presence. Revision 1 wrongly claimed
`confidence` moves.

**[R5, corrected] The `[R2]` claim above is right in conclusion but wrong in its stated
reason for 3 of the 10 movers.** For IBM/MCD/ORCL/PEP/UNH/JNJ/QCOM, `pe_ttm` was already
non-`None` pre-fix (an EDGAR fallback computed off the WRONG pre-fix EPS still populated it
— "computed EPS already populated `pe_ttm`" is accurate for these 7). It is **false as stated
for HON/MRK/XOM**: they had NO EPS at all pre-fix (root cause D), so `pe_vs_history`
genuinely moves `None` → populated for them — a real leg-presence change, not just a value
change. `confidence`/`scored` still don't move for HON/MRK/XOM, but for two different
reasons: (a) `confidence` is computed at **component** granularity
(`scoring.py:783-786` — the `value` component, not its `pe_vs_history` sub-leg, is what
enters `appl_w`/`pres_w`), and (b) HON/MRK/XOM already have `fcf_yield` derivable from EDGAR
FCF, so the `value` component itself was already non-`None` both before and after — a second
present leg inside an already-present component can't move `confidence`. **Near miss:** any
ticker whose `value` had ZERO present legs pre-fix and gains `pe_vs_history` post-fix WOULD
move `confidence`, and potentially `scored`, in a known (unmasked) sector bucket.

## File Structure

| File | Change |
|---|---|
| `src/shortlist/providers/_edgar_facts.py` | `_rows_by_concept` + `_series_by_concept_or_label`; call them from `extract_financials`; **[R3]** correct the falsified "ABSOLUTE, no scaling" module docstring (:7-10) | Modify |
| `tests/test_edgar_facts_concept_match.py` | Create |
| `docs/audits/2026-07-31-edgar-concept-match.md` | Create (evidence of record) |
| `CLAUDE.md`, `TODO.md` | Modify |

---

### Task 1: Value-aware concept-first extraction

**[R2] Design changed.** Revision 1 modified `_row_diluted_shares`/`_row_diluted_eps` to
return a concept row when found. That is **wrong**: `_series` is all-or-nothing, so a sparse
or all-`NaN` concept row would *shadow* a label row that works today and turn a populated
series into `[]`. The reviewer demonstrated it (`[7.453e9, …]` → `[]`). Selection must be
**value-aware**: a concept row wins only if it yields a complete series.

The `_row_*` pickers are therefore left **untouched** (every existing fixture keeps passing),
and a new series-level function owns the choice.

**Files:** Modify `src/shortlist/providers/_edgar_facts.py`; create `tests/test_edgar_facts_concept_match.py`

**Interfaces produced:**
- `_rows_by_concept(df, concepts) -> list[pd.Series]`
- `_series_by_concept_or_label(df, concepts, label_picker, fy_cols) -> list[float]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_edgar_facts_concept_match.py`. Label strings are **verbatim from live
EDGAR filings, 2026-07-31** — do not tidy them. Tests target `extract_financials`, because
that is where the computed-EPS fallback lives and where the behaviour actually materialises.

```python
from __future__ import annotations

import pandas as pd

from shortlist.providers._edgar_facts import extract_financials

SHARES = "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding"
BASIC = "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic"
ABSTRACT = "us-gaap_WeightedAverageNumberOfSharesOutstandingAbstract"
EPS = "us-gaap_EarningsPerShareDiluted"
EPS_CONTINUING = "us-gaap_IncomeLossFromContinuingOperationsPerDilutedShare"
REVENUE_SC, NI_SC = "Revenue", "NetIncomeLoss"

FY = ["2025-06-30 (FY)", "2024-06-30 (FY)", "2023-06-30 (FY)"]
NI = [90e9, 88e9, 72e9]


def _row(label, vals, concept=None, standard_concept=None, dimension=False, level=1):
    d = {"label": label, "concept": concept, "standard_concept": standard_concept,
         "dimension": dimension, "level": level}
    d.update(dict(zip(FY, vals, strict=True)))
    return d


def _income(rows: list[dict]) -> pd.DataFrame:
    base = [_row("Revenue", [200e9, 190e9, 180e9], standard_concept=REVENUE_SC),
            _row("Net income", NI, standard_concept=NI_SC)]
    return pd.DataFrame(base + rows)


_EMPTY = pd.DataFrame()


def _extract(income_rows, shares_scalar=7_000e6):
    return extract_financials(_income(income_rows), _EMPTY, _EMPTY,
                              shares_diluted=shares_scalar)


# --- root cause A: share-count label misses, concept hits -----------------

def test_msft_style_bare_diluted_label_recovers_shares():
    ef = _extract([
        _row("Diluted", [17.95, 13.64, 11.80], concept=EPS),
        _row("Weighted average shares outstanding:", [None, None, None], concept=ABSTRACT),
        _row("Basic", [7_430e6, 7_440e6, 7_450e6], concept=BASIC),
        _row("Diluted", [7_453e6, 7_465e6, 7_469e6], concept=SHARES),
    ])
    assert ef.diluted_shares == [7_453e6, 7_465e6, 7_469e6]


def test_ibm_style_assuming_dilution_label_recovers_shares():
    ef = _extract([_row("Assuming dilution (in shares)", [920e6, 925e6, 930e6], concept=SHARES)])
    assert ef.diluted_shares == [920e6, 925e6, 930e6]


def test_vz_style_label_without_the_word_diluted_recovers_shares():
    ef = _extract([_row("Weighted-average shares outstanding (in shares)",
                        [4.2e9, 4.2e9, 4.2e9], concept=SHARES)])
    assert ef.diluted_shares == [4.2e9, 4.2e9, 4.2e9]


def test_basic_share_row_is_never_used():
    ef = _extract([_row("Basic (in shares)", [1.0, 2.0, 3.0], concept=BASIC)])
    assert ef.diluted_shares == []


def test_dimensional_breakdown_rows_are_ignored():
    ef = _extract([
        _row("Diluted", [99.0, 99.0, 99.0], concept=SHARES, dimension=True),
        _row("Diluted", [7.0, 8.0, 9.0], concept=SHARES),
    ])
    assert ef.diluted_shares == [7.0, 8.0, 9.0]


def test_nested_child_row_loses_to_the_min_level_row():
    # Mirrors the MSFT OCF failure that motivated _row_by_standard_concept's
    # min-level tie-break: iloc[0] would grab the level-4 child.
    ef = _extract([
        _row("Diluted (child)", [1.0, 2.0, 3.0], concept=SHARES, level=4),
        _row("Diluted", [7.0, 8.0, 9.0], concept=SHARES, level=2),
    ])
    assert ef.diluted_shares == [7.0, 8.0, 9.0]


# --- [R2] Critical 1: value-aware fallback (regression guards) ------------

def test_sparse_concept_row_does_not_shadow_a_working_label_row():
    # _series is all-or-nothing. A concept row with a NaN must NOT beat a
    # complete label-matched row, or a populated series silently becomes [].
    ef = _extract([
        _row("Diluted", [7_453e6, None, 7_469e6], concept=SHARES),
        _row("Weighted average diluted shares", [1e9, 2e9, 3e9]),
    ])
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_all_nan_concept_row_does_not_shadow_a_working_label_row():
    ef = _extract([
        _row("Weighted average shares outstanding:", [None, None, None], concept=SHARES),
        _row("Weighted average diluted shares", [1e9, 2e9, 3e9]),
    ])
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_label_scan_still_works_with_no_concept_column():
    df = pd.DataFrame([
        {"label": "Revenue", "standard_concept": REVENUE_SC, **dict(zip(FY, [1.0, 1.0, 1.0], strict=True))},
        {"label": "Weighted average diluted shares", **dict(zip(FY, [1e9, 2e9, 3e9], strict=True))},
    ])
    ef = extract_financials(df, _EMPTY, _EMPTY, shares_diluted=None)
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_aapl_style_label_still_works_no_regression():
    ef = _extract([_row("Diluted (in shares)", [15.0e9, 15.4e9, 15.8e9], concept=SHARES)])
    assert ef.diluted_shares == [15.0e9, 15.4e9, 15.8e9]


# --- [R2] Critical 3: the EPS provenance flip, pinned ---------------------

def test_bare_diluted_eps_label_uses_reported_values_not_the_computed_fallback():
    scalar = 7_000e6
    ef = _extract([
        _row("Diluted", [17.95, 13.64, 11.80], concept=EPS),
        _row("Diluted", [7_453e6, 7_465e6, 7_469e6], concept=SHARES),
    ], shares_scalar=scalar)
    assert ef.diluted_eps == [17.95, 13.64, 11.80]          # as-reported
    assert ef.diluted_eps != [ni / scalar for ni in NI]     # NOT the fallback


def test_computed_fallback_still_fires_when_no_eps_row_exists_at_all():
    scalar = 7_000e6
    ef = _extract([], shares_scalar=scalar)
    assert ef.diluted_eps == [ni / scalar for ni in NI]


def test_reported_eps_label_path_still_works():
    ef = _extract([_row("Diluted (in dollars per share)", [7.46, 6.08, 6.13], concept=EPS)])
    assert ef.diluted_eps == [7.46, 6.08, 6.13]


# --- [R2] Important 6: discontinued-operations row ordering ---------------

def test_continuing_operations_eps_row_never_displaces_the_total_eps_row():
    # A filer with discontinued ops carries BOTH tags. Only EarningsPerShareDiluted
    # is the total; picking the continuing-ops row would silently move a scored leg.
    ef = _extract([
        _row("Continuing operations", [6.0, 5.0, 4.0], concept=EPS_CONTINUING),
        _row("Diluted", [5.0, 4.0, 3.0], concept=EPS),
    ])
    assert ef.diluted_eps == [5.0, 4.0, 3.0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_edgar_facts_concept_match.py -q`
**[R3] Measured partition: 7 FAIL, 7 PASS (14 tests total).**

Expected **FAIL** (7 — new behaviour): the three root-cause-A recovery tests
(`msft_style`, `ibm_style`, `vz_style`), `test_dimensional_breakdown_rows_are_ignored`,
`test_nested_child_row_loses_to_the_min_level_row`,
`test_bare_diluted_eps_label_uses_reported_values_not_the_computed_fallback`, **and
`test_continuing_operations_eps_row_never_displaces_the_total_eps_row`** — revision 2 wrongly
listed that last one as an existing pass. It fails today with `[12.857…] != [5.0, 4.0, 3.0]`,
because neither the `'Continuing operations'` nor the `'Diluted'` label contains "per share",
so both miss and the computed fallback fires. It is a new-behaviour test, not a regression pin.

Expected **PASS** already (7 — genuine regression pins): the two value-aware shadowing guards,
`test_label_scan_still_works_with_no_concept_column`, `test_aapl_style_label_still_works_no_regression`,
`test_basic_share_row_is_never_used`, `test_computed_fallback_still_fires_when_no_eps_row_exists_at_all`,
`test_reported_eps_label_path_still_works`.

A test asserted GREEN that runs RED reads as a broken plan at the first checkpoint — if the
observed partition is not 7/7, stop and reconcile before implementing.

- [ ] **Step 3: Implement**

Add to `src/shortlist/providers/_edgar_facts.py`, above `extract_financials`:

```python
# Authoritative raw us-gaap tags. Matched on `concept`, NOT `label` (filer
# presentation text: MSFT/COST/ORCL/PEP label the share-count row just "Diluted",
# IBM "Assuming dilution (in shares)", VZ omits "diluted" — 7 of 42 production
# tickers extracted EMPTY on labels alone) and NOT `standard_concept` (bucket names
# drift across edgartools releases — docs/audits/2026-07-12-accruals-leg-disable.md).
_DILUTED_SHARES_CONCEPTS = ("us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",)
_DILUTED_EPS_CONCEPTS = ("us-gaap_EarningsPerShareDiluted",)


def _rows_by_concept(df: pd.DataFrame, concepts: tuple[str, ...]) -> list[pd.Series]:
    """Non-dimensional rows whose raw `concept` EXACTLY equals one of `concepts`.
    Exact equality, never substring: a prefix match would let
    IncomeLossFromContinuingOperationsPerDilutedShare pose as total EPS.

    Returns only rows at the MINIMUM `level` — the same preference as
    `_row_by_standard_concept`, which exists because iloc[0] grabbed a nested child
    on real MSFT/GOOGL filings. Deeper children are dropped, NOT kept as later
    candidates: a sparse total must fall through to the label scan and ultimately
    ABSTAIN, never be silently replaced by a complete-but-wrong child line. Abstain
    rather than guess — a wrong-but-complete share series would feed
    `share_count_cagr` and the `dilution` flag with no signal that it is wrong.

    Indexing is POSITIONAL (`.iloc` + argsort). `.loc` with a sorted index is wrong
    here: on a duplicated index it silently returns the cartesian expansion AND
    inverts the ordering (measured: index [7,7], levels [4,2] -> 4 rows, child
    first), reintroducing the exact bug the min-level rule prevents."""
    if "concept" not in df.columns:
        return []
    rows = df
    if "dimension" in rows.columns:
        rows = rows[rows["dimension"] != True]      # noqa: E712 — drop breakdowns
    col = rows["concept"].astype(str)
    out: list[pd.Series] = []
    for c in concepts:
        hit = rows[col == c]
        if hit.empty:
            continue
        if "level" in hit.columns:
            lvl = pd.to_numeric(hit["level"], errors="coerce")
            if lvl.notna().any():
                hit = hit.iloc[(lvl.to_numpy() == lvl.min()).nonzero()[0]]
        out.extend(hit.iloc[i] for i in range(len(hit)))
    return out


def _series_by_concept_or_label(df: pd.DataFrame, concepts: tuple[str, ...],
                                label_picker, fy_cols: list[tuple[str, str]]) -> list[float]:
    """VALUE-AWARE pick. A concept row wins only if it yields a COMPLETE series;
    otherwise we fall through to the next candidate and finally to the label scan.
    Keying the fallback on row-presence instead would let a sparse or all-NaN
    concept row SHADOW a label row that works today, turning a populated series
    into [] (`_series` is all-or-nothing) — a regression, not a no-op."""
    for row in _rows_by_concept(df, concepts):
        series = _series(row, fy_cols)
        if series:
            return series
    return _series(label_picker(df), fy_cols)
```

Then in `extract_financials`, replace lines 282 and 286 only (leave 283-285 intact):

```python
    eps = _series_by_concept_or_label(income_df, _DILUTED_EPS_CONCEPTS, _row_diluted_eps, inc_fy)
    if not eps and fin.net_income and shares_diluted:
        eps = [ni / shares_diluted for ni in fin.net_income]
    fin.diluted_eps = eps
    fin.diluted_shares = _series_by_concept_or_label(
        income_df, _DILUTED_SHARES_CONCEPTS, _row_diluted_shares, inc_fy)
```

`_row_diluted_shares` and `_row_diluted_eps` are **not modified**.

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_edgar_facts_concept_match.py -q` → PASS (14 tests)

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q` → green. `tests/test_edgar_leverage_live.py` is `-m live`-marked and
deselected by default; do not run it (it hits SEC).

- [ ] **Step 6: Commit**

```bash
uv run ruff check src tests
git add src/shortlist/providers/_edgar_facts.py tests/test_edgar_facts_concept_match.py
git commit -m "fix(edgar): value-aware concept-first matching for diluted share/EPS rows"
```

---

### Task 2: Full-universe live verification + evidence + docs

- [ ] **Step 1: [R2] Live before/after across ALL 42 store tickers — mandatory, keyless**

Revision 1 checked 9 tickers against a universe-wide constraint. Enumerate the real universe
instead (`set -a && . ./.env && set +a` first for `SEC_IDENTITY`; no FMP quota needed):

```python
# scratch script; writes before/after JSON for diffing
import glob, gzip, json, os, sys
sys.path.insert(0, "src")
from edgar import Company, set_identity
set_identity(os.environ["SEC_IDENTITY"])
from shortlist.providers._edgar_facts import extract_financials

tickers = sorted({os.path.basename(os.path.dirname(p))
                  for p in glob.glob("/opt/shortlist/state/snapshots/*/*.json.gz")})
out = {}
for tk in tickers:
    try:
        f = Company(tk).get_financials()
        try: sh = f.get_shares_outstanding_diluted()
        except Exception: sh = None
        ef = extract_financials(f.income_statement().to_dataframe(),
                                f.cashflow_statement().to_dataframe(),
                                f.balance_sheet().to_dataframe(), shares_diluted=sh)
        out[tk] = {"shares": ef.diluted_shares[:3], "eps": ef.diluted_eps[:3]}
    except Exception as e:
        out[tk] = {"error": f"{type(e).__name__}: {e}"}
print(json.dumps(out, indent=1))
```

Run it on `main` and on the branch, diff the two JSONs.

**Go/no-go — this is the one premise unit tests cannot cover** (root cause A assumes the
concept row has complete values across every `inc_fy` column; `TODO.md` records MSFT carrying
an FY2026 column, so a partial extra column would still yield `[]`):
- The 7 root-cause-A tickers must go `shares=[]` → three real values.
- The 9 computed-EPS tickers must go long-float → 2-dp as-reported.
- **Every other ticker must be byte-identical. If any name not on those lists changes, STOP** —
  that is the shadowing regression or the continuing-ops swap, and the plan is wrong.
- **[R3] Hand-check the recovered values, not just their presence.** For all 7 root-cause-A
  tickers, verify `diluted_shares[0]` against the filed 10-K's own weighted-average diluted
  share count (the EPS note or income-statement face). "Recovered" and "recovered *correct*"
  are different claims, and only this check separates them: an `iloc[0]`-style pick of a
  nested child or a component line yields a complete, plausible-looking, WRONG series that the
  byte-identical rule above cannot catch — it is on the expected-to-change list. This is the
  same failure that bit `_row_by_standard_concept` twice on real filings (MSFT OCF child,
  GOOGL non-cash capex), which is why the min-level rule exists. Record the 7 comparisons in
  the audit doc.

- [ ] **Step 2: [R2] Commit the evidence to `docs/audits/`**

`CLAUDE.md` requires reproducible verdicts on the tracked audits tree ("that's how two
enablement artifacts already evaporated"); a commit message is not that surface. Write
`docs/audits/2026-07-31-edgar-concept-match.md` with the before/after table for all 42, the
root-cause A/B/C split, and the repro command.

- [ ] **Step 3: CLAUDE.md**

In the EDGAR section: row selection matches the raw `concept` column first and is
**value-aware** (a concept row wins only if it yields a complete series, so it can never
shadow a working label row), with the label scan as fallback; `standard_concept` must not be
used. **[R2]** Also correct the "ABSOLUTE USD, no scaling" claim at `_edgar_facts.py:7-10` —
MCD's `diluted_shares` are in millions.

- [ ] **Step 4: TODO.md**

Close the EDGAR-extraction follow-up with the measured before/after. Record root cause B
(9 tickers) and the `get_shares_outstanding_diluted()` units hazard as still open.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src tests && uv run pytest -q
git add CLAUDE.md TODO.md src/shortlist/providers/_edgar_facts.py \
    docs/audits/2026-07-31-edgar-concept-match.md
git commit -m "docs(edgar): concept-first row matching evidence + close the diluted-shares gap"
```

---

## Deferred

**Root cause B (9 tickers, concept absent).** Disjoint from A/C: the concept-first path
returns no rows, so these land on today's exact behaviour. Coverage goes 26/42 → 33/42 with no
half-fixed state. **[R2] Caveat: the residual absence is non-random** — CMCSA/CVX/GOOGL/HON/
LMT/MO/MRK/PG/XOM skew to old-line industrials, energy and pharma. Harmless for an advisory
flag; a selection bias for a scored leg. **Do not enable `quality.dilution` until B is closed.**

1. **Raw companyfacts.** `_xbrl_facts.py` already reads this tag for the backtest path, so the
   data likely exists at source. Cost: a per-ticker fetch (~2.5 MB/CIK) the harness avoids on
   the hot path. **Measure its real coverage first** — this is the route to try.
2. **Derive `shares = net_income / diluted_eps`.** **Circularity hazard:** valid only when EPS
   came from the reported row; deriving from a computed EPS would fabricate a flat share count
   and silently satisfy the `dilution` flag. The provenance flag falls out of Task 1 cheaply,
   and all 9 B-tickers currently carry as-reported EPS, so it is applicable today.
   **[R2] Error budget is larger than rounding.** `NetIncomeLoss` is consolidated, while
   diluted EPS is computed on income attributable to *common* shareholders (after
   noncontrolling interests and preferred dividends). A constant NCI fraction cancels in a
   CAGR; a **drifting** one does not — and CMCSA, XOM and MO are the NCI-heavy names in this
   list. Quantify NCI drift, not just 2-dp rounding (which is 0.5% on a $1 EPS, not 0.05%).

**[R2] `get_shares_outstanding_diluted()` units hazard.** Returns MCD's count in millions.
This plan removes MCD's dependence on it but does not fix the function. Any future consumer
inherits the bug.

**[R2] Adjacent cheap win.** The computed fallback divides by a *scalar*; `fin.diluted_shares`
is keyed on the same `inc_fy` axis and could be hoisted above it, giving a per-year divisor.
Strictly better, and it would already have saved DIS/MCD/UNH. It is route 2 in reverse, so
the two decisions inform each other.

## Done When

- `uv run ruff check src tests` clean; `uv run pytest -q` green.
- The 42-ticker before/after shows exactly the expected sets changing and nothing else.
- `docs/audits/2026-07-31-edgar-concept-match.md` committed.
- `CLAUDE.md`/`TODO.md` state the value-aware concept-first rule, root cause B, and the units
  hazard.
- **Not done here:** deployment, root cause B, the units bug in `get_shares_outstanding_diluted()`.
