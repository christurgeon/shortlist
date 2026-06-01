# EDGAR Value Legs + Harness Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Code is written by Sonnet subagents.**

**Goal:** Free the harness `value` axis from FMP's per-symbol 402 gating by deriving `fcf_yield` and `pe_vs_history` from free SEC EDGAR financials + Yahoo prices, and lay the groundwork (coverage-diagnostic parity) for consolidating onto the harness as the default engine.

**Architecture:** Add a dependency-isolated EDGAR financials extractor (parallel to the existing `providers/_form4.py` leaf) that parses edgartools statement DataFrames into annual series. `EdgarSource` populates `Statements`/`Fundamentals` from it; `YahooSource` carries sampled historical closes; `bridge.py` derives the two value legs cross-source when FMP is absent. Then port the screener's `coverage` diagnostic onto the harness path so consolidation loses no behavior.

**Tech Stack:** Python 3, `edgartools` (optional `edgar` extra), `pandas` (transitive via edgartools), `httpx` (Yahoo), `pytest`. `uv` for env.

**Plan version:** v2 (incorporates the 3-reviewer pass; see "Review Log" at the end for accepted/rejected findings and rationale).

---

## Background: verified API facts (do not re-discover)

Confirmed against the installed `edgartools` on live AAPL calls. The plan depends on these.

