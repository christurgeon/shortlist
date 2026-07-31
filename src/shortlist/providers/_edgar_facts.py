# src/shortlist/providers/_edgar_facts.py
"""Pure transform: edgartools statement DataFrames -> normalized annual series.

Dependency-isolated leaf (sibling of _form4.py). Imports pandas (a transitive
edgartools dep) but NOT edgar/httpx, so it is unit-testable with synthetic
DataFrames and never reached unless the `edgar` extra is installed.

UNITS: values are passed through verbatim, but "verbatim" is NOT always absolute
USD/shares. edgartools to_dataframe() returns ABSOLUTE USD for most issuers
(verified: AAPL revenue 416_161_000_000.0), matching FMP statements and
market_cap -- but NOT universally: MCD's diluted_shares series is
[716.4, 721.9, 732.3] (MILLIONS, filer-presentation-scaled), not absolute
share count. No scaling is applied here or downstream, so a per-issuer scale
drift passes through uncaught (docs/audits/2026-07-31-edgar-concept-match.md).
All series are NEWEST-FIRST to match the existing Statements convention."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..stats import accruals, asset_growth

# Cash-flow financing concept FAMILIES for total shareholder yield (PREDICTIVE_SIGNALS
# §5). Raw us-gaap tags (matched on the `concept` column). Single-sourced in
# _gaap_tags.py (shared with the XBRL panel in _xbrl_facts.py — edit THERE).
from ._gaap_tags import (
    DEBT_ISSUANCE_TAGS as _DEBT_ISSUANCE_TAGS,
)
from ._gaap_tags import (
    DEBT_REPAYMENT_TAGS as _DEBT_REPAYMENT_TAGS,
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
    cash_and_equivalents: list[float] = field(default_factory=list)
    total_assets: list[float] = field(default_factory=list)   # instant total assets, newest-first
    # Investment & earnings-quality scalars (PREDICTIVE_SIGNALS §3), computed here
    # because they need each series keyed by its OWN statement dates (NI on income,
    # CFO on cash-flow, Assets on balance-sheet instant) — list positions can differ.
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None
    # Total shareholder yield (PREDICTIVE_SIGNALS §5). Computed at extraction (needs
    # market_cap, supplied by the bridge) -> these are the three latest-FY dollar legs;
    # the bridge divides by market_cap. All are OUTFLOW MAGNITUDES (abs in shareholder_yield).
    dividends_paid: Optional[float] = None        # latest FY, outflow magnitude
    repurchases: Optional[float] = None           # latest FY, outflow magnitude (common+preferred)
    debt_repayments: Optional[float] = None       # latest FY, outflow magnitude
    debt_issuance: Optional[float] = None          # latest FY, inflow magnitude


def _matching_columns(df: pd.DataFrame, pattern: re.Pattern) -> list[tuple[str, str]]:
    """[(iso_date, column_name)] for columns whose name matches `pattern`, newest-first."""
    cols = []
    for c in df.columns:
        m = pattern.match(str(c))
        if m:
            cols.append((m.group(1), c))
    return sorted(cols, key=lambda t: t[0], reverse=True)


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


def _row_net_income(df: pd.DataFrame) -> Optional[pd.Series]:
    """Net-income row, robust to edgartools' `standard_concept` drift. edgartools 5.33
    RENAMED the net-income bucket from "NetIncomeLoss" to "NetIncome" (verified live on
    AAPL/MSFT/LMT), while the raw `concept` column stays the stable "us-gaap_NetIncomeLoss".
    Prefer the stable raw concept (same pattern as _concept_family_latest's financing rows),
    then fall back to either standard_concept spelling so older edgartools shapes and the
    leverage-test fixtures (standard_concept only, no `concept` column) still resolve."""
    if "concept" in df.columns:
        hit = df[df["concept"].astype(str) == "us-gaap_NetIncomeLoss"]
        if not hit.empty:
            return hit.iloc[0]
    for sc in ("NetIncome", "NetIncomeLoss"):
        row = _row_by_standard_concept(df, sc)
        if row is not None:
            return row
    return None


def _concept_family_latest(df: pd.DataFrame, suffixes: tuple[str, ...],
                           cols: list[tuple[str, str]]) -> Optional[float]:
    """Latest-FY value of a us-gaap concept FAMILY on the CASH-FLOW statement, summed
    across the family's distinct member tags (PREDICTIVE_SIGNALS §5). Matches the raw
    `concept` column (e.g. 'us-gaap_PaymentsOfDividends'), NOT `standard_concept` —
    edgartools' standard_concept mislabels financing rows (it buckets PaymentsOfDividends
    under 'DistributionsToMinorityInterests'), so we read the authoritative raw tag instead.

    A FAMILY because filers tag the same economic line differently — dividends are
    `PaymentsOfDividends` (AAPL) or `PaymentsOfDividendsCommonStock` (MSFT/LMT); debt
    repayment is `RepaymentsOfLongTermDebt` / `RepaymentsOfDebt` / `RepaymentsOfDebt-
    MaturingInMoreThanThreeMonths`. We sum the DISTINCT tags present (e.g. common +
    preferred repurchases), but EXCLUDE dimensional breakdown rows (`dimension=True`)
    so a member's by-instrument sub-rows aren't double-counted with its total.

    Returns the value at the LATEST FY column reporting any family member, or None when
    no family member is tagged. The value is returned VERBATIM (signed as edgartools
    presents it — outflows negative); shareholder_yield() abs()-normalizes each leg."""
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


def _end_map(row: Optional[pd.Series],
             cols: list[tuple[str, str]]) -> dict[str, float]:
    """{iso_end: float} for the columns this row populates, tolerant of gaps
    (unlike _series, which is all-or-nothing). Used to align NI/CFO/Assets by their
    OWN statement dates for the asset-growth / accruals scalars."""
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
    fin.net_income = _series(_row_net_income(income_df), inc_fy)

    eps = _series_by_concept_or_label(income_df, _DILUTED_EPS_CONCEPTS, _row_diluted_eps, inc_fy)
    if not eps and fin.net_income and shares_diluted:
        eps = [ni / shares_diluted for ni in fin.net_income]
    fin.diluted_eps = eps
    fin.diluted_shares = _series_by_concept_or_label(
        income_df, _DILUTED_SHARES_CONCEPTS, _row_diluted_shares, inc_fy)

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

    # Investment & earnings-quality fundamentals (PREDICTIVE_SIGNALS §3). Total
    # assets ("Assets" is edgartools' standard_concept for us-gaap:Assets, the
    # balance-sheet total — verified against the gaap standardization map). asset_growth
    # and accruals align NI/CFO/Assets by their OWN statement dates (the three spines can
    # differ in length), then apply the consecutive-fiscal-end guard + Sloan average-
    # assets in shared stats helpers. CFO is the as-reported operating cash flow (no sign
    # flip — distinct from the capex-style negation align_fcf applies).
    assets_by_end = _end_map(_row_by_standard_concept(balance_df, "Assets"), bal_inst)
    ni_by_end = _end_map(_row_net_income(income_df), inc_fy)
    cfo_by_end = _end_map(
        _row_by_standard_concept(cashflow_df, "NetCashFromOperatingActivities"), cf_fy)
    fin.total_assets = [assets_by_end[e] for e in sorted(assets_by_end, reverse=True)]
    fin.asset_growth = asset_growth(assets_by_end)
    fin.accruals = accruals(ni_by_end, cfo_by_end, assets_by_end)

    # Total shareholder yield financing legs (PREDICTIVE_SIGNALS §5). All four are
    # cash-flow FAMILIES, read off the raw us-gaap `concept` column (standard_concept
    # mislabels these). Latest-FY dollar magnitudes; the bridge divides by market_cap.
    fin.dividends_paid = _concept_family_latest(cashflow_df, _DIVIDEND_TAGS, cf_fy)
    fin.repurchases = _concept_family_latest(cashflow_df, _REPURCHASE_TAGS, cf_fy)
    fin.debt_repayments = _concept_family_latest(cashflow_df, _DEBT_REPAYMENT_TAGS, cf_fy)
    fin.debt_issuance = _concept_family_latest(cashflow_df, _DEBT_ISSUANCE_TAGS, cf_fy)
    return fin
