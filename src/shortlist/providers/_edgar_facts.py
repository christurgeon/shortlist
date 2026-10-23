"""Pure transform: edgartools statement DataFrames -> normalized annual series.

Dependency-isolated leaf (sibling of _form4.py). Imports pandas but not edgar/httpx,
so it is unit-testable with synthetic DataFrames.

UNITS: values pass through verbatim. edgartools returns absolute USD for most
issuers but not all: some filers present diluted_shares in millions. No scaling is
applied here or downstream, so a per-issuer scale drift passes through uncaught
(docs/audits/2026-07-31-edgar-concept-match.md).
All series are newest-first, matching the Statements convention."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import pandas as pd

from ..stats import accruals, asset_growth

# Financing tag sets are single-sourced in _gaap_tags.py (shared with _xbrl_facts.py). Edit them there.
from ._gaap_tags import (
    DEBT_ISSUANCE_TAGS as _DEBT_ISSUANCE_TAGS,
)
from ._gaap_tags import (
    DEBT_REPAYMENT_TAGS as _DEBT_REPAYMENT_TAGS,
)
from ._gaap_tags import (
    DILUTED_SHARES_TAG,
    INVENTORY_BALANCE_TAG,
)
from ._gaap_tags import (
    DIVIDEND_TAGS as _DIVIDEND_TAGS,
)
from ._gaap_tags import (
    REPURCHASE_TAGS as _REPURCHASE_TAGS,
)

_FY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*\(FY\)$")
_INSTANT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")


@dataclass
class EdgarFinancials:
    fiscal_period_end: list[str] = field(default_factory=list)   # ISO dates, newest first
    revenue: list[float] = field(default_factory=list)
    net_income: list[float] = field(default_factory=list)
    operating_cash_flow: list[float] = field(default_factory=list)
    free_cash_flow: list[float] = field(default_factory=list)
    diluted_eps: list[float] = field(default_factory=list)
    diluted_shares: list[float] = field(default_factory=list)   # weighted-avg, newest-first
    operating_income: list[float] = field(default_factory=list)
    dep_amort: list[float] = field(default_factory=list)
    interest_expense: list[float] = field(default_factory=list)
    ebitda: list[float] = field(default_factory=list)   # operating_income + D&A, date-aligned
    total_debt: list[float] = field(default_factory=list)
    # The FMP-equity backfill (docs/design/STATEMENTS_MERGE.md) cannot fire on the
    # FMP-gated path, which is where a computed ROIC is needed.
    total_equity: list[float] = field(default_factory=list)
    cash_and_equivalents: list[float] = field(default_factory=list)
    total_assets: list[float] = field(default_factory=list)   # instant total assets, newest-first
    # Newest-first, /deep context only. The all-or-nothing `_series` contract makes
    # index-0 pairing with the income-statement spine safe: a gap yields [].
    inventory: list[float] = field(default_factory=list)
    # Computed here because each input series needs its own statement dates
    # (NI on income, CFO on cash-flow, Assets on balance-sheet instants).
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None
    # Latest-FY dollar legs; the bridge divides by market_cap. shareholder_yield() takes abs().
    dividends_paid: Optional[float] = None        # latest FY, outflow magnitude
    repurchases: Optional[float] = None           # latest FY, outflow magnitude (common+preferred)
    debt_repayments: Optional[float] = None       # latest FY, outflow magnitude
    debt_issuance: Optional[float] = None          # latest FY, inflow magnitude


def _matching_columns(df: pd.DataFrame, pattern: re.Pattern) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for columns whose name matches `pattern`, newest-first.

    Trailing (oldest) columns with no value in any row are dropped: kept, one would empty
    every all-or-nothing `_series` on the statement. An empty newest or middle column is
    kept, so series abstain instead of shifting index 0 or skipping a year."""
    cols = []
    for c in df.columns:
        m = pattern.match(str(c))
        if m:
            cols.append((m.group(1), c))
    cols.sort(key=lambda t: t[0], reverse=True)
    while cols and not df[cols[-1][1]].notna().to_numpy().any():
        cols.pop()
    return cols


