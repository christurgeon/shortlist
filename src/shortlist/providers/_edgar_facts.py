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
    if hit.empty:
        return None
    # edgartools' standard_concept is a lossy bucket: the same tag lands on rows
    # that are NOT the line we want. Two failure modes, seen on real filings:
    #   1. Non-cash supplemental NOTES. GOOGL tags "Capital expenditures incurred
    #      but not yet paid" (a positive accrual, +15B) as CapitalExpenses; adding
    #      it instead of the -91B cash payment makes FCF exceed OCF. These rows sit
    #      under a *Noncash...Disclosure* parent abstract -> drop them first.
    #   2. Nested CHILD line items. MSFT tags working-capital children ("Other
    #      long-term assets", -3B) as NetCashFromOperatingActivities at level 4;
    #      the real subtotal ("Net cash from operations", +136B) is level 2. Picking
    #      iloc[0] grabbed a child and collapsed FCF to ~-capex -> prefer min level.
    if "parent_abstract_concept" in hit.columns:
        pac = hit["parent_abstract_concept"].astype(str).str.lower()
        cash_flow = hit[~pac.str.contains("noncash|disclosure|supplemental", regex=True)]
        if not cash_flow.empty:
            hit = cash_flow
    if "level" in hit.columns:
        lvl = pd.to_numeric(hit["level"], errors="coerce")
        if lvl.notna().any():
            return hit.loc[lvl.idxmin()]
    return hit.iloc[0]


def _row_diluted_eps(df: pd.DataFrame) -> Optional[pd.Series]:
    if "label" not in df.columns:
        return None
    for _, r in df.iterrows():
        lbl = str(r.get("label", "")).lower()
        if "diluted" in lbl and "per share" in lbl and "undiluted" not in lbl:
            return r
    return None


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


def extract_financials(
    income_df: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    shares_diluted: Optional[float],
) -> EdgarFinancials:
    """Build annual series from the two statement DataFrames. Missing rows yield
    empty lists (never partial). EPS prefers the filed diluted-EPS row; if absent,
    falls back to net_income/shares_diluted; if neither, stays empty.

    Cash-flow-derived series (operating_cash_flow/free_cash_flow) and
    fiscal_period_end key off the cash-flow statement's FY columns, while
    revenue/net_income/diluted_eps key off the income statement's FY columns.
    These can differ in length when the two statements cover different period
    counts; callers must NOT assume fiscal_period_end aligns index-for-index
    with the income-statement series."""
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
