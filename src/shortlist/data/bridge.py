from __future__ import annotations

import dataclasses
from typing import Optional

from ..models import StockMetrics
from ..stats import (
    cagr,
    compute_ebit_ev_yield,
    gross_margin_stability,
    growth_persistence,
    median_pe,
    net_debt_from,
    piotroski_f,
    shareholder_yield,
    surprise_dispersion,
)
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


_MAX_PLAUSIBLE_SHORT_PCT = 0.60   # > this of shares-outstanding => broken denominator (ADR/dual-class)
_DTC_SENTINEL = 999.99            # FINRA's zero-volume days-to-cover cap


def _age_days(as_of: Optional[str], settlement: Optional[str]) -> Optional[int]:
    """Whole days between a snapshot's capture time and the SI settlement date.
    Pure (no clock read) and None-safe (unparseable -> None)."""
    from datetime import date, datetime
    if not as_of or not settlement:
        return None
    try:
        a = datetime.fromisoformat(as_of).date()
        s = date.fromisoformat(settlement)
    except (TypeError, ValueError):
        return None
    return (a - s).days


def _financial_series(st) -> list[dict]:
    """Newest-first list-of-dicts from the parallel Statements series, for the
    research quant-context. Per-index None-guarded so ragged lengths never raise;
    a cell is None where its series is shorter. Returns [] for empty Statements."""
    cols = ("fiscal_years", "fiscal_period_end", "revenue", "gross_profit",
            "net_income", "operating_cash_flow", "free_cash_flow", "diluted_eps",
            "total_debt", "diluted_shares")
    n = max((len(getattr(st, c)) for c in cols), default=0)

    def at(seq, i):
        return seq[i] if i < len(seq) else None

    return [{
        "fiscal_year": at(st.fiscal_years, i),
        "period_end": at(st.fiscal_period_end, i),
        "revenue": at(st.revenue, i),
        "gross_profit": at(st.gross_profit, i),
        "net_income": at(st.net_income, i),
        "operating_cash_flow": at(st.operating_cash_flow, i),
        "free_cash_flow": at(st.free_cash_flow, i),
        "diluted_eps": at(st.diluted_eps, i),
        "total_debt": at(st.total_debt, i),
        "diluted_shares": at(st.diluted_shares, i),
    } for i in range(n)]


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
        m.sic = p.sic
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
        m.insider_distinct_buyers = ins.distinct_buyers
        m.insider_role_weighted_buy_value = ins.role_weighted_buy_value
        m.insider_planned_sell_value = ins.planned_sell_value
        # Compact recent Form-4 trades for the research brief (enrichment only).
        if ins.recent:
            m.insider_recent = [
                {"date": t.date, "name": t.name, "role": t.role,
                 "kind": t.kind, "value": t.value} for t in ins.recent]

    st = snap.statements
    if st:
        m.gross_margin_stability = gross_margin_stability(st.gross_margins())
        # Growth legs derived from the 5y series (statements are newest-first).
        m.revenue_cagr = cagr(st.revenue)
        m.fcf_cagr = cagr(st.free_cash_flow)
        m.eps_cagr = cagr(st.net_income)
        m.eps_cagr_ps = cagr(st.diluted_eps)             # genuine per-share (dilution-aware)
        m.share_count_cagr = cagr(st.diluted_shares)     # + = net issuance, - = buybacks
        m.revenue_growth_persistence = growth_persistence(st.revenue)
        # Investment & earnings-quality scalars (PREDICTIVE_SIGNALS §3) are pre-computed
        # at extraction (the source aligns NI/CFO/Assets by their own statement dates);
        # copy them through. None where the source didn't supply them (e.g. FMP-won
        # statements) -> the opt-in quality legs redistribute.
        m.asset_growth = st.asset_growth
        m.accruals = st.accruals
        # Fundamental-quality (Piotroski-inspired Core-6, asset-free). Statements lists
        # are newest-first and index-parallel by fiscal year; free-source derivable so
        # it survives FMP-402 gating (serves broad ticker coverage).
        m.piotroski_f, m.piotroski_f_legs = piotroski_f(
            net_income=st.net_income, ocf=st.operating_cash_flow,
            total_debt=st.total_debt, gross_profit=st.gross_profit,
            revenue=st.revenue,
        )
        # Raw newest-first series for the research quant-context (scorer-inert).
        series = _financial_series(st)
        if series:
            m.financial_series = series
        fcf0 = st.free_cash_flow[0] if st.free_cash_flow else None
        if st.free_cash_flow:
            m.fcf_positive = (fcf0 > 0) if fcf0 is not None else None
        # Leverage / coverage (ASSESSMENT_GAPS §2.7). FMP keeps priority where it set
        # these (m.* already non-None); EDGAR fills the gated gap. UNITS: absolute USD.
        rev0 = st.revenue[0] if st.revenue else None
        if m.revenue is None and rev0 is not None:
            m.revenue = rev0
        oi0 = st.operating_income[0] if st.operating_income else None
        ie0 = st.interest_expense[0] if st.interest_expense else None
        ebitda0 = st.ebitda[0] if st.ebitda else None
        cash0 = st.cash_and_equivalents[0] if st.cash_and_equivalents else None
        debt0 = st.total_debt[0] if st.total_debt else None
        if m.cash_and_equivalents is None and cash0 is not None:
            m.cash_and_equivalents = cash0
        # EBITDA is date-aligned at extraction (st.ebitda), so no cross-statement
        # positional combine here. interest_coverage stays op-income/interest (both
        # from the income statement -> already aligned).
        if m.ebitda is None and ebitda0 is not None:
            m.ebitda = ebitda0
        if m.interest_coverage is None and oi0 is not None and ie0:
            m.interest_coverage = oi0 / ie0
        if (m.net_debt_to_ebitda is None and m.ebitda and debt0 is not None
                and m.cash_and_equivalents is not None):
            m.net_debt_to_ebitda = (debt0 - m.cash_and_equivalents) / m.ebitda
        # EV/EBIT earnings yield (absolute valuation leg, §2.2). EBIT = operating
        # income; EV = market_cap + net_debt. Same positional [0] alignment the
        # net_debt_to_ebitda derivation above already relies on. Sole producer
        # (no source sets it) -> the is-None guard is for symmetry. UNITS: USD.
        if m.ebit_ev_yield is None:
            m.ebit_ev_yield = compute_ebit_ev_yield(
                oi0, m.market_cap, net_debt_from(debt0, m.cash_and_equivalents))
        # Total shareholder yield (PREDICTIVE_SIGNALS §5): (dividends + net buybacks +
        # net debt reduction) / market_cap. Derived here (not at extraction) because it
        # needs market_cap, a price-merge product. The financing legs are latest-FY dollar
        # magnitudes; shareholder_yield() abs()-normalizes each, nets repayments-issuance
        # (signed), and abstains when all legs or market_cap are absent.
        if m.shareholder_yield is None:
            m.shareholder_yield = shareholder_yield(
                st.dividends_paid, st.repurchases,
                st.debt_repayments, st.debt_issuance, m.market_cap)
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
            for e, end in zip(eps, ends, strict=True):
                px = _close_near(pr.monthly_closes, end)
                if px and e:
                    annual_pe.append(px / e)
            m.pe_median_5y = median_pe(annual_pe)   # None if < 2 points (min_points=2)

    si = snap.short_interest
    if si:
        dtc = si.days_to_cover
        m.days_to_cover = dtc if (dtc is not None and dtc < _DTC_SENTINEL) else None
        if si.short_shares is not None and m.market_cap and m.price:
            shares_out = m.market_cap / m.price
            pct = si.short_shares / shares_out if shares_out else None
            if pct is not None and 0.0 <= pct <= _MAX_PLAUSIBLE_SHORT_PCT:
                m.short_pct_outstanding = pct
        if (si.short_shares is not None and si.prev_short_shares is not None
                and not si.split_flag):
            m.short_interest_rising = si.short_shares > si.prev_short_shares
        m.short_data_age_days = _age_days(snap.as_of, si.settlement_date)

    soc = snap.social
    if soc:
        # rising/delta are re-derived here from raw facts (the ShortInterest pattern);
        # the apewisdom leaf computes the parallel WsbMention fields for the scout
        # consumer — keep both derivations in lockstep if you edit either.
        m.social_mentions = soc.mentions
        m.social_rank = soc.rank
        if soc.mentions is not None and soc.mentions_24h_ago is not None:
            m.social_mentions_rising = soc.mentions > soc.mentions_24h_ago
        if soc.mentions is not None and soc.mentions_24h_ago:
            m.social_mention_delta_pct = (soc.mentions - soc.mentions_24h_ago) / soc.mentions_24h_ago
        m.social_data_age_days = _age_days(snap.as_of, soc.as_of)

    # Government contracts (USAspending). Runs AFTER the statements block above, so
    # m.revenue is populated for the materiality ratio. Raw facts -> derived ratios.
    gc = snap.gov_contracts
    if gc:
        m.gov_contract_ttm_usd = gc.ttm_obligated
        m.gov_contract_prior_ttm_usd = gc.prior_ttm_obligated
        m.gov_contract_award_count = gc.award_count_ttm
        m.gov_contract_match_confidence = gc.match_confidence
        m.gov_contract_recipient_count = gc.recipient_count
        m.gov_contract_truncated = gc.truncated
        m.gov_contract_total_txns = gc.total_txns
        if gc.ttm_obligated is not None and gc.prior_ttm_obligated:  # truthy excludes 0
            m.gov_contract_yoy_growth = (
                (gc.ttm_obligated - gc.prior_ttm_obligated) / gc.prior_ttm_obligated)
        if gc.ttm_obligated is not None and m.revenue:  # truthy excludes 0/None
            m.gov_contract_to_revenue = gc.ttm_obligated / m.revenue
        # Staleness from the newest captured Action Date (real data age), not the
        # query date — falls back to query date when no action captured.
        m.gov_contract_data_age_days = _age_days(snap.as_of, gc.latest_action or gc.as_of)
    # Federal lobbying (Senate LDA). Raw facts -> derived YoY + staleness. None-safe.
    lb = snap.lobbying
    if lb:
        m.lobbying_ttm_usd = lb.ttm_spend
        m.lobbying_prior_ttm_usd = lb.prior_ttm_spend
        m.lobbying_filing_count = lb.filing_count_ttm
        m.lobbying_registrant_count = lb.registrant_count
        m.lobbying_match_confidence = lb.match_confidence
        m.lobbying_truncated = lb.truncated
        m.lobbying_total_filings = lb.total_filings
        if lb.ttm_spend is not None and lb.prior_ttm_spend:  # truthy excludes 0
            m.lobbying_yoy_growth = (
                (lb.ttm_spend - lb.prior_ttm_spend) / lb.prior_ttm_spend)
        m.lobbying_data_age_days = _age_days(snap.as_of, lb.latest_filing or lb.as_of)
    # News flow (Finnhub company-news). Raw counts -> rising + staleness. None-safe.
    nf = snap.news
    if nf:
        m.news_count_7d = nf.count_recent
        m.news_count_prior_7d = nf.count_prior
        m.news_count_30d = nf.count_window
        m.news_truncated = nf.truncated
        # rising only when prior is reliable (None when the free-tier cap truncated it)
        if nf.count_recent is not None and nf.count_prior is not None:
            m.news_flow_rising = nf.count_recent > nf.count_prior
        m.news_data_age_days = _age_days(snap.as_of, nf.latest_dt)
    # Earnings execution (Finnhub surprises + calendar). Raw facts -> rates. None-safe.
    e = snap.earnings
    if e:
        if e.quarters:
            m.earnings_quarters = e.quarters
            if e.beats is not None:
                m.earnings_beats = e.beats
                m.earnings_beat_rate = e.beats / e.quarters
        if e.recent_surprise_pcts:
            m.earnings_avg_surprise_pct = sum(e.recent_surprise_pcts) / len(e.recent_surprise_pcts)
        m.earnings_last_surprise_pct = e.last_surprise_pct
        # days_to_next: as_of -> next_date (forward); _age_days returns as_of - other,
        # so negate to get a positive "days until".
        days = _age_days(snap.as_of, e.next_date)
        if days is not None:
            m.earnings_days_to_next = -days
        # SUE inputs (PREDICTIVE_SIGNALS §1). Dispersion = pop std-dev of the firm's own
        # recent surprises (the SUE denominator); None below the min-quarters floor — its
        # presence also serves as the >=min-quarters guard for the scoring SUE leg.
        m.earnings_surprise_dispersion = surprise_dispersion(e.recent_surprise_pcts)
        # days_since_last_report: as_of - last_report_date. APPROXIMATION (see _earnings):
        # the last-announcement date is taken from the calendar where available, else the
        # fiscal quarter-END (which over-states staleness). Keyed off the PAST report, so
        # the SUE decay anchor never leaks the next (unannounced) report.
        m.earnings_days_since_last_report = _age_days(snap.as_of, e.last_report_date)

    # Accepted parity gap (left None): eps_revision (Alpha Vantage, out of scope).

    ev = snap.events
    if ev is not None:
        m.recent_8k = ev.recent_8k
        m.activist_13d = ev.activist_13d
        m.passive_13g = ev.passive_13g
        m.planned_insider_sale_144 = ev.planned_insider_sale_144
        m.filing_events = [dataclasses.asdict(e) for e in ev.recent]

    return m