def _fy_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for FY columns, sorted newest-first."""
    return _matching_columns(df, _FY_RE)


def _instant_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for balance-sheet INSTANT columns (plain ISO dates,
    no '(FY)' suffix — edgartools labels balance-sheet periods that way), newest-first."""
    return _matching_columns(df, _INSTANT_RE)


def _sum_concepts(df: pd.DataFrame, concepts: list[str],
                  cols: list[tuple[str, str]]) -> list[float]:
    """Sum several standard_concept rows per column (e.g. total_debt = long-term +
    current portion + short-term). Returns [] unless EVERY column has >=1 component
    (don't half-fill, matching _series)."""
    rows = [r for r in (_row_by_standard_concept(df, c) for c in concepts) if r is not None]
    if not rows or not cols:
        return []
    out = []
    for _, col in cols:
        present = [float(r.get(col)) for r in rows
                   if r.get(col) is not None and not pd.isna(r.get(col))]
        if not present:
            return []
        out.append(sum(present))
    return out


def _row_by_standard_concept(df: pd.DataFrame, concept: str) -> Optional[pd.Series]:
    if "standard_concept" not in df.columns:
        return None
    hit = df[df["standard_concept"] == concept]
    if hit.empty:
        return None
    # standard_concept is a lossy bucket. Two failure modes:
    #   1. Non-cash supplemental notes (e.g. unpaid capex accruals tagged as
    #      CapitalExpenses) make FCF exceed OCF. They sit under a Noncash/Disclosure
    #      parent abstract, so drop them first.
    #   2. Nested child line items (e.g. working-capital children tagged as operating
    #      cash flow) hide the real subtotal. Prefer the minimum level.
    if "parent_abstract_concept" in hit.columns:
        pac = hit["parent_abstract_concept"].astype(str).str.lower()
        cash_flow = hit[~pac.str.contains("noncash|disclosure|supplemental", regex=True)]
        if not cash_flow.empty:
            hit = cash_flow
    if "level" in hit.columns:
        lvl = pd.to_numeric(hit["level"], errors="coerce")
        if lvl.notna().any():
            # Positional, not `.loc[lvl.idxmin()]`: on a duplicated index `.loc` returns a
            # DataFrame, which breaks `_series()` and silently drops the ticker's whole
            # statements payload.
            return hit.iloc[int((lvl.to_numpy() == lvl.min()).nonzero()[0][0])]
    return hit.iloc[0]


def _row_net_income(df: pd.DataFrame) -> Optional[pd.Series]:
    """Net-income row, in preference order: raw `us-gaap_NetIncomeLoss` (stable where the
    bucket name drifts across edgartools releases); the "NetIncome" or "NetIncomeLoss"
    bucket (older shapes and fixtures without a `concept` column); then, for filers that
    tag no `NetIncomeLoss`, the parent's share available to common holders, and last
    `ProfitLoss`, which includes any noncontrolling interest."""
    if "concept" in df.columns:
        hit = df[df["concept"].astype(str) == "us-gaap_NetIncomeLoss"]
        if not hit.empty:
            return hit.iloc[0]
    for sc in ("NetIncome", "NetIncomeLoss"):
        row = _row_by_standard_concept(df, sc)
        if row is not None:
            return row
    rows = _rows_by_concept(df, ("us-gaap_NetIncomeLossAvailableToCommonStockholdersBasic",
                                 "us-gaap_ProfitLoss"))
    return rows[0] if rows else None


