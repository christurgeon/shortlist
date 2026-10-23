"""Pure transform: a parsed SEC companyfacts dict -> a point-in-time annual
fundamentals panel -> the scalars shortlist.models.StockMetrics carries.

Every series is a {fiscal_end_iso: value} dict so cross-metric math aligns by
fiscal end, never by list position. Values pass through verbatim — XBRL USD facts
are absolute dollars, like FMP/Finnhub*1e6.

POINT-IN-TIME RULE: a fact is usable at `as_of` only if filed <= as_of and
end <= as_of. Within a concept, the latest such filing wins for each fiscal-period
`end` (restatement-aware, no look-ahead). Real data satisfies end <= filed; the
`end` bound guards against malformed rows.

ALIASES ARE PRIORITY, NOT A MERGE: `concepts` is a fallback list in priority order.
The highest-priority concept that reports a fiscal end fills it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

# The annual span window (admits 52/53-week years, excludes quarters and stub periods)
# is single-sourced in stats.py.
from ..models import StockMetrics
from ..stats import _FY_MAX_DAYS as _MAX_PERIOD_DAYS
from ..stats import _FY_MIN_DAYS as _MIN_PERIOD_DAYS
from ..stats import (
    accruals,
    asset_growth,
    avg_roic,
    cagr,
    compute_ebit_ev_yield,
    gross_margin_stability,
    growth_persistence,
    median_pe,
    piotroski_f,
    shareholder_yield,
)
from ._gaap_tags import (
    DEBT_ISSUANCE_TAGS,
    DEBT_REPAYMENT_TAGS,
    DILUTED_SHARES_TAG,
    DIVIDEND_TAGS,
    REPURCHASE_TAGS,
)

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


_Parsed = list[list[tuple[str, date, date, object]]]


def _annual_rows(facts: dict, concepts: list[str], *, instant: bool,
                 units: tuple[str, ...]) -> _Parsed:
    """Per concept in priority order, its usable annual rows as (end_iso, filed, end, val)
    in SEC array order. Independent of any as-of date; `val` is not converted."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    parsed = []
    for concept in concepts:
        node = gaap[concept] if concept in gaap else dei.get(concept)
        if not node:
            continue
        rows = []
        for unit in units:
            for f in node.get("units", {}).get(unit, []):
                form = f.get("form")
                if form not in _ANNUAL_FORMS:
                    continue
                filed, end, val = f.get("filed"), f.get("end"), f.get("val")
                if None in (filed, end, val):
                    continue
                try:
                    filed_d = _d(filed)
                    end_d = _d(end)
                    if not instant:
                        start = f.get("start")
                        if start is None:
                            continue
                        span = (end_d - _d(start)).days
                        if not (_MIN_PERIOD_DAYS <= span <= _MAX_PERIOD_DAYS):
                            continue
                except ValueError:
                    continue   # malformed ISO date in this SEC fact -> skip the row
                rows.append((end, filed_d, end_d, val))
        parsed.append(rows)
    return parsed


def _select(parsed: _Parsed, as_of: date) -> dict[str, float]:
    out: dict[str, float] = {}
    for rows in parsed:                            # priority order
        chosen: dict[str, tuple[date, float]] = {}  # end -> (filed, val) within THIS concept
        for end, filed_d, end_d, val in rows:
            # Exclude future filings and future periods (a malformed row can have
            # a past `filed` but a future `end`).
            if filed_d > as_of or end_d > as_of:
                continue
            prev = chosen.get(end)
            # Strict `>`: a same-date tie keeps the first row in SEC array order.
            # float() here, not in the parse, so only a picked row can raise.
            if prev is None or filed_d > prev[0]:
                chosen[end] = (filed_d, float(val))
        for end, (_filed, val) in chosen.items():
            out.setdefault(end, val)   # the first concept in priority order owns this end
    return out


def annual_series(facts: dict, concepts: list[str], as_of: date, *,
                  instant: bool = False,
                  units: tuple[str, ...] = ("USD",)) -> dict[str, float]:
    """{fiscal_end_iso: value} via priority-with-fallback across `concepts`.
    `instant=True` for balance-sheet concepts (no `start` to length-check)."""
    return _select(_annual_rows(facts, concepts, instant=instant, units=units), as_of)


