# src/shortlist/providers/_xbrl_facts.py
"""Pure transform: a parsed SEC companyfacts dict -> a point-in-time annual
fundamentals panel -> the scalars shortlist.models.StockMetrics carries.

Every series is a {fiscal_end_iso: value} dict so cross-metric math aligns by
fiscal end, never by list position. Values pass through verbatim — XBRL USD facts
are absolute dollars, like FMP/Finnhub*1e6.

POINT-IN-TIME RULE: a fact is usable at `as_of` only if filed <= as_of AND
end <= as_of; within a concept, for each distinct fiscal-period `end` the value
from the LATEST such filing wins (restatement-aware, never look-ahead). Real SEC
data always satisfies end <= filed (you cannot file a report for a period that has
not ended), so bounding both by as_of is belt-and-suspenders against malformed or
mis-tagged rows where a future period end slips past a past filed date.

ALIASES ARE PRIORITY, NOT A MERGE: `concepts` is a fallback list in priority
order; each fiscal end is filled by the highest-priority concept that reports it
(Revenues / SalesRevenueNet / RevenueFromContract... are different line items)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}
_MIN_PERIOD_DAYS = 350   # admits 52/53-week fiscal years; excludes quarters/half-years
_MAX_PERIOD_DAYS = 380   # (transition/stub-period annuals are rare and dropped)


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def annual_series(facts: dict, concepts: list[str], as_of: date, *,
                  instant: bool = False,
                  units: tuple[str, ...] = ("USD",)) -> dict[str, float]:
    """{fiscal_end_iso: value} via priority-with-fallback across `concepts`.
    `instant=True` for balance-sheet concepts (no `start` to length-check)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    out: dict[str, float] = {}
    for concept in concepts:                       # priority order
        node = gaap[concept] if concept in gaap else dei.get(concept)
        if not node:
            continue
        chosen: dict[str, tuple[date, float]] = {}  # end -> (filed, val) within THIS concept
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
                # point-in-time: exclude both future filings AND future periods. The
                # latter guards against a malformed row whose period `end` post-dates
                # as_of despite a past `filed` (real SEC data satisfies end <= filed).
                if filed_d > as_of or end_d > as_of:
                    continue
                prev = chosen.get(end)
                # strict `>`: a same-date tie keeps the first row seen (SEC array order)
                if prev is None or filed_d > prev[0]:
                    chosen[end] = (filed_d, float(val))
        for end, (_filed, val) in chosen.items():
            out.setdefault(end, val)   # first concept in priority order owns this end; later aliases skipped
    return out


# ---------------------------------------------------------------------------
# US-GAAP concept families, PRIORITY order (annual_series fills each end from the
# first that reports it — these are NOT merged). Verified tag choices/aliases.
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
WTD_DIL_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]  # us-gaap, unit="shares"; scaffolded for later EPS/dilution work


# ---------------------------------------------------------------------------
# End-aligned dict helpers — all cross-metric math goes through these so it
# aligns by fiscal end (shared keys only), never by list position.
# ---------------------------------------------------------------------------

def align_fcf(ocf: dict, capex: dict) -> dict:
    """FCF = OCF - capex per fiscal end reporting both (capex is a positive outflow)."""
    return {e: ocf[e] - capex[e] for e in ocf if e in capex}


def sum_aligned(a: dict, b: dict) -> dict:
    """a + b per fiscal end reporting both."""
    return {e: a[e] + b[e] for e in a if e in b}


def ratio_latest(num: dict, den: dict) -> Optional[float]:
    """num/den at the LATEST fiscal end both report. None if no common end or den==0."""
    common = set(num) & set(den)
    if not common:
        return None
    e = max(common)   # ISO-8601 keys sort chronologically -> max() is the latest end
    return (num[e] / den[e]) if den[e] != 0.0 else None


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


def _gross_profit(facts: dict, as_of: date) -> dict:
    """GrossProfit if tagged, else revenue - COGS aligned by fiscal end. Prefers a
    single consistent definition: when GrossProfit is tagged we use it as-is and do
    NOT backfill earlier untagged years from revenue-COGS — mixing the two
    definitions across years would distort the margin-stability series (same
    rationale as the revenue-alias priority in annual_series)."""
    gp = annual_series(facts, GROSS_PROFIT, as_of)
    if gp:
        return gp
    rev = annual_series(facts, REVENUE, as_of)
    cogs = annual_series(facts, COGS, as_of)
    return {e: rev[e] - cogs[e] for e in rev if e in cogs}