def _concept_family_latest(df: pd.DataFrame, suffixes: tuple[str, ...],
                           cols: list[tuple[str, str]]) -> Optional[float]:
    """Latest-FY value of a us-gaap concept family on the cash-flow statement, summed
    across the distinct member tags present. Matches the raw `concept` column, not
    `standard_concept`, which mislabels financing rows.

    A family because filers tag the same line differently (for example
    `PaymentsOfDividends` vs `PaymentsOfDividendsCommonStock`). Dimensional rows
    (`dimension=True`) are excluded so sub-rows do not double-count the total.

    Returns None when no member is tagged. The value is verbatim and signed as
    edgartools presents it (outflows negative)."""
    if "concept" not in df.columns or not cols:
        return None
    rows = df.copy()
    if "dimension" in rows.columns:
        rows = rows[rows["dimension"] != True]      # noqa: E712 — drop dimensional breakdowns
    concept = rows["concept"].astype(str)
    mask = concept.str.startswith("us-gaap_") & concept.apply(
        lambda c: any(c == f"us-gaap_{s}" for s in suffixes))
    hit = rows[mask]
    if hit.empty:
        return None
    _, latest_col = cols[0]                          # cols are newest-first
    vals = [float(v) for v in hit[latest_col] if v is not None and not pd.isna(v)]
    return sum(vals) if vals else None


def _row_diluted_eps(df: pd.DataFrame) -> Optional[pd.Series]:
    if "label" not in df.columns:
        return None
    for _, r in df.iterrows():
        lbl = str(r.get("label", "")).lower()
        if "diluted" in lbl and "per share" in lbl and "undiluted" not in lbl:
            return r
    return None


def _row_diluted_shares(df: pd.DataFrame) -> Optional[pd.Series]:
    """Weighted-average diluted share-count row, distinct from the per-share EPS row.
    Requires the plural "shares". Do not exclude "per share": that would drop
    legitimate "shares used in computing ... per share" count rows. Returns the first
    canonical (weighted / used in computation / outstanding) row, else the first
    diluted-shares row seen."""
    if "label" not in df.columns:
        return None
    fallback = None
    for _, r in df.iterrows():
        lbl = str(r.get("label", "")).lower()
        if "diluted" not in lbl or "shares" not in lbl or "undiluted" in lbl:
            continue
        if "weighted" in lbl or "used in comput" in lbl or "outstanding" in lbl:
            return r
        if fallback is None:
            fallback = r
    return fallback


def _end_map(row: Optional[pd.Series],
             cols: list[tuple[str, str]]) -> dict[str, float]:
    """{iso_end: float} for the columns this row populates. Tolerates gaps, unlike
    _series. Aligns NI/CFO/Assets by their own statement dates."""
    if row is None:
        return {}
    out: dict[str, float] = {}
    for iso, col in cols:
        v = row.get(col)
        if v is not None and not pd.isna(v):
            out[iso] = float(v)
    return out


def _series(row: Optional[pd.Series], fy_cols: list[tuple[str, str]]) -> list[float]:
    if row is None:
        return []
    out = []
    for _, col in fy_cols:
        v = row.get(col)
        if v is None or pd.isna(v):
            return []  # incomplete series -> treat as absent (don't half-fill)
        out.append(float(v))
    return out


# Match on raw `concept`, not `label` (filer presentation text varies and extracts
# empty for some issuers) and not `standard_concept` (bucket names drift across
# edgartools releases: docs/audits/2026-07-12-accruals-leg-disable.md).
_DILUTED_SHARES_CONCEPTS = (f"us-gaap_{DILUTED_SHARES_TAG}",)
_DILUTED_EPS_CONCEPTS = ("us-gaap_EarningsPerShareDiluted",)


def _rows_by_concept(df: pd.DataFrame, concepts: tuple[str, ...]) -> list[pd.Series]:
    """Non-dimensional rows whose raw `concept` exactly equals one of `concepts`.
    Matching the raw tag, not `label`, is what separates rows that filers label
    identically (for example basic and continuing-ops diluted EPS).

    Returns only rows at the minimum `level`, as `_row_by_standard_concept` does.
    Deeper children are dropped, not kept as later candidates. A sparse min-level
    total falls through to the label scan, which is level-blind
    (`_row_diluted_eps`/`_row_diluted_shares` ignore `level`) and so can return a
    deeper child. This function does not guard against that.

    Indexing is positional (`.iloc` + a boolean mask). `.loc` on a duplicated index
    returns a cartesian expansion with inverted order, which reintroduces the
    child-first bug the min-level rule prevents."""
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


