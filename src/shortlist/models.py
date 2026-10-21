from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StockMetrics:
    """Normalized metrics for one ticker, merged across providers.

    All margins/returns are stored as fractions (0.42 == 42%), not percentages,
    so scoring thresholds stay consistent regardless of source formatting.
    """

    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    sic: Optional[str] = None   # SEC SIC code (EDGAR-sourced); drives the sector bucket
    price: Optional[float] = None
    market_cap: Optional[float] = None

    # Valuation
    pe_ttm: Optional[float] = None
    pe_median_5y: Optional[float] = None
    fcf_yield: Optional[float] = None
    # EBIT/EV; higher = cheaper. Backtest axis only, not a production leg.
    ebit_ev_yield: Optional[float] = None
    peg: Optional[float] = None
    target_median: Optional[float] = None

    # Quality
    roe: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    interest_coverage: Optional[float] = None
    fcf_positive: Optional[bool] = None
    # Diluted weighted-avg share-count CAGR: + = dilution, - = buyback.
    share_count_cagr: Optional[float] = None
    # Both are negative predictors (high -> lower returns), so the opt-in legs invert
    # the band. asset_growth = Assets_t/Assets_{t-1}-1; accruals = (NetIncome-CFO)/avg assets.
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None
    # (dividends + net buybacks + net debt reduction) / market_cap. Scored straight;
    # a net debt issuer can go negative.
    shareholder_yield: Optional[float] = None

    # Absolute USD. net_debt_to_ebitda is SIGNED (net cash < 0) and read raw by the
    # over_leveraged gate; any display floor is serializer-only.
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None

    # Moat proxies
    gross_margin_stability: Optional[float] = None  # 0..1, higher = steadier margins
    roic_5y_avg: Optional[float] = None

    # Growth (fundamental compounding — distinct from price momentum)
    revenue_cagr: Optional[float] = None
    fcf_cagr: Optional[float] = None
    eps_cagr: Optional[float] = None              # net-income CAGR proxy (dilution-blind)
    eps_cagr_ps: Optional[float] = None           # genuine per-share diluted-EPS CAGR (dilution-aware)
    revenue_growth_persistence: Optional[float] = None  # 0..1, fraction of YoY periods that grew

    # Momentum
    price_vs_200dma: Optional[float] = None  # (price / 200dma) - 1
    rel_strength_6m: Optional[float] = None  # 6m return minus benchmark 6m return
    residual_momentum: Optional[float] = None  # 12-1 momentum of CAPM residuals, vol-scaled
    eps_revision: Optional[float] = None     # trailing estimate revision trend
    realized_vol: Optional[float] = None     # annualized stdev of daily returns (risk, unscored)
    max_drawdown: Optional[float] = None     # trailing ~1y peak-to-trough, negative (risk, unscored)
    # Backtest axes only.
    pct_to_52w_high: Optional[float] = None      # closes[-1]/max(last 252) in (0,1]
    max_daily_return: Optional[float] = None     # largest daily return, last ~21d; negative predictor
    vol_scaled_momentum: Optional[float] = None  # mom_12_1 / 6m realized vol

    # Analyst sentiment
    rating_buy: Optional[int] = None
    rating_hold: Optional[int] = None
    rating_sell: Optional[int] = None
    # Rating revision: deltas only. See Analyst in data/models.py for why no prior level.
    rating_months: Optional[int] = None
    rating_buy_delta: Optional[int] = None
    rating_hold_delta: Optional[int] = None
    rating_sell_delta: Optional[int] = None

    # Insider activity (the "minimal insider selling" criterion lives here)
    insider_net_6m: Optional[float] = None     # net USD: buys positive, sells negative
    insider_sentiment: Optional[float] = None  # -1..1, Finnhub MSPR-style net signal
    # Insider conviction (Form 4 enrichment; None unless the conviction feature is on)
    insider_distinct_buyers: Optional[int] = None
    insider_role_weighted_buy_value: Optional[float] = None
    insider_planned_sell_value: Optional[float] = None

    # Raw counts; the min-legs floor, sector masking and fraction are applied in scoring.
    # None without full statements (abstains).
    piotroski_f: Optional[int] = None        # tests won (0..6)
    piotroski_f_legs: Optional[int] = None   # tests evaluated (fraction denominator)

    # Filing-stream events. Enrichment only, NOT scored. Set by the bridge when
    # snap.events is present.
    recent_8k: Optional[bool] = None
    activist_13d: Optional[bool] = None
    passive_13g: Optional[bool] = None
    planned_insider_sale_144: Optional[bool] = None
    late_filing: Optional[bool] = None
    shelf_offering: Optional[bool] = None
    sec_comment_letter: Optional[bool] = None
    restatement_8k: Optional[bool] = None
    auditor_change: Optional[bool] = None
    listing_deficiency: Optional[bool] = None
    # list of {form, filed, accession, url, items} dicts (items = 8-K item codes, else None)
    filing_events: Optional[list[dict]] = None

    # Recent open-market Form-4 trades, newest-first: {date, name, role, kind, value}.
    # Enrichment only, NOT scored.
    insider_recent: Optional[list[dict]] = None

    # Up-to-5y statements, newest-first, one dict per fiscal year (keys as in
    # data.Statements). A plain list of dicts to avoid a core->data import. Research
    # context only; never read by the scorer.
    financial_series: Optional[list[dict]] = None
    # /deep capital-allocation section only; never read by the scorer or the prompt.
    # {fiscal_year, nopat, invested_capital}, newest-first, on the computed-ROIC basis.
    invested_capital_series: Optional[list[dict]] = None
    # Latest fiscal year's cash-flow outflows, signed as filed (outflows usually negative).
    dividends_paid: Optional[float] = None
    repurchases: Optional[float] = None

    # Short interest (FINRA). Soft-flag inputs only.
    short_pct_outstanding: Optional[float] = None  # short_shares / (market_cap/price); under-states float
    days_to_cover: Optional[float] = None          # FINRA-supplied; 999.99 sentinel -> None
    short_interest_rising: Optional[bool] = None   # current > prior cycle; None across a split
    short_data_age_days: Optional[int] = None      # as_of - settlement_date (staleness guard input)

    # Social attention (WSB via ApeWisdom). Soft-flag inputs only.
    social_mentions: Optional[int] = None          # WSB mentions in the latest cycle
    social_mentions_rising: Optional[bool] = None  # mentions > 24h-ago
    social_mention_delta_pct: Optional[float] = None  # (mentions - prev)/prev
    social_rank: Optional[int] = None              # ApeWisdom volume rank (1 = most-mentioned)
    social_data_age_days: Optional[int] = None     # as_of - fetch date (staleness guard input)

    # Government contracts (USAspending). Research context only, NOT scored.
    gov_contract_ttm_usd: Optional[float] = None        # net USD obligated, trailing 12m
    gov_contract_prior_ttm_usd: Optional[float] = None  # net USD obligated, 12-24m
    gov_contract_yoy_growth: Optional[float] = None     # (ttm - prior)/prior
    gov_contract_award_count: Optional[int] = None      # captured txn count, 12m
    gov_contract_to_revenue: Optional[float] = None     # ttm_usd / revenue (materiality)
    gov_contract_match_confidence: Optional[float] = None  # primary recipient match 0-1
    gov_contract_recipient_count: Optional[int] = None  # distinct matched recipients in the sum
    gov_contract_truncated: Optional[bool] = None       # paging hit cap -> sum is partial
    gov_contract_total_txns: Optional[int] = None       # pre-match action count (search breadth)
    gov_contract_data_age_days: Optional[int] = None    # days since latest captured Action Date
    # Federal lobbying (Senate LDA). Research context only, NOT scored.
    lobbying_ttm_usd: Optional[float] = None        # USD on federal lobbying, trailing 12m
    lobbying_prior_ttm_usd: Optional[float] = None  # USD, 12-24m
    lobbying_yoy_growth: Optional[float] = None     # (ttm - prior)/prior
    lobbying_filing_count: Optional[int] = None     # captured filings, 12m
    lobbying_registrant_count: Optional[int] = None  # distinct registrants in the sum
    lobbying_match_confidence: Optional[float] = None  # client match 0-1
    lobbying_truncated: Optional[bool] = None       # paging hit cap -> sum is partial
    lobbying_total_filings: Optional[int] = None    # pre-match filing count (search breadth)
    lobbying_data_age_days: Optional[int] = None    # days since latest filing
    # News flow (Finnhub). Soft-flag inputs only.
    news_count_7d: Optional[int] = None            # articles in the last 7d
    news_count_prior_7d: Optional[int] = None      # articles in days 7-14
    news_count_30d: Optional[int] = None           # articles in the 30d lookback
    news_flow_rising: Optional[bool] = None        # count_7d > prior 7d (None if truncated)
    news_truncated: Optional[bool] = None          # free-tier cap hit -> counts are lower bounds
    news_data_age_days: Optional[int] = None       # as_of - latest article date (staleness)
    # Earnings execution (Finnhub). Research context only, except the SUE inputs below.
    earnings_beat_rate: Optional[float] = None     # fraction of recent quarters that beat
    earnings_beats: Optional[int] = None           # # recent quarters that beat (raw count)
    earnings_avg_surprise_pct: Optional[float] = None  # mean surprise % over recent quarters
    earnings_last_surprise_pct: Optional[float] = None # newest quarter's surprise %
    earnings_quarters: Optional[int] = None        # # recent quarters with a usable surprise
    earnings_days_to_next: Optional[int] = None    # days until the next scheduled report
    # SUE inputs; the decayed leg itself is computed in scoring.
    earnings_surprise_dispersion: Optional[float] = None  # pop std-dev of recent surprise % (>=min quarters)
    earnings_days_since_last_report: Optional[int] = None  # APPROX days since the last announcement

    # Cosine of Item-1A + MD&A tokens vs the prior same-type filing. Input to the
    # `filing_text_change` flag, which is disabled (see config.yaml). No producer today.
    filing_text_similarity: Optional[float] = None  # 1.0 == unchanged, 0.0 == fully rewritten

    # Bookkeeping: which provider supplied each populated field
    sources: dict[str, str] = field(default_factory=dict)

    def upside_to_target(self) -> Optional[float]:
        if self.price and self.target_median:
            return self.target_median / self.price - 1.0
        return None

    def pe_vs_history(self) -> Optional[float]:
        """Positive => cheaper than its own 5y median (room to re-rate up)."""
        if self.pe_ttm and self.pe_median_5y and self.pe_ttm > 0:
            return self.pe_median_5y / self.pe_ttm - 1.0
        return None


