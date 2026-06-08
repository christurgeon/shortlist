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
    cash_and_equivalents: list[float] = field(default_factory=list)


def _fy_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for FY columns, sorted newest-first."""
    cols = []
    for c in df.columns:
        m = _FY_RE.match(str(c))
        if m:
            cols.append((m.group(1), c))
    return sorted(cols, key=lambda t: t[0], reverse=True)


def _instant_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for balance-sheet INSTANT columns (plain ISO dates,
    no '(FY)' suffix — edgartools labels balance-sheet periods that way), newest-first."""
    cols = []
    for c in df.columns:
        m = _INSTANT_RE.match(str(c))
        if m:
            cols.append((m.group(1), c))
    return sorted(cols, key=lambda t: t[0], reverse=True)


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


def _row_diluted_shares(df: pd.DataFrame) -> Optional[pd.Series]:
    """Weighted-average diluted SHARE COUNT row (e.g. "Weighted average shares
    outstanding, diluted" or "Shares used in computing diluted earnings per share")
    — distinct from the per-share EPS *value* row. We require the plural "shares"
    (the EPS value rows read "...per share", singular), which already excludes the
    per-share line, so we do NOT exclude "per share" here (that would wrongly drop
    legitimate "shares used in computing ... per share" COUNT rows). Single pass:
    return the first canonical (weighted-average / used-in-computation / outstanding)
    row; otherwise fall back to the first diluted-shares row seen — a stray "diluted
    shares from <X>" reconciliation/component line that lacks level/parent
    disambiguation here."""
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
    balance_df: pd.DataFrame,
    shares_diluted: Optional[float],
) -> EdgarFinancials:
    """Build annual series from the two statement DataFrames. Missing rows yield
    empty lists (never partial). EPS prefers the filed diluted-EPS row; if absent,
    falls back to net_income/shares_diluted; if neither, stays empty.

    fiscal_period_end is labelled from the INCOME statement's FY columns — the same
    basis as revenue/net_income/diluted_eps/diluted_shares, and the basis the bridge's
    PE-history fallback pairs diluted_eps against. Cash-flow-derived series
    (operating_cash_flow/free_cash_flow) are extracted on the cash-flow statement's own
    FY columns; when the two statements cover different period counts these can differ
    in length from fiscal_period_end, so callers must NOT assume the cash-flow series
    aligns index-for-index with fiscal_period_end."""
    cf_fy = _fy_columns(cashflow_df) or _fy_columns(income_df)
    inc_fy = _fy_columns(income_df)
    fin = EdgarFinancials(fiscal_period_end=[d for d, _ in (inc_fy or cf_fy)])

    fin.operating_cash_flow = _series(_row_by_standard_concept(cashflow_df, "NetCashFromOperatingActivities"), cf_fy)
    capex = _series(_row_by_standard_concept(cashflow_df, "CapitalExpenses"), cf_fy)
    if fin.operating_cash_flow and capex and len(fin.operating_cash_flow) == len(capex):
        fin.free_cash_flow = [ocf + cx for ocf, cx in zip(fin.operating_cash_flow, capex, strict=True)]

    fin.revenue = _series(_row_by_standard_concept(income_df, "Revenue"), inc_fy)
    fin.net_income = _series(_row_by_standard_concept(income_df, "NetIncomeLoss"), inc_fy)

    eps = _series(_row_diluted_eps(income_df), inc_fy)
    if not eps and fin.net_income and shares_diluted:
        eps = [ni / shares_diluted for ni in fin.net_income]
    fin.diluted_eps = eps
    fin.diluted_shares = _series(_row_diluted_shares(income_df), inc_fy)

    # Leverage / coverage inputs (ASSESSMENT_GAPS §2.7). The standard_concept names
    # below are edgartools' OWN normalized buckets (NOT raw us-gaap) — verified against a
    # live AAPL filing (tests/test_edgar_leverage_live.py). Key facts learned there:
    #   - D&A lives on the CASH-FLOW statement (`DepreciationExpense`), not the income stmt.
    #   - Balance-sheet columns are INSTANT dates (no '(FY)' suffix) -> _instant_columns.
    #   - Total debt = long-term + current portion + short-term (summed components).
    #   - Interest expense is often netted into other income (e.g. AAPL) -> may be [],
    #     which leaves interest_coverage None (the gate's net-debt/EBITDA path is primary).
    fin.operating_income = _series(_row_by_standard_concept(income_df, "OperatingIncomeLoss"), inc_fy)
    fin.dep_amort = _series(_row_by_standard_concept(cashflow_df, "DepreciationExpense"), cf_fy)
    fin.interest_expense = _series(_row_by_standard_concept(income_df, "InterestExpense"), inc_fy)

    # EBITDA = operating income + D&A, combined ONLY at matching fiscal ends. Operating
    # income keys off the income statement's FY dates, D&A off the cash-flow statement's
    # — these can differ (see this function's docstring), so we align by date here rather
    # than zipping by list position downstream (the bridge consumes fin.ebitda directly).
    _oi_by = dict(zip([d for d, _ in inc_fy], fin.operating_income, strict=False))
    _da_by = dict(zip([d for d, _ in cf_fy], fin.dep_amort, strict=False))
    fin.ebitda = [_oi_by[d] + _da_by[d]
                  for d in sorted(set(_oi_by) & set(_da_by), reverse=True)]

    bal_inst = _instant_columns(balance_df)
    fin.total_debt = _sum_concepts(
        balance_df, ["LongTermDebt", "CurrentPortionOfLongTermDebt", "ShortTermDebt"], bal_inst)
    # edgartools' `CashAndMarketableSecurities` bucket maps to the "Cash and cash
    # equivalents" balance-sheet LINE (cash-only; marketable securities are a separate
    # `ShortTermInvestments` row), so this is cash & equivalents — comparable to the
    # screener's FMP `cashAndCashEquivalents` and the XBRL `CashAndCashEquivalents...`
    # concept. Verified on live AAPL (~$30B, not the ~$160B incl. securities).
    fin.cash_and_equivalents = _series(
        _row_by_standard_concept(balance_df, "CashAndMarketableSecurities"), bal_inst)
    return fin