# Totals that edgartools' bucket misses: it also tags reconciling lines and components
# with the same standard_concept, sometimes shallower than the total, or leaves the
# total unbucketed. Raw tags in priority order.
_OCF_TOTAL = "us-gaap_NetCashProvidedByUsedInOperatingActivities"
# Continuing operations first: capex is continuing-only, so FCF must be too. Accruals
# keeps the total because net income includes discontinued operations.
_OPERATING_CASH_FLOW_CONCEPTS = (
    "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", _OCF_TOTAL)
_REVENUE_CONCEPTS = ("us-gaap_Revenues", "us-gaap_RevenuesNetOfInterestExpense")


def _row_by_concept_or_bucket(df: pd.DataFrame, concepts: tuple[str, ...], bucket: str,
                              fy_cols: list[tuple[str, str]]) -> Optional[pd.Series]:
    """First non-dimensional raw-concept row with a complete series. None when a raw row
    exists but none is complete. The `standard_concept` bucket row only when the filer
    tags none of `concepts`: its min-level pick is the path that reads the wrong line."""
    rows = _rows_by_concept(df, concepts)
    for row in rows:
        if _series(row, fy_cols):
            return row
    return None if rows else _row_by_standard_concept(df, bucket)


def _series_by_concept_or_label(df: pd.DataFrame, concepts: tuple[str, ...],
                                label_picker: Callable[[pd.DataFrame], Optional[pd.Series]],
                                fy_cols: list[tuple[str, str]]) -> list[float]:
    """Value-aware pick. A concept row wins only if it yields a complete series;
    otherwise fall through to the next candidate, then the label scan. Keying on
    row presence would let a sparse concept row shadow a working label row."""
    for row in _rows_by_concept(df, concepts):
        series = _series(row, fy_cols)
        if series:
            return series
    return _series(label_picker(df), fy_cols)