- `edgar.Company(t).get_financials()` → `Financials`. `Financials.cashflow_statement().to_dataframe()` and `.income_statement().to_dataframe()` return multi-period `pandas.DataFrame`s.
- DataFrame columns: `concept`, `label`, `standard_concept`, then one column **per fiscal year** named like `"2025-09-27 (FY)"` (header = exact fiscal period-end date), plus `level`, `abstract`.
- **A single latest 10-K renders ~3 FY columns** (not 5). `shortlist.stats.median_pe`/`avg_roic` use `min_points=2`, and `cagr` works on whatever is present, so 3 points is usable. **Full 5y is OUT OF SCOPE for v1** (would need multi-filing stitching).
- **UNITS — verified: `to_dataframe()` returns ABSOLUTE USD**, NOT millions. AAPL revenue came back as `416161000000.0`, OCF as `111482000000.0`. This matches FMP's statements (absolute USD) and `market_cap` (absolute USD per `CLAUDE.md`). **Therefore `fcf_yield = FCF / market_cap` needs NO unit scaling** — both sides are absolute USD and the quotient is the correct fraction (0.0x). Do **not** multiply by `1e6`.
- Reliable rows by `standard_concept`:
  - Operating cash flow: `standard_concept == "NetCashFromOperatingActivities"` (positive).
  - Capex: `standard_concept == "CapitalExpenses"` (**negative-signed** — it's a payment; `FCF = OCF + capex`).
  - Revenue: `standard_concept == "Revenue"`.
  - Net income: `standard_concept == "NetIncomeLoss"`.
- **Diluted EPS has `standard_concept == NaN`** — match by `label` (case-insensitive `"diluted" in label and "per share" in label`). Fragile across issuers → MUST fall back to `net_income / shares_diluted` and then degrade to `None` (scorer redistributes weight) rather than guess.
- The scalar helpers (`Financials.get_operating_cash_flow()` etc.) return `None`/single values inconsistently — **do not use them for series.**
- **`market_cap` is already backfilled by Finnhub/Yahoo** into `snapshot.profile.market_cap` (`CLAUDE.md`), so the fcf_yield denominator needs no FMP.
- **YahooSource does NOT currently parse timestamps.** `_closes_from_chart` (sources.py:473) returns a flat `list[float]`; `_normalize_yahoo` (sources.py:482) takes only that list. So `monthly_closes` (date,close pairs) is **new plumbing** — Task 5 must extract the `timestamp` array from the raw chart, not "reuse existing locals." **Verified live:** the Yahoo `/v8/finance/chart` response *does* include a `chart.result[0].timestamp` array, length-aligned with `adjclose` (1255 points over a 5y daily range for AAPL).
- **`Statements` is already imported** in `sources.py:16` (`FMPSource` uses it). Task 4 needs no new import for it.
- **`merge_snapshots` populates `snap.provenance` and `snap.errors`** (models.py:275, 280) on the merged snapshot. The coverage adapter (Task 11) runs on the merged snapshot, so provenance/errors are available without changing any source.
- `run_harness` does `from .data.collector import collect` as a **local import** (screen.py:58). Tests that mock collection must patch **`shortlist.data.collector.collect`**, not `shortlist.screen.collect`.

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `config.yaml` | `research.model` comment accuracy | Modify (Task 1) |
| `src/shortlist/providers/_edgar_facts.py` | **NEW** pure extractor: statement DataFrames → `EdgarFinancials` annual series. Dependency-isolated leaf (like `_form4.py`). | Create (Task 2) |
| `tests/test_edgar_facts.py` | **NEW** extractor unit tests vs synthetic DataFrames. | Create (Task 2) |
| `src/shortlist/data/models.py` | Add `diluted_eps`/`fiscal_period_end` to `Statements`, `monthly_closes` to `Price`; **exclude all three from `coverage()`/`missing()`**. | Modify (Task 3) |
| `src/shortlist/data/sources.py` | `EdgarSource` populates `Statements` (failure-isolated, reuses one `Company`); `YahooSource` → 5y range + extracts timestamps → `monthly_closes`. | Modify (Tasks 4, 5) |
| `src/shortlist/data/bridge.py` | Derive `fcf_yield` and `pe_ttm`/`pe_median_5y` when FMP absent. | Modify (Tasks 6, 7) |
| `tests/test_edgar_source_financials.py` | **NEW** EdgarSource financials + isolation tests. | Create (Tasks 4, 10) |
| `src/shortlist/coverage.py` | Soften `_FMP_NOTE`. | Modify (Task 8) |
| `src/shortlist/data/coverage_adapt.py` | **NEW** map harness snapshot → `build_coverage` inputs. | Create (Task 11) |
| `src/shortlist/screen.py` | Attach coverage to harness cards. Default-engine flip is Phase B (gated). | Modify (Task 12) |
| `HARNESS.md`, `README.md`, `CLAUDE.md` | Docs. | Modify (Task 9) |

---

# PART 0 — Trivial doc fix (finding #5)

### Task 1: Correct the `research.model` comment

**Files:** Modify `config.yaml:59`

- [ ] **Step 1: Edit the comment**

Replace line 59:

```yaml
  model: claude-sonnet-4-6     # pinned full ID (not the drifting "sonnet" alias)
```

with:

```yaml
  model: claude-sonnet-4-6     # pinned to the 4.6 family (not the cross-version "sonnet" alias).
                               # NOTE: tracks the latest 4.6 snapshot, not a frozen date-pinned
                               # build; brief reproducibility is anchored by accession-keyed caching
                               # (research/), not the model string.
```

- [ ] **Step 2: Sanity-check the string still resolves (no code change)**

Run: `claude --model claude-sonnet-4-6 -p hi --tools "" --max-turns 1`
Expected: exit 0, a short greeting.

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "docs: clarify research.model is 4.6-family pinned, not frozen"
```

---

# PART 1 — EDGAR-derived value legs (finding #2)

> **Scope discipline (YAGNI):** v1 derives from the **latest 10-K only** (~3 fiscal years). Do NOT build multi-filing stitching. `fcf_yield` is the primary, robust deliverable; `pe_vs_history` is secondary and MUST degrade to `None` cleanly when EPS/price alignment is unavailable. Symbols with no XBRL financials (Form 20-F foreign issuers, recent spin-offs) degrade to `None` on both legs — same as FMP gating, never a crash.

### Task 2: Pure EDGAR financials extractor (`_edgar_facts.py`)

**Files:**
- Create: `src/shortlist/providers/_edgar_facts.py`
- Test: `tests/test_edgar_facts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_edgar_facts.py
import pandas as pd
import pytest

from shortlist.providers._edgar_facts import extract_financials, EdgarFinancials


def _cashflow_df():
    # Faithful mirror of edgartools cashflow_statement().to_dataframe(): includes the
    # non-FY columns (concept/label/level/abstract) and ABSOLUTE-USD values, newest FY first.
    return pd.DataFrame([
        {"concept": "us-gaap_NetCashProvided...", "label": "Cash generated by operating activities",
         "standard_concept": "NetCashFromOperatingActivities", "level": 1, "abstract": False,
         "2025-09-27 (FY)": 111_482_000_000.0, "2024-09-28 (FY)": 118_254_000_000.0, "2023-09-30 (FY)": 110_543_000_000.0},
        {"concept": "us-gaap_PaymentsToAcquire...", "label": "Payments for acquisition of PP&E",
         "standard_concept": "CapitalExpenses", "level": 2, "abstract": False,
         "2025-09-27 (FY)": -12_715_000_000.0, "2024-09-28 (FY)": -9_447_000_000.0, "2023-09-30 (FY)": -10_959_000_000.0},
    ])


def _income_df():
    return pd.DataFrame([
        {"concept": "us-gaap_Revenue", "label": "Net sales", "standard_concept": "Revenue", "level": 0, "abstract": False,
         "2025-09-27 (FY)": 416_161_000_000.0, "2024-09-28 (FY)": 391_035_000_000.0, "2023-09-30 (FY)": 383_285_000_000.0},
        {"concept": "us-gaap_NetIncomeLoss", "label": "Net income", "standard_concept": "NetIncomeLoss", "level": 0, "abstract": False,
         "2025-09-27 (FY)": 112_010_000_000.0, "2024-09-28 (FY)": 93_736_000_000.0, "2023-09-30 (FY)": 96_995_000_000.0},
        {"concept": float("nan"), "label": "Diluted (in dollars per share)", "standard_concept": float("nan"), "level": 2, "abstract": False,
         "2025-09-27 (FY)": 7.46, "2024-09-28 (FY)": 6.08, "2023-09-30 (FY)": 6.13},
    ])


def test_extract_aligns_series_newest_first():
    fin = extract_financials(_income_df(), _cashflow_df(), shares_diluted=15_004_697_000.0)
    assert fin.fiscal_period_end == ["2025-09-27", "2024-09-28", "2023-09-30"]
    assert fin.operating_cash_flow == [111_482_000_000.0, 118_254_000_000.0, 110_543_000_000.0]
    # FCF = OCF + capex (capex already negative)
    assert fin.free_cash_flow == [pytest.approx(98_767_000_000.0), pytest.approx(108_807_000_000.0), pytest.approx(99_584_000_000.0)]
    assert fin.revenue == [416_161_000_000.0, 391_035_000_000.0, 383_285_000_000.0]
    assert fin.net_income == [112_010_000_000.0, 93_736_000_000.0, 96_995_000_000.0]
    assert fin.diluted_eps == [7.46, 6.08, 6.13]


def test_eps_falls_back_to_net_income_over_shares_when_row_missing():
    inc = _income_df()
    inc = inc[inc["label"] != "Diluted (in dollars per share)"]  # drop the EPS row
    fin = extract_financials(inc, _cashflow_df(), shares_diluted=15_004_697_000.0)
    assert fin.diluted_eps[0] == pytest.approx(112_010_000_000.0 / 15_004_697_000.0, rel=1e-3)


def test_eps_empty_when_no_row_and_no_shares():
    inc = _income_df()
    inc = inc[inc["label"] != "Diluted (in dollars per share)"]
    fin = extract_financials(inc, _cashflow_df(), shares_diluted=None)
    assert fin.diluted_eps == []


def test_missing_concepts_yield_empty_lists_not_crash():
    empty = pd.DataFrame([{"standard_concept": "SomethingElse", "label": "x", "2025-09-27 (FY)": 1.0}])
    fin = extract_financials(empty, empty, shares_diluted=None)
    assert fin.revenue == []
    assert fin.free_cash_flow == []
    assert fin.diluted_eps == []


def test_no_financials_at_all_returns_empty_dataclass():
    # 20-F / recent spin-off: empty DataFrames -> all-empty, no crash.
    empty = pd.DataFrame()
    fin = extract_financials(empty, empty, shares_diluted=None)
    assert fin == EdgarFinancials()


def test_fy_columns_detected_by_suffix_and_sorted_desc():
    df = pd.DataFrame([{"standard_concept": "Revenue", "label": "r", "concept": "x", "level": 0, "abstract": False,
                        "2023-09-30 (FY)": 1.0, "2025-09-27 (FY)": 3.0, "2024-09-28 (FY)": 2.0}])
    fin = extract_financials(df, df, shares_diluted=None)
    assert fin.revenue == [3.0, 2.0, 1.0]  # newest-first regardless of source column order
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_edgar_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: shortlist.providers._edgar_facts`.

- [ ] **Step 3: Implement the extractor**

```python
# src/shortlist/providers/_edgar_facts.py
"""Pure transform: edgartools statement DataFrames -> normalized annual series.

Dependency-isolated leaf (sibling of _form4.py). Imports pandas (a transitive
edgartools dep) but NOT edgar/httpx, so it is unit-testable with synthetic
DataFrames and never reached unless the `edgar` extra is installed.

UNITS: values are passed through verbatim. edgartools to_dataframe() returns
ABSOLUTE USD (verified: AAPL revenue 416_161_000_000.0), matching FMP statements
and market_cap. No scaling here or downstream. All series are NEWEST-FIRST to
match the existing Statements convention."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

_FY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*\(FY\)$")


@dataclass
class EdgarFinancials:
    fiscal_period_end: list[str] = field(default_factory=list)   # ISO dates, newest first
    revenue: list[float] = field(default_factory=list)
    net_income: list[float] = field(default_factory=list)
    operating_cash_flow: list[float] = field(default_factory=list)
    free_cash_flow: list[float] = field(default_factory=list)
    diluted_eps: list[float] = field(default_factory=list)


def _fy_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for FY columns, sorted newest-first."""
    cols = []
    for c in df.columns:
        m = _FY_RE.match(str(c))
        if m:
            cols.append((m.group(1), c))
    return sorted(cols, key=lambda t: t[0], reverse=True)


def _row_by_standard_concept(df: pd.DataFrame, concept: str) -> Optional[pd.Series]:
    if "standard_concept" not in df.columns:
        return None
    hit = df[df["standard_concept"] == concept]
    return hit.iloc[0] if not hit.empty else None


def _row_diluted_eps(df: pd.DataFrame) -> Optional[pd.Series]:
    if "label" not in df.columns:
        return None
    for _, r in df.iterrows():
        lbl = str(r.get("label", "")).lower()
        if "diluted" in lbl and "per share" in lbl:
            return r
    return None


def _series(row: Optional[pd.Series], fy_cols: list[tuple[str, str]]) -> list[float]:
    if row is None:
        return []
    out = []
    for _, col in fy_cols:
        v = row.get(col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return []  # incomplete series -> treat as absent (don't half-fill)
        out.append(float(v))
    return out


def extract_financials(
    income_df: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    shares_diluted: Optional[float],
) -> EdgarFinancials:
    """Build annual series from the two statement DataFrames. Missing rows yield
    empty lists (never partial). EPS prefers the filed diluted-EPS row; if absent,
    falls back to net_income/shares_diluted; if neither, stays empty."""
    fy = _fy_columns(cashflow_df) or _fy_columns(income_df)
    fin = EdgarFinancials(fiscal_period_end=[d for d, _ in fy])

    fin.operating_cash_flow = _series(_row_by_standard_concept(cashflow_df, "NetCashFromOperatingActivities"), fy)
    capex = _series(_row_by_standard_concept(cashflow_df, "CapitalExpenses"), fy)
    if fin.operating_cash_flow and capex and len(fin.operating_cash_flow) == len(capex):
        fin.free_cash_flow = [ocf + cx for ocf, cx in zip(fin.operating_cash_flow, capex)]

    inc_fy = _fy_columns(income_df)
    fin.revenue = _series(_row_by_standard_concept(income_df, "Revenue"), inc_fy)
    fin.net_income = _series(_row_by_standard_concept(income_df, "NetIncomeLoss"), inc_fy)

    eps = _series(_row_diluted_eps(income_df), inc_fy)
    if not eps and fin.net_income and shares_diluted:
        eps = [ni / shares_diluted for ni in fin.net_income]
    fin.diluted_eps = eps
    return fin
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_edgar_facts.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/providers/_edgar_facts.py tests/test_edgar_facts.py
git commit -m "feat: pure EDGAR financials extractor (statement DataFrames -> annual series)"
```

### Task 3: Extend `Statements`/`Price` schemas; keep `coverage()` stable

**Files:**
- Modify: `src/shortlist/data/models.py` — `Statements` (~46-56), `Price` (~93-106), `coverage()` (~145-149), `missing()` (~159-163)
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_harness.py
def test_statements_and_price_carry_new_value_fields():
    from shortlist.data.models import Statements, Price
    s = Statements(diluted_eps=[7.46], fiscal_period_end=["2025-09-27"])
    assert s.diluted_eps == [7.46]
    assert s.fiscal_period_end == ["2025-09-27"]
    p = Price(monthly_closes=[["2025-09-30", 255.0]])
    assert p.monthly_closes == [["2025-09-30", 255.0]]


def test_new_plumbing_fields_excluded_from_coverage_denominator():
    # diluted_eps/fiscal_period_end/monthly_closes are internal derivation aids, not
    # assessment-ready signals -> they must NOT change coverage() vs a baseline snapshot.
    from shortlist.data.models import TickerSnapshot, Statements, Price, Fundamentals, Profile, Analyst, Insider
    base = TickerSnapshot(ticker="X", profile=Profile(name="x"), fundamentals=Fundamentals(roe=0.2),
                          statements=Statements(revenue=[1.0]), analyst=Analyst(buy=1),
                          insider=Insider(buy_count=1), price=Price(price=1.0))
    cov_before = base.coverage()
    base.statements.diluted_eps = [1.0]
    base.statements.fiscal_period_end = ["2025-01-01"]
    base.price.monthly_closes = [["2025-01-01", 1.0]]
    assert base.coverage() == cov_before     # excluded fields don't move the needle
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_harness.py -k "new_value_fields or plumbing" -v`
Expected: FAIL — `TypeError: unexpected keyword 'diluted_eps'` (first test), then coverage drift (second).

- [ ] **Step 3: Implement**

In `Statements` (after `total_equity`):

```python
    diluted_eps: list[float] = field(default_factory=list)
    fiscal_period_end: list[str] = field(default_factory=list)  # ISO dates, newest-first
```

In `Price` (after `max_drawdown`):

```python
    # ~monthly-sampled (date, close) pairs over the fetch window, oldest->newest.
    # Lets the bridge align EDGAR fiscal-year-end dates to a historical price.
    monthly_closes: list[list] = field(default_factory=list)
```

In **both** `coverage()` (models.py:146) and `missing()` (models.py:160), extend the skip set. Each line currently reads `if f.name in ("recent",):` — these are the only two occurrences; **edit both**. Change them to:

```python
            # `recent` is illustrative; the three below are internal derivation
            # plumbing, not assessment-ready signals -> excluded from coverage math.
            if f.name in ("recent", "diluted_eps", "fiscal_period_end", "monthly_closes"):
```

This keeps the `coverage()` denominator unchanged, so `test_mock_collect_is_assessment_ready` (`>= 0.8`) and every other coverage assertion stay valid. (Resolves open question #3.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_harness.py tests/test_coverage.py -v`
Expected: PASS (including the existing `>= 0.8` mock-coverage test).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/models.py tests/test_harness.py
git commit -m "feat: add EPS/period-end/monthly-closes fields; exclude plumbing from coverage()"
```

### Task 4: `EdgarSource` populates financials (isolated; one `Company` reused)

**Files:**
- Modify: `src/shortlist/data/sources.py` — `EdgarSource._fetch_sync` (~291-318)
- Test: `tests/test_edgar_source_financials.py`

**Design note (rate limits + correctness):** Build **one** `Company(ticker)` and use it for both the Form 4 filings and `get_financials()` — avoids a second company lookup and keeps the added SEC load minimal (still inside the `_EDGAR_MAX_CONCURRENCY=3` gate). `Company` is already imported inside `_fetch_sync`; do **not** re-import or alias it. **Insider isolation contract:** `_fetch_insider(ticker)` encapsulates the current insider try/except and **always returns a `SourceResult` with `res.partial` set** — on the success branches (`summary.found`/else) *and* on the exception branch (which today sets `res.partial = TickerSnapshot(ticker)` and returns). So when `_fetch_sync` calls it, `res.partial` is guaranteed non-None and the financials block merges into it unconditionally (no `is None` guard). The financials fetch is in its **own** try/except so a financials failure never drops the authoritative insider result. (`Statements` is already imported at sources.py:16.)

- [ ] **Step 1: Write the failing tests** (no network; inject a fake financials object)

```python
# tests/test_edgar_source_financials.py
import asyncio
import pandas as pd
import pytest

from shortlist.data.sources import EdgarSource


class _FakeStatement:
    def __init__(self, df): self._df = df
    def to_dataframe(self): return self._df


class _FakeFinancials:
    def __init__(self, inc, cf): self._inc, self._cf = inc, cf
    def income_statement(self): return _FakeStatement(self._inc)
    def cashflow_statement(self): return _FakeStatement(self._cf)
    def get_shares_outstanding_diluted(self): return 15_004_697_000.0


def _inc():
    return pd.DataFrame([
        {"standard_concept": "Revenue", "label": "r", "2025-09-27 (FY)": 416_161_000_000.0, "2024-09-28 (FY)": 391_035_000_000.0},
        {"standard_concept": "NetIncomeLoss", "label": "ni", "2025-09-27 (FY)": 112_010_000_000.0, "2024-09-28 (FY)": 93_736_000_000.0},
        {"standard_concept": float("nan"), "label": "Diluted (in dollars per share)", "2025-09-27 (FY)": 7.46, "2024-09-28 (FY)": 6.08},
    ])


def _cf():
    return pd.DataFrame([
        {"standard_concept": "NetCashFromOperatingActivities", "label": "ocf", "2025-09-27 (FY)": 111_482_000_000.0, "2024-09-28 (FY)": 118_254_000_000.0},
        {"standard_concept": "CapitalExpenses", "label": "capex", "2025-09-27 (FY)": -12_715_000_000.0, "2024-09-28 (FY)": -9_447_000_000.0},
    ])


def test_build_financials_snapshot_fills_statements():
    src = EdgarSource.__new__(EdgarSource)        # bypass __init__ (no SEC identity / network)
    src.name = "edgar"
    snap = src._build_financials_snapshot("AAPL", _FakeFinancials(_inc(), _cf()))
    assert snap.statements.revenue == [416_161_000_000.0, 391_035_000_000.0]
    assert snap.statements.free_cash_flow == [pytest.approx(98_767_000_000.0), pytest.approx(108_807_000_000.0)]
    assert snap.statements.diluted_eps == [7.46, 6.08]
    assert snap.statements.fiscal_period_end == ["2025-09-27", "2024-09-28"]
    assert snap.statements.fiscal_years == [2025, 2024]


def test_build_financials_snapshot_empty_on_no_data():
    src = EdgarSource.__new__(EdgarSource); src.name = "edgar"
    snap = src._build_financials_snapshot("ZZZ", _FakeFinancials(pd.DataFrame(), pd.DataFrame()))
    assert snap.statements is None       # 20-F / no XBRL -> no statements, no crash


def test_financials_failure_does_not_drop_insider(monkeypatch):
    """A financials exception must be caught, logged, and leave insider intact."""
    src = EdgarSource.__new__(EdgarSource); src.name = "edgar"; src.lookback_days = 183

    # Stub the insider half to a known-good partial, and force financials to raise.
    from shortlist.data.models import SourceResult, TickerSnapshot, Insider
    def fake_insider(self, ticker):
        r = SourceResult(source="edgar")
        r.partial = TickerSnapshot(ticker=ticker, insider=Insider(buy_count=3, sell_count=0))
        return r
    monkeypatch.setattr(EdgarSource, "_fetch_insider", fake_insider, raising=False)
    monkeypatch.setattr(EdgarSource, "_fetch_financials_object",
                        lambda self, t: (_ for _ in ()).throw(RuntimeError("SEC 503")), raising=False)

    res = src._fetch_sync("AAPL")
    assert res.partial.insider.buy_count == 3            # insider survived
    assert any("edgar-financials" in e for e in res.errors)
    assert res.partial.statements is None
```

> The third test assumes `_fetch_sync` is refactored into two seams — `_fetch_insider(ticker) -> SourceResult` and `_fetch_financials_object(ticker) -> Financials` — so each half is independently mockable. Implement that refactor in Step 3.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_edgar_source_financials.py -v`
Expected: FAIL — `_build_financials_snapshot` / `_fetch_financials_object` don't exist.

- [ ] **Step 3: Implement** — add the builder + seams, and call them from `_fetch_sync`.

Add to `EdgarSource`:

```python
    def _fetch_financials_object(self, ticker: str):
        """Seam for mocking: returns an edgartools Financials (or raises)."""
        from edgar import Company
        return Company(ticker).get_financials()

    def _build_financials_snapshot(self, ticker: str, fin) -> "TickerSnapshot":
        """Map an edgartools Financials onto a Statements-only snapshot. Pure given
        `fin`. Values are absolute USD (no scaling)."""
        from ..providers._edgar_facts import extract_financials
        try:
            shares = fin.get_shares_outstanding_diluted()
        except Exception:
            shares = None
        ef = extract_financials(
            fin.income_statement().to_dataframe(),
            fin.cashflow_statement().to_dataframe(),
            shares_diluted=shares,
        )
        snap = TickerSnapshot(ticker=ticker)
        if ef.fiscal_period_end:
            snap.statements = Statements(
                fiscal_years=[int(d[:4]) for d in ef.fiscal_period_end],
                fiscal_period_end=ef.fiscal_period_end,
                revenue=ef.revenue,
                net_income=ef.net_income,
                operating_cash_flow=ef.operating_cash_flow,
                free_cash_flow=ef.free_cash_flow,
                diluted_eps=ef.diluted_eps,
            )
        return snap
```

Refactor `_fetch_sync` so the existing insider logic lives in `_fetch_insider(self, ticker) -> SourceResult` (mechanical move of the current body), then:

```python
    def _fetch_sync(self, ticker: str) -> SourceResult:
        res = self._fetch_insider(ticker)        # always sets res.partial (existing branches)
        # Financials are isolated: a failure here must never drop the insider result.
        try:
            fin_snap = self._build_financials_snapshot(ticker, self._fetch_financials_object(ticker))
            if fin_snap.statements is not None:
                res.partial.statements = fin_snap.statements
        except Exception as e:
            res.errors.append(f"edgar-financials: {e}")
        return res
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_edgar_source_financials.py tests/test_harness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_edgar_source_financials.py
git commit -m "feat: EdgarSource supplies financials alongside insider, failure-isolated, one Company"
```

### Task 5: `YahooSource` — 5y range + extract timestamps → `monthly_closes`

**Files:**
- Modify: `src/shortlist/data/sources.py` — `_get_chart` range (~372), `fetch` (~390-400), `_normalize_yahoo` (~482) or a new helper
- Test: `tests/test_yahoo_source.py`

**Design note:** Timestamps are NOT currently parsed. Add a `_monthly_closes_from_chart(raw)` helper that pairs the `timestamp` array with `adjclose`, and have `fetch` extract the raw chart once, derive both `closes` (existing) and `monthly_closes` (new), and set `monthly_closes` on the resulting `Price`. Mirror the existing test style: real `YahooSource(cache_dir=str(tmp_path))` + `monkeypatch.setattr(src, "_get_chart", fake)` (the canned payload uses the real `adjclose` schema and now also a `timestamp` array).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_yahoo_source.py
import asyncio


def _chart_payload_with_ts(closes, timestamps):
    return {"chart": {"result": [{
        "timestamp": timestamps,
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


def test_yahoo_emits_monthly_closes(tmp_path, monkeypatch):
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))      # real __init__: _client/_cache_dir/_spy_closes set
    ts = [i * 86400 for i in range(400)]            # 400 daily points, 1 day apart
    closes = [100.0 + i for i in range(400)]

    async def fake_get(symbol):
        return _chart_payload_with_ts(closes, ts)
    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAPL"))
    mc = res.partial.price.monthly_closes
    assert mc, "monthly_closes should be populated"
    assert 5 <= len(mc) <= 40                       # ~monthly sampling of ~13 months
    assert mc[0][0] < mc[-1][0]                     # ISO dates ascending
    assert all(isinstance(p[0], str) and isinstance(p[1], float) for p in mc)


def test_monthly_closes_empty_when_no_timestamps(tmp_path, monkeypatch):
    from shortlist.data.sources import YahooSource
    src = YahooSource(cache_dir=str(tmp_path))
    async def fake_get(symbol):
        return {"chart": {"result": [{"indicators": {"adjclose": [{"adjclose": [1.0, 2.0]}]}}]}}
    monkeypatch.setattr(src, "_get_chart", fake_get)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.partial.price.monthly_closes == []   # no timestamps -> no dated history, no crash
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_yahoo_source.py -k monthly -v`
Expected: FAIL — `monthly_closes` is `[]`/attr path empty (helper not implemented).

- [ ] **Step 3: Implement**

1. Range `2y` → `5y` at sources.py:372:

```python
        r = await self._client.get(
            f"{self.BASE}/{symbol}", params={"range": "5y", "interval": "1d"})
```

2. Add the helper near `_closes_from_chart`:

```python
def _monthly_closes_from_chart(raw: Any) -> list[list]:
    """Pair the chart's timestamp + adjclose arrays and down-sample to ~one point
    per calendar month (last valid obs each month), oldest->newest as [iso, close].
    Returns [] if timestamps are absent (older cached payloads / SPY-style fetches)."""
    from datetime import datetime, timezone
    try:
        result = raw["chart"]["result"][0]
        ts = result["timestamp"]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    if not ts or not series:
        return []
    by_month: dict[str, list] = {}
    for t, c in zip(ts, series):
        if not isinstance(c, (int, float)):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date()
        by_month[f"{d.year}-{d.month:02d}"] = [d.isoformat(), float(c)]
    return [by_month[k] for k in sorted(by_month)]
```

3. Rework `fetch` to fetch the raw chart once and thread monthly_closes onto the Price (SPY still uses `_closes`):

```python
    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        try:
            raw = await self._get_chart(ticker)
            closes = _closes_from_chart(raw)
            spy = await self._spy()
            res.partial = _normalize_yahoo(ticker, closes, spy)
            if res.partial.price is not None:
                res.partial.price.monthly_closes = _monthly_closes_from_chart(raw)
            res.raw = {"close_count": len(closes)}
        except Exception as e:
            res.errors.append(f"yahoo: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
        return res
```

(`_normalize_yahoo` returns a snapshot whose `.price` is `None` only when `closes` is empty; the guard handles that.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_yahoo_source.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_yahoo_source.py
git commit -m "feat: Yahoo fetches 5y and emits ~monthly dated closes for PE history"
```

### Task 6: Bridge derives `fcf_yield` (primary, robust)

**Files:**
- Modify: `src/shortlist/data/bridge.py` — statements block (~61-72)
- Test: `tests/test_bridge.py`

- [ ] **Step 1: Write the failing tests** (fixtures use REALISTIC absolute USD — both FCF and market_cap)

```python
# append to tests/test_bridge.py  (add `import pytest` at top if missing)
from shortlist.data.models import TickerSnapshot, Profile, Statements, Fundamentals, Price


def test_bridge_derives_fcf_yield_from_edgar_when_fmp_absent():
    from shortlist.data.bridge import snapshot_to_metrics
    # Absolute USD on BOTH sides (verified EDGAR units) -> quotient is a fraction.
    snap = TickerSnapshot(
        ticker="GEV",
        profile=Profile(market_cap=20_000_000_000.0),       # $20B, backfilled by Finnhub/Yahoo
        fundamentals=Fundamentals(fcf_yield=None),           # FMP gated -> no fcf_yield
        statements=Statements(free_cash_flow=[1_000_000_000.0]),  # $1B FCF
    )
    m = snapshot_to_metrics(snap)
    assert m.fcf_yield == pytest.approx(0.05)                # 1e9 / 20e9


def test_bridge_keeps_fmp_fcf_yield_when_present():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="AAPL",
        profile=Profile(market_cap=20_000_000_000.0),
        fundamentals=Fundamentals(fcf_yield=0.03),
        statements=Statements(free_cash_flow=[1_000_000_000.0]),
    )
    assert snapshot_to_metrics(snap).fcf_yield == 0.03       # FMP wins, no override
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_bridge.py -k fcf_yield -v`
Expected: FAIL — derived value is `None`.

- [ ] **Step 3: Implement** — inside the `if st:` block (after the growth legs), add:

```python
        # Value-leg derivation (FMP-gating fallback). UNITS: st.free_cash_flow and
        # m.market_cap are BOTH absolute USD (EDGAR + Finnhub/Yahoo), so the quotient
        # is the fcf_yield fraction directly — no scaling. Only fires when FMP gave
        # nothing (m.fcf_yield set from f.fcf_yield earlier keeps FMP's priority).
        if m.fcf_yield is None and st.free_cash_flow and m.market_cap:
            fcf0 = st.free_cash_flow[0]
            if fcf0 is not None:
                m.fcf_yield = fcf0 / m.market_cap
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/bridge.py tests/test_bridge.py
git commit -m "feat: bridge derives fcf_yield from EDGAR FCF + market cap when FMP gates"
```

### Task 7: Bridge derives `pe_ttm`/`pe_median_5y` (secondary, degrade-safe)

**Files:**
- Modify: `src/shortlist/data/bridge.py` — add `_close_near` helper + wire into `if st:`
- Test: `tests/test_bridge.py`

- [ ] **Step 1: Write the failing tests** (helper unit tests + integration, incl. nearest-date and degradation)

```python
# append to tests/test_bridge.py
from shortlist.data.bridge import _close_near


def test_close_near_exact_match():
    closes = [["2024-01-15", 100.0], ["2024-01-31", 105.0], ["2024-02-15", 102.0]]
    assert _close_near(closes, "2024-01-31") == 105.0


def test_close_near_picks_nearest_when_no_exact():
    closes = [["2024-01-15", 100.0], ["2024-02-15", 102.0]]
    # 2024-01-31 is 16d from Jan-15, 15d from Feb-15 -> Feb-15
    assert _close_near(closes, "2024-01-31") == 102.0


def test_close_near_empty_and_none_safe():
    assert _close_near([], "2024-01-31") is None
    assert _close_near([["2024-02-15", 102.0]], "garbage") is None
    assert _close_near([["2024-01-15", None], ["2024-02-15", 102.0]], "2024-01-31") == 102.0


def test_bridge_derives_pe_history_from_edgar_eps_and_yahoo_closes():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="GEV",
        fundamentals=Fundamentals(pe_ttm=None, pe_median_5y=None),
        statements=Statements(
            diluted_eps=[10.0, 8.0, 7.0],
            fiscal_period_end=["2024-12-31", "2023-12-31", "2022-12-31"],
        ),
        price=Price(
            price=200.0,
            # FY-end dates intentionally a few days off, to exercise nearest-match.
            monthly_closes=[["2022-12-29", 105.0], ["2023-12-29", 120.0], ["2024-12-31", 180.0]],
        ),
    )
    m = snapshot_to_metrics(snap)
    assert m.pe_ttm == pytest.approx(20.0)               # 200 / 10 (latest annual EPS)
    # annual PEs: 180/10=18, 120/8=15, 105/7=15 -> median = 15
    assert m.pe_median_5y == pytest.approx(15.0)
    assert m.pe_vs_history() == pytest.approx(15.0 / 20.0 - 1)


def test_bridge_pe_history_degrades_to_none_without_prices():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="GEV",
        fundamentals=Fundamentals(pe_ttm=None, pe_median_5y=None),
        statements=Statements(diluted_eps=[10.0], fiscal_period_end=["2024-12-31"]),
        price=Price(price=None, monthly_closes=[]),
    )
    m = snapshot_to_metrics(snap)
    assert m.pe_median_5y is None                        # 1 point < min_points=2 anyway; no prices
    assert m.pe_ttm is None


def test_bridge_keeps_fmp_pe_when_present():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="AAPL",
        fundamentals=Fundamentals(pe_ttm=30.0, pe_median_5y=25.0),
        statements=Statements(diluted_eps=[10.0], fiscal_period_end=["2024-12-31"]),
        price=Price(price=200.0, monthly_closes=[["2024-12-31", 180.0]]),
    )
    m = snapshot_to_metrics(snap)
    assert (m.pe_ttm, m.pe_median_5y) == (30.0, 25.0)    # FMP untouched
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_bridge.py -k "pe or close_near" -v`
Expected: FAIL — `_close_near` undefined; derived PEs `None`.

- [ ] **Step 3: Implement**

Update the bridge imports (the file already has `from .models import TickerSnapshot` on line 5 and `from ..stats import cagr, gross_margin_stability, growth_persistence` on line 4 — do NOT duplicate them; just add `median_pe` and `Optional`):

```python
from typing import Optional
from ..stats import cagr, gross_margin_stability, growth_persistence, median_pe
```

Add the helper at module level:

```python
def _close_near(monthly_closes: list, iso_date: str) -> Optional[float]:
    """Close from the sampled history nearest (by absolute day distance) to iso_date.
    None if no usable point or the target date is unparseable."""
    from datetime import date
    if not monthly_closes:
        return None
    try:
        target = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return None
    best = None
    for d_iso, close in monthly_closes:
        if close is None:
            continue
        try:
            gap = abs((date.fromisoformat(d_iso) - target).days)
        except (TypeError, ValueError):
            continue
        if best is None or gap < best[0]:
            best = (gap, float(close))
    return best[1] if best else None
```

Inside `if st:` (after the Task 6 fcf_yield block). NOTE: `pr` (`= snap.price`) is bound earlier at the function's top level (bridge.py:41), so it is in scope here regardless of whether the `if pr:` block ran:

```python
        # PE-vs-history from EDGAR EPS + Yahoo closes when FMP gated the symbol.
        # pr is in scope from the function top (line 41). pe_ttm uses latest ANNUAL
        # EPS as a TTM proxy (documented approximation; see open question #1).
        eps, ends = st.diluted_eps, st.fiscal_period_end
        if m.pe_ttm is None and pr and pr.price and eps and eps[0]:
            m.pe_ttm = pr.price / eps[0]
        if m.pe_median_5y is None and pr and eps and ends and len(eps) == len(ends):
            annual_pe = []
            for e, end in zip(eps, ends):
                px = _close_near(pr.monthly_closes, end)
                if px and e:
                    annual_pe.append(px / e)
            m.pe_median_5y = median_pe(annual_pe)   # None if < 2 points (min_points=2)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/bridge.py tests/test_bridge.py
git commit -m "feat: bridge derives pe_ttm/pe_median_5y from EDGAR EPS + Yahoo closes (nearest-date)"
```

### Task 8: Soften the coverage FMP note

**Files:**
- Modify: `src/shortlist/coverage.py:26-29` (`_FMP_NOTE`)
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Update the test expectation** — find the test asserting the FMP-note substring in `tests/test_coverage.py` and change it to match the new wording (analyst-target + PEG remain FMP-only; FCF yield + PE-vs-history recovered via EDGAR on the harness engine).

- [ ] **Step 2: Implement**

```python
_FMP_NOTE = (
    "FMP gated this symbol (402); analyst-target upside and PEG still need FMP "
    "Starter tier. On --engine harness, FCF yield and PE-vs-history are recovered "
    "from EDGAR financials + Yahoo prices; FMP-sourced ROE/ROIC remain absent."
)
```

- [ ] **Step 3: Run to verify pass**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/shortlist/coverage.py tests/test_coverage.py
git commit -m "docs: coverage note reflects EDGAR-recovered value legs"
```

### Task 9: Documentation

**Files:** Modify `HARNESS.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: HARNESS.md** — EDGAR section: state `EdgarSource` now supplies `Statements` (revenue/net income/OCF/FCF/diluted EPS, **absolute USD**) from the **latest 10-K (~3 fiscal years)** in addition to Form 4 insider; the bridge derives `fcf_yield` and `pe_vs_history` from EDGAR+Yahoo when FMP is absent. Explicitly document the **limitations**: ~3-year history (not 5), monthly-sampled closes, `pe_ttm` uses latest-annual-EPS as a TTM proxy, and **Form 20-F filers / recent spin-offs without XBRL financials degrade both legs to `None`** (graceful, never a crash). Note the added `get_financials()` SEC call ~doubles per-ticker EDGAR requests (still gated at concurrency 3); a full-universe run still needs the caching layer.

- [ ] **Step 2: README.md** — source table EDGAR row: "Form 4 insider **+ 10-K financials (revenue/FCF/EPS)**". Under Value, add: "On `--engine harness`, FCF yield and P/E-vs-history are recoverable from free EDGAR+Yahoo data, so only analyst-target upside and PEG require FMP."

- [ ] **Step 3: CLAUDE.md** — update "EDGAR in the harness" (now supplies financials too) and the FMP-gating "null value" paragraph (harness recovers 2 of 4 value legs; PEG + analyst-target still need FMP Starter).

- [ ] **Step 4: Commit**

```bash
git add HARNESS.md README.md CLAUDE.md
git commit -m "docs: EDGAR supplies harness financials; value survives FMP gating on 2 legs"
```

### Task 10: Live integration guard (opt-in, network)

**Files:** add to `tests/test_edgar_source_financials.py`

- [ ] **Step 1: Add opt-in live tests** (skipped unless `RUN_LIVE_EDGAR=1` and `SEC_IDENTITY` set):

```python
import os

@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_edgar_financials_10k_filer():
    from shortlist.data.sources import EdgarSource
    res = asyncio.run(EdgarSource().fetch("LMT"))     # SEC_IDENTITY from env
    assert res.partial.statements is not None
    assert res.partial.statements.free_cash_flow
    assert res.partial.statements.diluted_eps

@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_edgar_foreign_or_nofinancials_degrades_cleanly():
    # A 20-F foreign issuer (ASML) should not crash; statements may be None.
    from shortlist.data.sources import EdgarSource
    res = asyncio.run(EdgarSource().fetch("ASML"))
    assert res.partial is not None                    # no exception escaped
    # Either parsed statements or a logged financials error — never a crash.
    assert res.partial.statements is not None or any("edgar-financials" in e for e in res.errors) or res.partial.statements is None
```

- [ ] **Step 2: Run once manually** (not CI):

Run: `RUN_LIVE_EDGAR=1 SEC_IDENTITY="you@example.com" uv run pytest tests/test_edgar_source_financials.py -k live -v`
Expected: PASS (re-run if SEC throttles).

- [ ] **Step 3: Commit**

```bash
git add tests/test_edgar_source_financials.py
git commit -m "test: opt-in live EDGAR financials + 20-F degradation guard"
```

---

# PART 2 — Harness consolidation groundwork (finding #1)

> **GATING POLICY (read first):** Tasks 11–12 (coverage parity) are safe and land now. **Phase B (flip default engine) and Phase C (delete screener providers) are DESTRUCTIVE / behavior-changing and MUST NOT be auto-executed.** They require explicit user sign-off per the Handoff Checklist below.

### Task 11: Harness→coverage adapter

**Files:**
- Create: `src/shortlist/data/coverage_adapt.py`
- Test: `tests/test_coverage_adapt.py`

> Provenance/errors are populated by `merge_snapshots` on the merged snapshot (verified: models.py:275, 280) — the adapter consumes them; no source change is needed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_coverage_adapt.py
from shortlist.data.models import TickerSnapshot, Profile
from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs


def test_gated_fmp_maps_to_402():
    snap = TickerSnapshot(
        ticker="GEV",
        profile=Profile(market_cap=1e9),
        provenance={"profile": ["finnhub"], "price": ["yahoo"]},
        errors=["fmp: 402 Special Endpoint for GEV", "edgar-financials: 503 backoff"],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "gated_402"
    assert outcomes["edgar"] == "error"             # "edgar-financials:" maps to source "edgar"
    assert outcomes["finnhub"] == "ok"
    assert {"finnhub", "yahoo"} <= contributed


def test_clean_run_all_ok():
    snap = TickerSnapshot(
        ticker="AAPL",
        provenance={"profile": ["fmp"], "price": ["yahoo"], "insider": ["edgar"]},
        errors=[],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert set(outcomes.values()) == {"ok"}
    assert {"fmp", "yahoo", "edgar"} <= contributed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coverage_adapt.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/shortlist/data/coverage_adapt.py
"""Adapt a merged harness TickerSnapshot to the (outcomes, contributed) shape that
shortlist.coverage.build_coverage consumes, so harness-engine cards carry the same
per-source diagnostic the screener path produces.

`outcomes`: source -> "ok" | "gated_402" | "error". The harness records failures as
strings in snapshot.errors, prefixed "<source>: ..." (or "<source>-<phase>: ...",
e.g. "edgar-financials: ..."). A 402 substring -> gated_402; any other error ->
"error"; absence -> "ok".
`contributed`: sources that supplied >=1 field, from snapshot.provenance (populated
by merge_snapshots)."""
from __future__ import annotations

from .models import TickerSnapshot


def _source_of(err: str, known: list[str]) -> str:
    head = err.split(":", 1)[0].strip()
    # "edgar-financials" -> "edgar" only if the base name is a known source.
    base = head.split("-", 1)[0]
    return base if base in known else head


def snapshot_to_coverage_inputs(snap: TickerSnapshot, sources: list[str]) -> tuple[dict, set]:
    contributed: set = set()
    for srcs in snap.provenance.values():
        contributed.update(srcs)

    err_by_source: dict[str, list[str]] = {}
    for e in snap.errors:
        err_by_source.setdefault(_source_of(e, sources), []).append(e.lower())

    outcomes: dict[str, str] = {}
    for s in sources:
        errs = err_by_source.get(s, [])
        if any("402" in e for e in errs):
            outcomes[s] = "gated_402"
        elif errs:
            outcomes[s] = "error"
        else:
            outcomes[s] = "ok"
    return outcomes, contributed
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_coverage_adapt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/coverage_adapt.py tests/test_coverage_adapt.py
git commit -m "feat: adapter mapping harness snapshot -> coverage diagnostic inputs"
```

### Task 12: Attach coverage to harness cards

**Files:**
- Modify: `src/shortlist/screen.py` — `run_harness` (~52-63)
- Test: `tests/test_screen_engine.py`

> `ScoreCard.coverage` already exists and is assignable (models.py:102); the screener attaches it identically at screen.py:46. `_print_coverage_notes` (screen.py:116) and the `--json` block (screen.py:258) already render any card with coverage, so harness coverage flows to both for free.

- [ ] **Step 1: Write the failing test** — patch the **collector's** `collect` (local import target), return a merged-style snapshot with an FMP 402 error + provenance, assert the card carries `gated_402`.

```python
# append to tests/test_screen_engine.py  (CONFIG is the module's existing fixture/config)
def test_harness_card_carries_coverage(monkeypatch):
    from shortlist.data.models import TickerSnapshot, Profile, Fundamentals
    from shortlist import screen

    def fake_collect(tickers, source_names):
        return [TickerSnapshot(
            ticker="GEV",
            profile=Profile(market_cap=2e10),
            fundamentals=Fundamentals(fcf_yield=None),
            provenance={"profile": ["finnhub"], "price": ["yahoo"]},
            errors=["fmp: 402 Special Endpoint for GEV"],
        )]

    # run_harness does `from .data.collector import collect` -> patch THERE, not screen.collect.
    monkeypatch.setattr("shortlist.data.collector.collect", fake_collect)
    cards = screen.run_harness(["GEV"], ["yahoo", "fmp", "finnhub", "edgar"], CONFIG)
    assert len(cards) == 1
    assert cards[0].coverage is not None
    assert cards[0].coverage.providers.get("fmp") == "gated_402"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_screen_engine.py -k coverage -v`
Expected: FAIL — `cards[0].coverage is None`.

- [ ] **Step 3: Implement** — in `run_harness`:

```python
    from .coverage import build_coverage
    from .data.coverage_adapt import snapshot_to_coverage_inputs

    cards = []
    for s in snapshots:
        card = score(snapshot_to_metrics(s), config)
        outcomes, contributed = snapshot_to_coverage_inputs(s, source_names)
        card.coverage = build_coverage(outcomes, contributed, card)
        cards.append(card)
    cards.sort(key=lambda c: c.composite, reverse=True)
    return cards
```

Update the `run_harness` docstring — it currently claims harness cards carry no coverage diagnostic; that is now false.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_screen_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/screen.py tests/test_screen_engine.py
git commit -m "feat: harness-engine cards carry the coverage diagnostic (parity with screener)"
```

### Phase B — Flip default engine (GATED: requires user sign-off)

**Not a coding task in this plan.** When approved:
- Change the `--engine` default in `screen.py` from `screener` to `harness`; keep `screener` selectable for one deprecation cycle.
- Surface the cost: harness is ~13 FMP calls/ticker (~19 tickers/day free) and now also ~2x EDGAR calls/ticker — confirm acceptable or land caching first.
- Update README/CLAUDE.md "two layers" framing.

### Phase C — Retire screener providers (GATED: destructive, requires explicit user sign-off)

**Not a coding task in this plan.** Only after Phase B is proven in real use:
- Delete `providers/fmp.py`, `providers/finnhub.py`, `providers/edgar.py`, `merge.py`, screener registry entries, the `--engine` switch.
- **Keep** `providers/_form4.py` and `providers/_edgar_facts.py` (shared leaves).
- Migrate screener-only tests; delete the rest.

### Handoff Checklist (gating Phases B and C)

```
Before Phase B (flip default engine):
- [ ] Tasks 1–12 merged and the full suite is green
- [ ] Task 10 live integration run passed once (10-K filer + 20-F degradation)
- [ ] Manual spot-check: harness vs screener composite on 5–10 names within tolerance
- [ ] User explicitly approves the engine flip

Before Phase C (delete screener providers):
- [ ] Phase B has run for several real universe screens without incident
- [ ] No coverage/score regressions observed
- [ ] User explicitly approves deleting fmp.py/finnhub.py/edgar.py/merge.py
```

---

# Self-Review (run before handing off)

- [ ] **Spec coverage:** #5 → Task 1. #2 → Tasks 2–10. #1 → Tasks 11–12 (+ gated Phases B/C). All findings covered.
- [ ] **Placeholder scan:** every code step contains complete code; Task 12's fixture is now concrete with the correct patch target. No "TBD"/"similar to".
- [ ] **Type consistency:** `EdgarFinancials` fields (Task 2) ↔ `_build_financials_snapshot` reads (Task 4); `Statements.diluted_eps`/`fiscal_period_end` + `Price.monthly_closes` (Task 3) ↔ bridge reads (Tasks 6–7); `_close_near` signature (Task 7) ↔ its tests; `snapshot_to_coverage_inputs` (Task 11) ↔ call in Task 12; `Coverage.providers` field name (Task 12) ↔ models.py:84.
- [ ] **Unit safety:** EDGAR + market_cap both absolute USD; no `1e6` scaling anywhere. Fixtures use realistic absolute values.
- [ ] **Full suite green:** `uv run pytest` after each task; final ≥ 156 baseline + new tests, 0 failures.

---

# Open questions for the reviewer to confirm

1. **`pe_ttm` fidelity:** v1 uses latest-annual diluted EPS as the TTM proxy (not a true trailing-twelve-month EPS) and ~monthly-sampled closes. Acceptable, or require quarterly TTM EPS via `facts.get_ttm` (more network + complexity)?
2. **3-year vs 5-year history:** v1 accepts ~3 FY from the latest 10-K (satisfies `median_pe` min_points=2). Defer multi-filing stitching for a true 5y? (Plan assumes: yes, defer.)
3. ~~coverage() denominator growth~~ — **RESOLVED:** the 3 new fields are excluded from `coverage()`/`missing()` (Task 3), so reported coverage is unchanged.

---

# Review Log (v1 → v2)

Three independent reviewers (API-correctness, test-design, scope/risk) audited v1. Triaged against the live codebase:

**Accepted & applied:**
- Yahoo test used the wrong payload schema (`quote`/`close`) and `__new__` without `_cache_dir` → rewritten to real `YahooSource(cache_dir=tmp_path)` + `adjclose` + `timestamp` (Task 5).
- **Deeper finding (mine, beyond reviewers):** timestamps aren't parsed at all today → Task 5 now adds genuine `_monthly_closes_from_chart` extraction rather than "reuse existing locals."
- Task 12 was a placeholder and (in a reviewer's draft) patched the wrong target → now a concrete fixture patching `shortlist.data.collector.collect` (the local import).
- Task 4 `Company` alias/`is None` confusion → use the already-imported `Company` once for both insider + financials; merge unconditionally; failure-isolated. Adds insider-survives-financials test.
- `coverage()` denominator growth could break the `>= 0.8` mock test → exclude the 3 plumbing fields from `coverage()`/`missing()` (Task 3). Resolves open-Q#3.
- Missing tests: `_close_near` nearest-date unit tests; 20-F/no-financials degradation (Tasks 7, 10).
- Docs: 3y/monthly approximation + 20-F degradation + EDGAR rate-limit note (Task 9). Handoff Checklist added (Phases B/C).
- Fixture fidelity (concept/level/abstract columns) (Task 2).

**Rejected, with rationale (verified against source):**
- *"BLOCKER: EDGAR returns millions — multiply FCF by 1e6."* **False.** Live probe: AAPL revenue `416161000000.0`, OCF `111482000000.0` — absolute USD, matching FMP statements and `market_cap`. The proposed fix would inject a 1,000,000× error. The real defect was unrealistic v1 fixtures, now fixed with absolute-USD values + a units comment.
- *"MAJOR: sources don't populate `provenance`."* **False.** `merge_snapshots` sets `provenance`/`errors` on the merged snapshot (models.py:275, 280); the adapter runs post-merge.
- *"`pr` not in scope inside `if st:`"* — reviewer self-corrected; `pr` is function-scoped (bridge.py:41). Comment added for clarity.

**Cycle 2 (v2 audit) — both "blockers" empirically refuted; no real blockers found:**
- *"BLOCKER: Task 5 assumes a `timestamp` array that may not exist."* **Refuted.** Live `GET /v8/finance/chart/AAPL?range=5y` returns `result[0].timestamp` with 1255 points, length-aligned to `adjclose`. The `_chart_payload_with_ts` test helper being "new" is expected TDD. Recorded in Background.
- *"BLOCKER: Task 4 must import `Statements`."* **Refuted.** `Statements` is already imported at sources.py:16 (used by `FMPSource`). Noted in Task 4.
- Accepted polish: named the exact line numbers for the `coverage()`/`missing()` edits (Task 3); clarified the insider-isolation contract incl. the exception branch (Task 4 design note).