class AnnualFacts:
    """`annual_series` and the gross-profit rule over one companyfacts dict, for any number
    of as-of dates, parsing each concept family once. The facts must not change while the
    object is in use."""

    def __init__(self, facts: dict) -> None:
        self._facts = facts
        self._parsed: dict[tuple, _Parsed] = {}

    def series(self, concepts: list[str], as_of: date, *, instant: bool = False,
               units: tuple[str, ...] = ("USD",)) -> dict[str, float]:
        concepts, units = tuple(concepts), tuple(units)
        key = (concepts, instant, units)
        parsed = self._parsed.get(key)
        if parsed is None:
            # Parse on first read only: a family never read (COGS when GrossProfit
            # is tagged) must not raise on a malformed row.
            parsed = self._parsed[key] = _annual_rows(self._facts, concepts, instant=instant,
                                                      units=units)
        return _select(parsed, as_of)

    def gross_profit(self, as_of: date) -> dict:
        """GrossProfit if tagged, else revenue - COGS aligned by fiscal end. Never
        backfills untagged years from revenue - COGS: mixing definitions across years
        would distort the margin-stability series."""
        gp = self.series(GROSS_PROFIT, as_of)
        if gp:
            return gp
        rev = self.series(REVENUE, as_of)
        cogs = self.series(COGS, as_of)
        return {e: rev[e] - cogs[e] for e in rev if e in cogs}


# ---------------------------------------------------------------------------
# US-GAAP concept families in priority order (not merged; see the module docstring).
# ---------------------------------------------------------------------------
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
NET_INCOME = ["NetIncomeLoss"]
OCF = ["NetCashProvidedByUsedInOperatingActivities",
       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment",
         "PaymentsToAcquireProductiveAssets"]
DILUTED_EPS = ["EarningsPerShareDiluted"]
GROSS_PROFIT = ["GrossProfit"]
COGS = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]
EQUITY = ["StockholdersEquity"]
LT_DEBT = ["LongTermDebtNoncurrent", "LongTermDebt"]
CUR_DEBT = ["LongTermDebtCurrent", "DebtCurrent"]
OP_INCOME = ["OperatingIncomeLoss"]
INTEREST = ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]
DEP_AMORT = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
             "DepreciationAndAmortization"]