# Equity is a concept family: some filers carry only `StockholdersEquity`, some only the
# NCI-inclusive tag, some both. `standard_concept` cannot express it.
#
# Parent-only first, deliberately: FMP supplies parent-only equity and EDGAR outranks FMP
# in `_merge_statements`. Preferring the NCI-inclusive tag would make one field mean two
# things by winning source and would pre-empt the FMP backfill
# (docs/design/STATEMENTS_MERGE.md). The NCI-inclusive tag is the fallback.
#
# Not a sum: a filer reporting both variants would double-count.
_EQUITY_CONCEPTS = (
    "us-gaap_StockholdersEquity",
    "us-gaap_StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)


def _total_equity_series(df: pd.DataFrame, cols: list[tuple[str, str]]) -> list[float]:
    """Total equity, newest-first, aligned to the balance sheet's instant columns.

    Uses `_rows_by_concept`: exact `concept` equality (so
    `us-gaap_LiabilitiesAndStockholdersEquity`, which equals total assets, never
    matches), no dimensional rows, and the minimum level (a nested child would read
    equity far too small and ROIC far too large).

    A sparse row does not shadow a later concept with a complete series. There is no
    label-scan fallback, so an exhausted family yields [].

    Consumed by the computed ROIC in `bridge.py`
    (docs/audits/2026-08-10-roic-proxy-and-edgar-equity-design.md)."""
    for row in _rows_by_concept(df, _EQUITY_CONCEPTS):
        series = _series(row, cols)
        if series:
            return series
    return []


def extract_financials(
    income_df: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    balance_df: pd.DataFrame,
    shares_diluted: Optional[float],
) -> EdgarFinancials:
    """Build annual series from the two statement DataFrames. Missing rows yield
    empty lists (never partial). EPS prefers the filed diluted-EPS row; if absent,
    falls back to net_income/shares_diluted; if neither, stays empty.

    fiscal_period_end comes from the income statement's FY columns, the same basis as
    revenue/net_income/diluted_eps/diluted_shares. Cash-flow series use the cash-flow
    statement's own FY columns and can differ in length, so callers must not assume
    they align index-for-index with fiscal_period_end."""
    cf_fy = _fy_columns(cashflow_df) or _fy_columns(income_df)
    inc_fy = _fy_columns(income_df)
    fin = EdgarFinancials(fiscal_period_end=[d for d, _ in (inc_fy or cf_fy)])

    ocf_row = _row_by_concept_or_bucket(
        cashflow_df, _OPERATING_CASH_FLOW_CONCEPTS, "NetCashFromOperatingActivities", cf_fy)
    fin.operating_cash_flow = _series(ocf_row, cf_fy)
    capex = _series(_row_by_standard_concept(cashflow_df, "CapitalExpenses"), cf_fy)
    if fin.operating_cash_flow and capex and len(fin.operating_cash_flow) == len(capex):
        fin.free_cash_flow = [ocf + cx for ocf, cx in zip(fin.operating_cash_flow, capex, strict=True)]

    fin.revenue = _series(
        _row_by_concept_or_bucket(income_df, _REVENUE_CONCEPTS, "Revenue", inc_fy), inc_fy)
    fin.net_income = _series(_row_net_income(income_df), inc_fy)

    eps = _series_by_concept_or_label(income_df, _DILUTED_EPS_CONCEPTS, _row_diluted_eps, inc_fy)
    if not eps and fin.net_income and shares_diluted:
        eps = [ni / shares_diluted for ni in fin.net_income]
    fin.diluted_eps = eps
    fin.diluted_shares = _series_by_concept_or_label(
        income_df, _DILUTED_SHARES_CONCEPTS, _row_diluted_shares, inc_fy)

    # The standard_concept names below are edgartools' own buckets, not raw us-gaap
    # (tests/test_edgar_leverage_live.py).
    #   - D&A is on the cash-flow statement, not the income statement.
    #   - Interest expense is often netted into other income and may be [], which
    #     leaves interest_coverage None.
    fin.operating_income = _series(_row_by_standard_concept(income_df, "OperatingIncomeLoss"), inc_fy)
    fin.dep_amort = _series(_row_by_standard_concept(cashflow_df, "DepreciationExpense"), cf_fy)
    fin.interest_expense = _series(_row_by_standard_concept(income_df, "InterestExpense"), inc_fy)

    # EBITDA = operating income + D&A, combined only at matching fiscal ends: the two
    # statements' FY dates can differ (see the docstring).
    _oi_by = dict(zip([d for d, _ in inc_fy], fin.operating_income, strict=False))
    _da_by = dict(zip([d for d, _ in cf_fy], fin.dep_amort, strict=False))
    fin.ebitda = [_oi_by[d] + _da_by[d]
                  for d in sorted(set(_oi_by) & set(_da_by), reverse=True)]

    bal_inst = _instant_columns(balance_df)
    fin.total_debt = _sum_concepts(
        balance_df, ["LongTermDebt", "CurrentPortionOfLongTermDebt", "ShortTermDebt"], bal_inst)
    fin.total_equity = _total_equity_series(balance_df, bal_inst)
    # The `CashAndMarketableSecurities` bucket maps to the cash-only balance-sheet line
    # (securities are a separate `ShortTermInvestments` row), comparable to FMP's
    # `cashAndCashEquivalents`.
    fin.cash_and_equivalents = _series(
        _row_by_standard_concept(balance_df, "CashAndMarketableSecurities"), bal_inst)

    # Match the raw concept, never standard_concept: some inventory rows have a NaN
    # bucket and edgartools buckets InventoryWriteDown as "RestructuringExpenseBenefit".
    # Abstain unless exactly one non-dimensional row exists.
    _inv_rows = _rows_by_concept(balance_df, (f"us-gaap_{INVENTORY_BALANCE_TAG}",))
    if len(_inv_rows) == 1:
        fin.inventory = _series(_inv_rows[0], bal_inst)

    # asset_growth and accruals align NI/CFO/Assets by their own statement dates (the
    # spines can differ in length). CFO is as reported, with no sign flip.
    assets_by_end = _end_map(_row_by_standard_concept(balance_df, "Assets"), bal_inst)
    ni_by_end = _end_map(_row_net_income(income_df), inc_fy)
    cfo_by_end = _end_map(_row_by_concept_or_bucket(
        cashflow_df, (_OCF_TOTAL,), "NetCashFromOperatingActivities", cf_fy), cf_fy)
    fin.total_assets = [assets_by_end[e] for e in sorted(assets_by_end, reverse=True)]
    fin.asset_growth = asset_growth(assets_by_end)
    fin.accruals = accruals(ni_by_end, cfo_by_end, assets_by_end)

    # Read from the raw `concept` column; standard_concept mislabels these rows.
    fin.dividends_paid = _concept_family_latest(cashflow_df, _DIVIDEND_TAGS, cf_fy)
    fin.repurchases = _concept_family_latest(cashflow_df, _REPURCHASE_TAGS, cf_fy)
    fin.debt_repayments = _concept_family_latest(cashflow_df, _DEBT_REPAYMENT_TAGS, cf_fy)
    fin.debt_issuance = _concept_family_latest(cashflow_df, _DEBT_ISSUANCE_TAGS, cf_fy)
    return fin


# --- companyconcept fallback (root cause B) ---------------------------------
# Recovers diluted_shares for issuers whose income-statement view has no share-count
# tag. Fallback only: EdgarSource calls it when statement extraction returned [].
# See docs/design/PLAN_EDGAR_ROOT_CAUSE_B.md for the evidence behind each guard below.

# The band admits 52-week (363) and 53-week (370) filers as well as 364/365-day years.
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380


def diluted_shares_from_concept(payload: dict, fiscal_period_end: list[str]) -> list[float]:
    """Weighted-average diluted share counts from an SEC `companyconcept` payload,
    re-indexed onto `fiscal_period_end`. Fallback only: the statement view wins when
    it has the row.

    Guards (docs/design/PLAN_EDGAR_ROOT_CAUSE_B.md):
      - Annual only: a 10-K payload also carries quarterly durations, so a fact counts
        only when end-start is ~1 year.
      - Restatements: the same `end` recurs across filings. The latest `filed` wins.
        `filed` is ISO, so string order is chronological.
      - All-or-nothing: any spine year without a fact gives [], never a partial series,
        so `cagr` cannot span a hole. An extra or partial `inc_fy` column therefore
        yields [].
      - form == "10-K" only: 8-K recast rows can be filed after the 10-K and would win
        the dedup. 10-K/A rows are dropped too. Both drops are deliberate.
      - Tag echo: a payload whose `tag` is present and not `DILUTED_SHARES_TAG`
        abstains, because its rows may belong to another concept.
    Never raises: malformed input (including a non-list `units.shares`) yields []."""
    payload = payload or {}
    try:
        tag = payload.get("tag")
        if tag is not None and tag != DILUTED_SHARES_TAG:
            return []
        rows = (payload.get("units") or {}).get("shares") or []
    except AttributeError:
        return []
    if not isinstance(rows, list):
        return []
    best: dict[str, tuple[str, float]] = {}
    for r in rows:
        try:
            if r.get("form") != "10-K":
                continue
            start, end = r.get("start"), r.get("end")
            if not start or not end:
                continue
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if not (_ANNUAL_MIN_DAYS <= days <= _ANNUAL_MAX_DAYS):
                continue
            filed = str(r.get("filed") or "")
            val = float(r["val"])
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        prev = best.get(end)
        if prev is None or filed >= prev[0]:
            best[end] = (filed, val)
    if not fiscal_period_end:
        return []
    out = [best.get(e) for e in fiscal_period_end]
    if any(v is None for v in out):
        return []
    return [v[1] for v in out]
