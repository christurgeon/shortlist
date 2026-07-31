# EDGAR diluted-shares/EPS extraction — concept-matching fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Recover `diluted_shares` (and as-reported `diluted_eps`) for the ~38% of EDGAR-covered issuers where extraction silently yields `[]`, by matching the authoritative raw us-gaap `concept` instead of the filer-chosen `label` text.

**Architecture:** `providers/_edgar_facts.py` picks statement rows by scanning the human-readable `label` column. Labels are filer presentation text and vary wildly; the raw `concept` column is the authoritative XBRL tag and is stable. Add a concept-first lookup, keep the existing label scan as a fallback so no currently-working issuer regresses.

**Tech Stack:** Python 3.11+, pandas (already a dep via edgartools), pytest, uv.

## Evidence — measured, not assumed (2026-07-31)

Prevalence, from the 42 EDGAR-won tickers in the production accumulation store
(`/opt/shortlist/state/snapshots`, latest snapshot each):

- **`diluted_shares` empty for 16/42 = 38%**: CMCSA COST CVX GOOGL HON IBM LMT MO MRK MSFT ORCL PEP PG QCOM VZ XOM

Classified against live EDGAR income statements, those 16 split into **two distinct root causes**:

**Root cause A — label mismatch (7 of 16). THIS PLAN FIXES THIS.**
The concept `us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding` is present with
complete values for every fiscal-year column, but `_row_diluted_shares` requires the label
to contain both `"diluted"` and `"shares"`:

| ticker | actual label | why the matcher misses |
|---|---|---|
| COST, MSFT, ORCL, PEP, QCOM | `'Diluted'` | no `"shares"` |
| IBM | `'Assuming dilution (in shares)'` | no `"diluted"` — it says *dilution* |
| VZ | `'Weighted-average shares outstanding (in shares)'` | no `"diluted"` |

Compare AAPL, which works: `'Diluted (in shares)'`. The distinguishing context for MSFT-style
filers lives in a separate abstract parent row (`'Weighted average shares outstanding:'`,
concept `…SharesOutstandingAbstract`, all-`NaN`) that the flat dataframe does not associate
with its children.

**Root cause B — concept genuinely absent (9 of 16). OUT OF SCOPE, see §Deferred.**
CMCSA CVX GOOGL HON LMT MO MRK PG XOM. Probed GOOGL/LMT/XOM: the only share-related concepts
in the income statement are `us-gaap_EarningsPerShareBasic` and `…Diluted`. No share-count
tag at any label. edgartools' income-statement view simply does not carry it for these filers.

**Root cause C — the same label bug in `_row_diluted_eps` (4 issuers), with a worse
consequence.** `_row_diluted_eps` requires `"per share"` in the label. COST/MSFT/ORCL/PEP
label their EPS row just `'Diluted'`, so it misses and `extract_financials` falls through to
its computed fallback (`_edgar_facts.py:283-284`):

```python
if not eps and fin.net_income and shares_diluted:
    eps = [ni / shares_diluted for ni in fin.net_income]
```

