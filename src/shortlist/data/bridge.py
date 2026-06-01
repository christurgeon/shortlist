from __future__ import annotations

from ..models import StockMetrics
from ..stats import cagr, gross_margin_stability, growth_persistence
from .models import TickerSnapshot


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
        if st.free_cash_flow:
            fcf0 = st.free_cash_flow[0]
            m.fcf_positive = (fcf0 > 0) if fcf0 is not None else None
        # Value-leg derivation (FMP-gating fallback). UNITS: st.free_cash_flow and
        # m.market_cap are BOTH absolute USD (EDGAR + Finnhub/Yahoo), so the quotient
        # is the fcf_yield fraction directly -- no scaling. Only fires when FMP gave
        # nothing (m.fcf_yield set from f.fcf_yield earlier keeps FMP's priority).
        if m.fcf_yield is None and st.free_cash_flow and m.market_cap:
            fcf0 = st.free_cash_flow[0]
            if fcf0 is not None:
                m.fcf_yield = fcf0 / m.market_cap

    # Accepted parity gap (left None): eps_revision (Alpha Vantage, out of scope).
    return m
