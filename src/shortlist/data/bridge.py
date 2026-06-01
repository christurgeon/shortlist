from __future__ import annotations

from ..models import StockMetrics
from ..stats import gross_margin_stability
from .models import TickerSnapshot


def snapshot_to_metrics(snap: TickerSnapshot) -> StockMetrics:
    """Map a harness TickerSnapshot onto the flat StockMetrics that
    scoring.score() consumes. Pure (no I/O). Absent inputs stay None so the
    scorer's weight-redistribution handles them.

    Two fields are DERIVED here because the harness has the raw material but not
    the field: gross_margin_stability (from Statements) and fcf_positive (most
    recent FCF). Two are accepted None parity gaps the harness does not fetch:
    pe_median_5y and roic_5y_avg. eps_revision is out of scope."""
    m = StockMetrics(ticker=snap.ticker)

    p = snap.profile
    if p:
        m.name = p.name
        m.sector = p.sector
        m.market_cap = p.market_cap

    f = snap.fundamentals
    if f:
        m.pe_ttm = f.pe_ttm
        m.peg = f.peg
        m.fcf_yield = f.fcf_yield
        m.roe = f.roe
        m.roic = f.roic
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
        if st.free_cash_flow:
            fcf0 = st.free_cash_flow[0]
            m.fcf_positive = (fcf0 > 0) if fcf0 is not None else None

    # Accepted parity gaps (left None): pe_median_5y, roic_5y_avg, eps_revision.
    return m