CASH = ["CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
INCOME_TAX = ["IncomeTaxExpenseBenefit"]
SHARES_OUT = ["EntityCommonStockSharesOutstanding"]                   # dei, instant, unit="shares"
WTD_DIL_SHARES = [DILUTED_SHARES_TAG]  # us-gaap, unit="shares"
ASSETS = ["Assets"]   # us-gaap, instant balance-sheet total
# Financing families: annual flows, positive magnitudes (companyfacts reports
# PaymentsOf* / RepaymentsOf* as positive outflows). Tag sets are single-sourced in
# _gaap_tags.py. Unlike the aliases above, these are summed per fiscal end via
# sum_family, since a filer may tag several members in one year.
DIVIDENDS_PAID = list(DIVIDEND_TAGS)
REPURCHASES = list(REPURCHASE_TAGS)
DEBT_REPAYMENTS = list(DEBT_REPAYMENT_TAGS)
DEBT_ISSUANCE = list(DEBT_ISSUANCE_TAGS)


# ---------------------------------------------------------------------------
# End-aligned dict helpers: cross-metric math aligns by fiscal end, never list position.
# ---------------------------------------------------------------------------

def align_fcf(ocf: dict, capex: dict) -> dict:
    """FCF = OCF - capex per fiscal end reporting both (capex is a positive outflow)."""
    return {e: ocf[e] - capex[e] for e in ocf if e in capex}


def sum_aligned(a: dict, b: dict) -> dict:
    """a + b per fiscal end reporting both."""
    return {e: a[e] + b[e] for e in a if e in b}


def sum_family(facts: dict, concepts: list[str], as_of: date) -> dict:
    """{fiscal_end: sum} over a us-gaap concept family. Unlike annual_series, every
    member reporting an end is added (for example common + preferred repurchases),
    not overridden."""
    out: dict[str, float] = {}
    for concept in concepts:
        for end, val in annual_series(facts, [concept], as_of).items():
            out[end] = out.get(end, 0.0) + val
    return out


def ratio_latest(num: dict, den: dict, *, positive_den: bool = False) -> Optional[float]:
    """num/den at the latest fiscal end both report. None if no common end or den==0.
    positive_den=True also gives None for a negative denominator (a negative EBITDA
    would make a leveraged name read as net cash)."""
    common = set(num) & set(den)
    if not common:
        return None
    e = max(common)   # ISO keys sort chronologically
    d = den[e]
    if d == 0.0 or (positive_den and d < 0):
        return None
    return num[e] / d


def desc(series: dict) -> list[float]:
    """Series values newest-first (descending fiscal end) — for cagr/persistence."""
    return [series[e] for e in sorted(series, reverse=True)]


def latest(series: dict) -> Optional[float]:
    """Value at the most recent fiscal end, or None if empty."""
    return series[max(series)] if series else None


# ---------------------------------------------------------------------------
# Point-in-time fundamentals panel
# ---------------------------------------------------------------------------

@dataclass
class XbrlPanel:
    """Point-in-time annual fundamentals as {fiscal_end: val} dicts (aligned by
    end, never by list position). `shares` is a single latest instant scalar."""
    revenue: dict[str, float] = field(default_factory=dict)
    net_income: dict[str, float] = field(default_factory=dict)
    fcf: dict[str, float] = field(default_factory=dict)
    ocf: dict[str, float] = field(default_factory=dict)
    diluted_eps: dict[str, float] = field(default_factory=dict)
    gross_profit: dict[str, float] = field(default_factory=dict)
    total_equity: dict[str, float] = field(default_factory=dict)
    total_debt: dict[str, float] = field(default_factory=dict)
    operating_income: dict[str, float] = field(default_factory=dict)
    interest_expense: dict[str, float] = field(default_factory=dict)
    pretax_income: dict[str, float] = field(default_factory=dict)
    income_tax: dict[str, float] = field(default_factory=dict)
    shares: Optional[float] = None
    diluted_shares: dict[str, float] = field(default_factory=dict)  # weighted-avg annual series
    dep_amort: dict[str, float] = field(default_factory=dict)
    cash: dict[str, float] = field(default_factory=dict)            # instant (balance sheet)
    assets: dict[str, float] = field(default_factory=dict)          # instant total assets (balance sheet)
    # Annual flows, positive magnitudes (raw companyfacts convention).
    dividends_paid: dict[str, float] = field(default_factory=dict)
    repurchases: dict[str, float] = field(default_factory=dict)
    debt_repayments: dict[str, float] = field(default_factory=dict)
    debt_issuance: dict[str, float] = field(default_factory=dict)


def extract_panel(facts: dict, as_of: date) -> XbrlPanel:
    af = AnnualFacts(facts)
    ocf = af.series(OCF, as_of)
    capex = af.series(CAPEX, as_of)
    lt = af.series(LT_DEBT, as_of, instant=True)
    cur = af.series(CUR_DEBT, as_of, instant=True)
    shares = af.series(SHARES_OUT, as_of, instant=True, units=("shares",))
    return XbrlPanel(
        revenue=af.series(REVENUE, as_of),
        net_income=af.series(NET_INCOME, as_of),
        fcf=align_fcf(ocf, capex),
        ocf=ocf,
        diluted_eps=af.series(DILUTED_EPS, as_of, units=("USD/shares",)),
        gross_profit=af.gross_profit(as_of),
        total_equity=af.series(EQUITY, as_of, instant=True),
        total_debt=sum_aligned(lt, cur),
        operating_income=af.series(OP_INCOME, as_of),
        interest_expense=af.series(INTEREST, as_of),
        dep_amort=af.series(DEP_AMORT, as_of),
        cash=af.series(CASH, as_of, instant=True),
        pretax_income=af.series(PRETAX, as_of),
        income_tax=af.series(INCOME_TAX, as_of),
        shares=latest(shares),
        diluted_shares=af.series(WTD_DIL_SHARES, as_of, units=("shares",)),
        assets=af.series(ASSETS, as_of, instant=True),
        dividends_paid=sum_family(facts, DIVIDENDS_PAID, as_of),
        repurchases=sum_family(facts, REPURCHASES, as_of),
        debt_repayments=sum_family(facts, DEBT_REPAYMENTS, as_of),
        debt_issuance=sum_family(facts, DEBT_ISSUANCE, as_of),
    )


# ---------------------------------------------------------------------------
# Panel -> StockMetrics
# ---------------------------------------------------------------------------

_STATUTORY_TAX = 0.21


def _roic_series(p: XbrlPanel) -> list[float]:
    """NOPAT / (debt + equity), newest-first, over fiscal ends where operating
    income, equity and debt all exist.  Per-year effective tax rate clamped to
    [0, 0.5]; falls back to statutory 21% on losses or out-of-range rates."""
    out = []
    for e in sorted(p.operating_income, reverse=True):
        eq = p.total_equity.get(e)
        dc = p.total_debt.get(e)
        op = p.operating_income[e]
        # Negative equity (buyback compounders) would give a backwards negative ROIC.
        if eq is None or dc is None or (eq + dc) <= 0:
            continue
        ptx = p.pretax_income.get(e)
        tx = p.income_tax.get(e)
        rate = (tx / ptx) if (ptx and ptx > 0 and tx is not None) else _STATUTORY_TAX
        # Also catches tax-benefit years (negative rate).
        if not (0.0 <= rate <= 0.5):
            rate = _STATUTORY_TAX
        out.append(op * (1.0 - rate) / (eq + dc))
    return out


def panel_to_metrics(p: XbrlPanel, *, ticker: str, sic: Optional[str],
                     price: Optional[float],
                     price_at: Callable[[date], Optional[float]]) -> StockMetrics:
    """Build the scorer's StockMetrics from a point-in-time panel + price.
    Every ratio aligns by fiscal end; missing inputs leave fields None
    (scorer redistributes the composite weight)."""
    m = StockMetrics(ticker=ticker, sic=sic, price=price)

    # Quality (each ratio at the latest fiscal end both legs report)
    m.net_margin = ratio_latest(p.net_income, p.revenue)
    m.roe = ratio_latest(p.net_income, p.total_equity)
    m.debt_to_equity = ratio_latest(p.total_debt, p.total_equity)
    m.interest_coverage = ratio_latest(p.operating_income, p.interest_expense)

    # Moat
    m.gross_margin = ratio_latest(p.gross_profit, p.revenue)
    common = sorted(set(p.gross_profit) & set(p.revenue), reverse=True)
    margins = [p.gross_profit[e] / p.revenue[e] for e in common if p.revenue[e]]
    m.gross_margin_stability = gross_margin_stability(margins) if margins else None
    roics = _roic_series(p)
    m.roic = roics[0] if roics else None
    m.roic_5y_avg = avg_roic(roics) if roics else None

    # Growth (reuse production stats over newest-first series)
    m.revenue_cagr = cagr(desc(p.revenue))
    m.fcf_cagr = cagr(desc(p.fcf))
    m.eps_cagr = cagr(desc(p.net_income))    # net-income proxy (production convention)
    m.eps_cagr_ps = cagr(desc(p.diluted_eps))         # genuine per-share (dilution-aware)
    m.share_count_cagr = cagr(desc(p.diluted_shares)) # + = net issuance, - = buybacks
    m.revenue_growth_persistence = growth_persistence(desc(p.revenue))

    # p.ocf is CFO as reported: no sign flip.
    m.asset_growth = asset_growth(p.assets)
    m.accruals = accruals(p.net_income, p.ocf, p.assets)

    # The panel dicts need not share fiscal ends, but piotroski_f aligns by list position.
    # Pass series on a common newest-first spine of the union of ends, with None where a
    # series lacks an end. The affected leg then abstains instead of comparing mismatched years.
    _ends = sorted(set(p.net_income) | set(p.ocf) | set(p.total_debt)
                   | set(p.gross_profit) | set(p.revenue), reverse=True)
    m.piotroski_f, m.piotroski_f_legs = piotroski_f(
        net_income=[p.net_income.get(e) for e in _ends],
        ocf=[p.ocf.get(e) for e in _ends],
        total_debt=[p.total_debt.get(e) for e in _ends],
        gross_profit=[p.gross_profit.get(e) for e in _ends],
        revenue=[p.revenue.get(e) for e in _ends],
    )

    # Value (peg and upside_to_target need analyst data that XBRL lacks)
    m.market_cap = (price * p.shares) if (price and p.shares) else None
    fcf_latest = latest(p.fcf)
    # Feeds the negative_fcf gate, as in bridge.py. align_fcf intersects OCF and capex
    # ends, so a newest FY with OCF but no capex tag drops out and latest(p.fcf) would
    # be a stale older year. Abstain (None) in that case.
    fcf_is_current = bool(p.fcf) and bool(p.ocf) and max(p.fcf) == max(p.ocf)
    m.fcf_positive = (fcf_latest > 0) if (fcf_latest is not None and fcf_is_current) else None
    if fcf_latest is not None and m.market_cap:
        m.fcf_yield = fcf_latest / m.market_cap

    m.shareholder_yield = shareholder_yield(
        latest(p.dividends_paid), latest(p.repurchases),
        latest(p.debt_repayments), latest(p.debt_issuance), m.market_cap)
    eps_latest = latest(p.diluted_eps)
    if price and eps_latest:
        m.pe_ttm = price / eps_latest        # latest annual EPS as a TTM proxy
    annual_pe = []
    for end_iso in sorted(p.diluted_eps, reverse=True):
        px = price_at(_d(end_iso))
        e = p.diluted_eps[end_iso]
        if px and e:
            annual_pe.append(px / e)
    m.pe_median_5y = median_pe(annual_pe)    # None if < 2 points

    # EBITDA = operating income + D&A at the latest common fiscal end; net_debt = total_debt - cash.
    m.revenue = latest(p.revenue)
    ebitda_series = sum_aligned(p.operating_income, p.dep_amort)   # aligned by fiscal end
    m.ebitda = latest(ebitda_series)
    m.cash_and_equivalents = latest(p.cash)
    net_debt_series = sum_aligned(p.total_debt, {e: -v for e, v in p.cash.items()})
    m.net_debt_to_ebitda = ratio_latest(net_debt_series, ebitda_series,
                                        positive_den=True)

    # EBIT and net_debt each use their own latest fiscal end (no common-end intersection
    # as in ratio_latest). This assumes both share the latest annual end; it does not
    # abstain if they disagree.
    m.ebit_ev_yield = compute_ebit_ev_yield(
        latest(p.operating_income), m.market_cap, latest(net_debt_series))

    return m