def extract_panel(facts: dict, as_of: date) -> XbrlPanel:
    ocf = annual_series(facts, OCF, as_of)
    capex = annual_series(facts, CAPEX, as_of)
    lt = annual_series(facts, LT_DEBT, as_of, instant=True)
    cur = annual_series(facts, CUR_DEBT, as_of, instant=True)
    shares = annual_series(facts, SHARES_OUT, as_of, instant=True, units=("shares",))
    return XbrlPanel(
        revenue=annual_series(facts, REVENUE, as_of),
        net_income=annual_series(facts, NET_INCOME, as_of),
        fcf=align_fcf(ocf, capex),
        ocf=ocf,
        diluted_eps=annual_series(facts, DILUTED_EPS, as_of, units=("USD/shares",)),
        gross_profit=_gross_profit(facts, as_of),
        total_equity=annual_series(facts, EQUITY, as_of, instant=True),
        total_debt=sum_aligned(lt, cur),
        operating_income=annual_series(facts, OP_INCOME, as_of),
        interest_expense=annual_series(facts, INTEREST, as_of),
        dep_amort=annual_series(facts, DEP_AMORT, as_of),
        cash=annual_series(facts, CASH, as_of, instant=True),
        pretax_income=annual_series(facts, PRETAX, as_of),
        income_tax=annual_series(facts, INCOME_TAX, as_of),
        shares=latest(shares),
        diluted_shares=annual_series(facts, WTD_DIL_SHARES, as_of, units=("shares",)),
    )


# ---------------------------------------------------------------------------
# Panel -> StockMetrics
# ---------------------------------------------------------------------------
from ..models import StockMetrics  # noqa: E402
from ..stats import (  # noqa: E402
    avg_roic,
    cagr,
    gross_margin_stability,
    growth_persistence,
    median_pe,
    piotroski_f,
)

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
        # Skip non-positive invested capital: negative equity (e.g. buyback-heavy
        # compounders) would otherwise yield a backwards negative ROIC.
        if eq is None or dc is None or (eq + dc) <= 0:
            continue
        ptx = p.pretax_income.get(e)
        tx = p.income_tax.get(e)
        rate = (tx / ptx) if (ptx and ptx > 0 and tx is not None) else _STATUTORY_TAX
        # clamp also catches tax-benefit years (negative tx -> negative rate)
        if not (0.0 <= rate <= 0.5):
            rate = _STATUTORY_TAX
        out.append(op * (1.0 - rate) / (eq + dc))
    return out


def panel_to_metrics(p: XbrlPanel, *, ticker: str, sic: Optional[str],
                     price: Optional[float], price_at) -> StockMetrics:
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

    # Fundamental-quality (Piotroski-inspired Core-6, asset-free). The panel series are
    # independently-keyed {end: val} dicts that need NOT share fiscal ends (e.g.
    # total_debt = sum_aligned(LT, current) drops a year a filer didn't tag current debt
    # for). piotroski_f aligns by list POSITION, so we must hand it series aligned to a
    # common fiscal-end spine (newest-first over the union of ends, None where a series
    # lacks that end) — otherwise a delta leg would compare mismatched years. None gaps
    # make the affected leg abstain (its guards), never mix years. Mirrors how the
    # quality/moat legs above align cross-series math by fiscal end (ratio_latest).
    _ends = sorted(set(p.net_income) | set(p.ocf) | set(p.total_debt)
                   | set(p.gross_profit) | set(p.revenue), reverse=True)
    m.piotroski_f, m.piotroski_f_legs = piotroski_f(
        net_income=[p.net_income.get(e) for e in _ends],
        ocf=[p.ocf.get(e) for e in _ends],
        total_debt=[p.total_debt.get(e) for e in _ends],
        gross_profit=[p.gross_profit.get(e) for e in _ends],
        revenue=[p.revenue.get(e) for e in _ends],
    )

    # Value (2 of 4 legs; peg + upside_to_target need analyst data absent from XBRL)
    m.market_cap = (price * p.shares) if (price and p.shares) else None
    fcf_latest = latest(p.fcf)
    if fcf_latest is not None and m.market_cap:
        m.fcf_yield = fcf_latest / m.market_cap
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

    # Leverage (ASSESSMENT_GAPS §2.7). EBITDA = operating income + D&A at the latest
    # common fiscal end; net_debt = total_debt - cash (signed). Backtest axis only.
    m.revenue = latest(p.revenue)
    ebitda_series = sum_aligned(p.operating_income, p.dep_amort)   # aligned by fiscal end
    m.ebitda = latest(ebitda_series)
    m.cash_and_equivalents = latest(p.cash)
    # net_debt = total_debt - cash and net_debt/EBITDA, all aligned by fiscal end via the
    # panel helpers (never mixing ends across the three series).
    net_debt_series = sum_aligned(p.total_debt, {e: -v for e, v in p.cash.items()})
    m.net_debt_to_ebitda = ratio_latest(net_debt_series, ebitda_series)

    return m