@dataclass
class Coverage:
    """Why a ticker's data is thin. `providers` maps provider name -> status ("ok" |
    "gated_402" | "rate_limited_429" | "empty" | "error"); `unavailable` lists null output
    fields; `note` is interpretive prose for recognized patterns. Built in coverage.py."""
    providers: dict[str, str]
    unavailable: list[str]
    note: Optional[str] = None


@dataclass
class ScoreCard:
    ticker: str
    composite: float
    quality: Optional[float]
    moat: Optional[float]
    growth: Optional[float]
    momentum: Optional[float]
    value: Optional[float]
    opportunity: Optional[float]  # display-only: max(momentum, value); does NOT feed the composite
    insider: Optional[float]
    gates: list[str] = field(default_factory=list)  # tripped hard filters
    flags: list[str] = field(default_factory=list)  # soft advisories (e.g. crowded_short); NOT disqualifying
    metrics: Optional[StockMetrics] = None
    coverage: Optional["Coverage"] = None
    # Fields below `coverage` are appended so positional construction stays valid.
    # sic_bucket: resolved bucket or "unknown"; confidence: present-applicable
    # component weight / applicable weight; scored: above the validity floor (always
    # True for "unknown"); abstentions: [{field, reason: inapplicable|missing,
    # scope: leg|subscore}].
    sic_bucket: Optional[str] = None
    confidence: float = 1.0
    scored: bool = True
    abstentions: list[dict[str, str]] = field(default_factory=list)
    # Excluded from confidence.
    risk: Optional[float] = None
    # Display only: confidence < ranking.thin_below. Never feeds rank_key/passed/composite.
    thin: bool = False
    # Surfaced for display and advisories; never feed composite/passed/scored.
    piotroski_f: Optional[int] = None
    piotroski_f_legs: Optional[int] = None
    share_count_cagr: Optional[float] = None
    # Display only; the gate reads m.* directly. net_debt_to_ebitda is signed here.
    ebitda: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None

    @property
    def passed(self) -> bool:
        return not self.gates and self.scored


def rank_key(card) -> tuple[bool, float, float]:
    """Descending sort key: scored first, then composite, then confidence as a tiebreaker
    (a higher composite always wins). Duck-typed so it works on cards enrich() accepts.
    Single source of truth for every sort site."""
    return (getattr(card, "scored", True), card.composite, getattr(card, "confidence", 1.0))
