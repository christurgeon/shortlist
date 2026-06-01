from __future__ import annotations

from typing import Optional

from ..models import StockMetrics
from ..stats import cagr, gross_margin_stability, growth_persistence, median_pe
from .models import TickerSnapshot


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


def snapshot_to_metrics(snap: TickerSnapshot) -> StockMetrics:
    """Map a harness TickerSnapshot onto the flat StockMetrics that
    scoring.score() consumes. Pure (no I/O). Absent inputs stay None so the
    scorer's weight-redistribution handles them.

    Several fields are DERIVED here because the harness has the raw material but
    not the field: gross_margin_stability, fcf_positive, and the growth legs
    (revenue_cagr/fcf_cagr/eps_cagr/revenue_growth_persistence) — all from the 5y
    Statements via the shared shortlist.stats helpers. eps_revision is the one
    accepted None parity gap (Alpha Vantage, out of scope); pe_median_5y and
    roic_5y_avg flow from FMPSource's annual history fetches."""
    m = StockMetrics(ticker=snap.ticker)

    p = snap.profile
    if p:
        m.name = p.name
        m.sector = p.sector
        m.market_cap = p.market_cap

    f = snap.fundamentals
    if f:
        m.pe_ttm = f.pe_ttm
        m.pe_median_5y = f.pe_median_5y
        m.peg = f.peg
        m.fcf_yield = f.fcf_yield
        m.roe = f.roe
        m.roic = f.roic
        m.roic_5y_avg = f.roic_5y_avg
        m.gross_margin = f.gross_margin
        m.net_margin = f.net_margin
        m.debt_to_equity = f.debt_to_equity
        m.interest_coverage = f.interest_coverage

    pr = snap.price
    if pr:
        m.price = pr.price
        m.price_vs_200dma = pr.price_vs_200dma()
        m.rel_strength_6m = pr.rel_strength_6m
        m.realized_vol = pr.realized_vol
        m.max_drawdown = pr.max_drawdown

    a = snap.analyst
    if a:
        m.target_median = a.target_median
        m.rating_buy = a.buy
        m.rating_hold = a.hold
        m.rating_sell = a.sell

    ins = snap.insider
    if ins:
        m.insider_net_6m = ins.net_value_6m
        m.insider_sentiment = ins.sentiment_mspr

    st = snap.statements
    if st:
        m.gross_margin_stability = gross_margin_stability(st.gross_margins())
        # Growth legs derived from the 5y series (statements are newest-first).
        m.revenue_cagr = cagr(st.revenue)
        m.fcf_cagr = cagr(st.free_cash_flow)
        m.eps_cagr = cagr(st.net_income)
        m.revenue_growth_persistence = growth_persistence(st.revenue)
        fcf0 = st.free_cash_flow[0] if st.free_cash_flow else None
        if st.free_cash_flow:
            m.fcf_positive = (fcf0 > 0) if fcf0 is not None else None
        # Value-leg derivation (FMP-gating fallback). UNITS: st.free_cash_flow and
        # m.market_cap are BOTH absolute USD (EDGAR + Finnhub/Yahoo), so the quotient
        # is the fcf_yield fraction directly -- no scaling. Only fires when FMP gave
        # nothing (m.fcf_yield set from f.fcf_yield earlier keeps FMP's priority).
        if m.fcf_yield is None and fcf0 is not None and m.market_cap:
            m.fcf_yield = fcf0 / m.market_cap
        # PE-vs-history from EDGAR EPS + Yahoo closes when FMP gated the symbol.
        # pr is in scope from the function top. pe_ttm uses latest ANNUAL EPS as a
        # TTM proxy (documented approximation).
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

    # Accepted parity gap (left None): eps_revision (Alpha Vantage, out of scope).
    return m