`shares_diluted` is a **single scalar** (today's count from `get_shares_outstanding_diluted()`)
divided into **every** year's net income — so historical EPS is computed against the current
share count. Confirmed in the store: MSFT's persisted `diluted_eps[0]` is
`17.94565946598685` (computed) where the filing reports `17.95`; AAPL's is the exact reported
`7.46`. Error scales with buyback/issuance drift over the window.

**This is now higher-stakes than before.** The just-merged statements fix (#154) makes
`diluted_eps` reach `pe_ttm`/`pe_median_5y` via the EDGAR PE fallback (`bridge.py:241,:243`),
feeding the scored `pe_vs_history` value leg. A computed-EPS series now moves `composite`.

## Global Constraints

- **Match the raw `concept` column, never `standard_concept`.** Established repo rule:
  `_concept_family_latest` in this same file already does exactly this, and
  `docs/audits/2026-07-12-accruals-leg-disable.md` records `standard_concept` bucket names
  drifting across edgartools releases and silently breaking a leg.
- **No new fetch, no new dependency, no new config block.** The `concept` column is already
  in the dataframes being parsed.
- **No currently-working issuer may regress.** The label scan stays as a fallback.
- **`_series` stays all-or-nothing.** Do not relax it — a half-filled series would silently
  corrupt `cagr`.
- **LANDMINE:** a pandas `Series` in a boolean context raises
  `ValueError: The truth value of a Series is ambiguous`. Never write
  `row = _row_by_concept(...) or _label_scan(...)`. Use explicit `if row is not None:`.
- Drop `dimension == True` rows before matching (dimensional breakdowns double-count), the
  same guard `_concept_family_latest` uses.
- CI, in order: `uv run ruff check src tests`, then `uv run pytest -q`. Line length 110.

## Blast radius — declare it up front

This repo's rule is that live scoring-surface changes are named before shipping (the #154
review caught me omitting one). This change has **two**:

| Effect | Path | Surface |
|---|---|---|
| `diluted_shares` populates for 7 issuers | → `share_count_cagr` | `dilution` **flag** (ON); `quality.dilution` leg (OFF) |
| `diluted_eps` switches from **computed** to **as-reported** for 4 issuers | → `eps_cagr_ps`, and → `pe_ttm`/`pe_median_5y` → **`pe_vs_history`** | **scored `value` leg — moves `composite`, `confidence`, ranking** |

The second is a value *correction* (as-reported beats a stale-share-count approximation), but
it changes existing numbers for COST/MSFT/ORCL/PEP and must be pinned by a test.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/shortlist/providers/_edgar_facts.py` | `_row_by_concept` helper; concept-first `_row_diluted_shares` / `_row_diluted_eps` | Modify |
| `tests/test_edgar_facts_concept_match.py` | Label-shape fixtures from the 4 real patterns | Create |
| `docs/PLAN_EDGAR_DILUTED_SHARES.md` | This plan | — |
| `CLAUDE.md`, `TODO.md` | Document the rule + close the follow-up | Modify |

---

### Task 1: Concept-first row matching

**Files:**
- Modify: `src/shortlist/providers/_edgar_facts.py`
- Test: `tests/test_edgar_facts_concept_match.py` (create)

**Interfaces produced:**
- `_row_by_concept(df: pd.DataFrame, concepts: tuple[str, ...]) -> Optional[pd.Series]`
- `_row_diluted_shares` / `_row_diluted_eps` — unchanged signatures, concept-first behaviour

- [ ] **Step 1: Write the failing tests**

Create `tests/test_edgar_facts_concept_match.py`. The label strings are **verbatim from live
EDGAR filings on 2026-07-31** — do not "tidy" them.

```python
from __future__ import annotations

import pandas as pd

from shortlist.providers._edgar_facts import _row_diluted_eps, _row_diluted_shares

SHARES = "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding"
BASIC = "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic"
ABSTRACT = "us-gaap_WeightedAverageNumberOfSharesOutstandingAbstract"
EPS = "us-gaap_EarningsPerShareDiluted"

FY = ["2025-06-30 (FY)", "2024-06-30 (FY)", "2023-06-30 (FY)"]


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _row(label, concept, vals, dimension=False):
    d = {"label": label, "concept": concept, "dimension": dimension}
    d.update(dict(zip(FY, vals, strict=True)))
    return d


def test_msft_style_bare_diluted_label_is_matched_by_concept():
    # MSFT/COST/ORCL/PEP/QCOM label the share-count row just 'Diluted' — the
    # label scan requires "shares" and misses it. The concept is authoritative.
    df = _df([
        _row("Diluted", EPS, [17.95, 13.64, 11.80]),
        _row("Weighted average shares outstanding:", ABSTRACT, [None, None, None]),
        _row("Basic", BASIC, [7_430e6, 7_440e6, 7_450e6]),
        _row("Diluted", SHARES, [7_453e6, 7_465e6, 7_469e6]),
    ])
    row = _row_diluted_shares(df)
    assert row is not None
    assert row["concept"] == SHARES          # the COUNT row, not the EPS row
    assert row[FY[0]] == 7_453e6


def test_ibm_style_assuming_dilution_label_is_matched_by_concept():
    # IBM: 'Assuming dilution (in shares)' — contains neither "diluted" nor a
    # canonical keyword the label scan looks for.
    df = _df([
        _row("Assuming dilution (in shares)", SHARES, [920e6, 925e6, 930e6]),
    ])
    row = _row_diluted_shares(df)
    assert row is not None and row[FY[0]] == 920e6


def test_vz_style_label_without_the_word_diluted_is_matched_by_concept():
    df = _df([
        _row("Weighted-average shares outstanding (in shares)", SHARES, [4_2e8, 4_2e8, 4_2e8]),
    ])
    assert _row_diluted_shares(df) is not None


def test_aapl_style_label_still_works_no_regression():
    # AAPL already worked via the label scan; it must keep working.
    df = _df([
        _row("Diluted (in shares)", SHARES, [15.0e9, 15.4e9, 15.8e9]),
    ])
    row = _row_diluted_shares(df)
    assert row is not None and row[FY[0]] == 15.0e9


def test_label_scan_still_works_when_no_concept_column_exists():
    # Older/other dataframes may lack `concept` entirely -> fall back to labels.
    df = pd.DataFrame([{"label": "Weighted average diluted shares", **dict(zip(FY, [1.0, 2.0, 3.0], strict=True))}])
    row = _row_diluted_shares(df)
    assert row is not None and row[FY[0]] == 1.0


def test_basic_share_row_is_never_returned():
    df = _df([_row("Basic (in shares)", BASIC, [1.0, 2.0, 3.0])])
    assert _row_diluted_shares(df) is None


def test_all_nan_abstract_parent_row_is_never_returned():
    df = _df([_row("Weighted average shares outstanding:", ABSTRACT, [None, None, None])])
    assert _row_diluted_shares(df) is None


def test_dimensional_breakdown_rows_are_ignored():
    df = _df([
        _row("Diluted", SHARES, [99.0, 99.0, 99.0], dimension=True),
        _row("Diluted", SHARES, [7.0, 8.0, 9.0]),
    ])
    row = _row_diluted_shares(df)
    assert row is not None and row[FY[0]] == 7.0


def test_msft_style_bare_diluted_eps_label_is_matched_by_concept():
    # Root cause C: same bug on EPS. Without this the computed fallback
    # (net_income / TODAY's share scalar) silently replaces reported EPS.
    df = _df([
        _row("Diluted", EPS, [17.95, 13.64, 11.80]),
        _row("Diluted", SHARES, [7_453e6, 7_465e6, 7_469e6]),
    ])
    row = _row_diluted_eps(df)
    assert row is not None
    assert row["concept"] == EPS and row[FY[0]] == 17.95


def test_diluted_eps_label_path_still_works():
    df = pd.DataFrame([{"label": "Diluted (in dollars per share)",
                        **dict(zip(FY, [7.46, 6.08, 6.13], strict=True))}])
    row = _row_diluted_eps(df)
    assert row is not None and row[FY[0]] == 7.46
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_edgar_facts_concept_match.py -q`
Expected: FAIL — the MSFT/IBM/VZ share tests return `None` (label scan misses), and
`test_msft_style_bare_diluted_eps_label_is_matched_by_concept` returns `None`.

- [ ] **Step 3: Implement**

In `src/shortlist/providers/_edgar_facts.py`, add above `_row_diluted_eps`:

```python
# Authoritative raw us-gaap tags. Matched on the `concept` column, NOT `label`
# (filer presentation text: MSFT/COST/ORCL/PEP label the share-count row just
# "Diluted", IBM "Assuming dilution (in shares)", VZ omits "diluted" entirely —
# 7 of 42 production tickers extracted EMPTY on labels alone) and NOT
# `standard_concept` (bucket names drift across edgartools releases and have
# silently broken a leg before — docs/audits/2026-07-12-accruals-leg-disable.md).
_DILUTED_SHARES_CONCEPTS = ("us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",)
_DILUTED_EPS_CONCEPTS = ("us-gaap_EarningsPerShareDiluted",)


def _row_by_concept(df: pd.DataFrame, concepts: tuple[str, ...]) -> Optional[pd.Series]:
    """First non-dimensional row whose raw `concept` exactly equals one of
    `concepts`, in the order given. None when the column or the tag is absent."""
    if "concept" not in df.columns:
        return None
    rows = df
    if "dimension" in rows.columns:
        rows = rows[rows["dimension"] != True]      # noqa: E712 — drop breakdowns
    col = rows["concept"].astype(str)
    for c in concepts:
        hit = rows[col == c]
        if not hit.empty:
            return hit.iloc[0]
    return None
```

Then make each row-picker concept-first. **Do not use `or` between them** — a `Series` in a
boolean context raises `ValueError: The truth value of a Series is ambiguous`:

```python
def _row_diluted_shares(df: pd.DataFrame) -> Optional[pd.Series]:
    """...<keep the existing docstring, and add:>...

    Concept-first: the raw us-gaap tag is authoritative and stable; the label
    scan below is the fallback for dataframes without a `concept` column."""
    row = _row_by_concept(df, _DILUTED_SHARES_CONCEPTS)
    if row is not None:
        return row
    # ... existing label-scan body unchanged ...
```

Apply the identical concept-first prelude to `_row_diluted_eps` with `_DILUTED_EPS_CONCEPTS`.

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_edgar_facts_concept_match.py -q` → PASS (10 tests)

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: green. `tests/test_edgar_leverage_live.py` is `-m live`-marked and deselected by
default; do not run it (it hits SEC).

- [ ] **Step 6: Commit**

```bash
uv run ruff check src tests
git add src/shortlist/providers/_edgar_facts.py tests/test_edgar_facts_concept_match.py
git commit -m "fix(edgar): match diluted share/EPS rows by raw concept, not filer label"
```

---

### Task 2: Live verification + docs

**Files:** `CLAUDE.md`, `TODO.md`

- [ ] **Step 1: Live before/after — mandatory, keyless, no FMP quota needed**

Run against real EDGAR (needs `SEC_IDENTITY` from `.env`; `set -a && . ./.env && set +a`):

```bash
uv run --extra edgar python -c "
import os,sys; sys.path.insert(0,'src')
from edgar import Company, set_identity
set_identity(os.environ['SEC_IDENTITY'])
from shortlist.providers._edgar_facts import extract_financials
for tk in ['MSFT','COST','ORCL','PEP','QCOM','IBM','VZ','AAPL','AMZN']:
    f=Company(tk).get_financials()
    try: sh=f.get_shares_outstanding_diluted()
    except Exception: sh=None
    ef=extract_financials(f.income_statement().to_dataframe(),
                          f.cashflow_statement().to_dataframe(),
                          f.balance_sheet().to_dataframe(), shares_diluted=sh)
    print(tk, 'shares=', ef.diluted_shares[:3], 'eps=', ef.diluted_eps[:3])
"
```

Expected: the 7 root-cause-A tickers go from `shares=[]` to three real values; AAPL/AMZN
(already working) are **unchanged**; COST/MSFT/ORCL/PEP `eps` become the as-reported
2-decimal values rather than long computed floats. Record the actual output in the commit
message. If AAPL or AMZN changes, STOP — that is a regression.

- [ ] **Step 2: CLAUDE.md**

In the EDGAR section that documents `_edgar_facts.py`, add: row selection matches the **raw
`concept`** column first (label text is filer presentation and varies — `'Diluted'`,
`'Assuming dilution (in shares)'`, `'Weighted-average shares outstanding (in shares)'` all
denote the same tag), with the label scan as fallback; and that `standard_concept` must not
be used (release drift).

- [ ] **Step 3: TODO.md**

Close the EDGAR-extraction follow-up recorded in the statements-merge entry, citing the
measured before/after. Record root cause B (9 tickers, concept absent) as still open.

- [ ] **Step 4: Commit**

```bash
uv run ruff check src tests && uv run pytest -q
git add CLAUDE.md TODO.md && git commit -m "docs(edgar): document concept-first row matching; close the diluted-shares gap"
```

---

## Deferred — root cause B (9 of 16), needs its own investigation

CMCSA CVX GOOGL HON LMT MO MRK PG XOM carry **no** share-count concept in edgartools'
income-statement view (probed: only `EarningsPerShareBasic`/`Diluted`). Two candidate routes,
neither in scope here:

1. **Raw companyfacts.** `_xbrl_facts.py` already reads
   `WeightedAverageNumberOfDilutedSharesOutstanding` from companyfacts for the backtest path,
   so the data very likely exists at source. Cost: a new per-ticker fetch (~2.5 MB/CIK
   cached), which the harness deliberately avoids on the hot path.
2. **Derive `shares = net_income / diluted_eps`.** Both are present for this group, and the
   2-dp EPS rounding implies ~0.05% error — negligible against the 3%/yr `dilution` threshold.
   **Hard prerequisite:** only valid when `diluted_eps` came from the *reported row*. If EPS
   itself fell through to the computed fallback, deriving shares from it is **circular** and
   would fabricate a flat share count. `extract_financials` does not currently track EPS
   provenance, so this route needs that flag first.

Recommend measuring route 1's real coverage on companyfacts before building either.

## Done When

- `uv run ruff check src tests` clean; `uv run pytest -q` green.
- The live before/after in Task 2 Step 1 shows 7 tickers recovering and AAPL/AMZN unchanged,
  with the actual output recorded.
- `CLAUDE.md`/`TODO.md` state the concept-first rule and the still-open root cause B.
- **Not done here:** deployment, and root cause B.
