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
    # EBIT/EV earnings yield (absolute valuation leg, §2.2; higher = cheaper).
    # Backtest-measured; NOT yet a production sub-score leg (spec §11). UNFITTED prior.
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
    # Diluted weighted-avg share-count CAGR (+ = net issuance/dilution, - = net
    # buyback). Always populated where a share-count series exists; feeds the opt-in
    # quality dilution leg + the advisory `dilution` flag. UNFITTED prior.
    share_count_cagr: Optional[float] = None
    # Investment & earnings-quality fundamentals (PREDICTIVE_SIGNALS §3). Both are
    # NEGATIVE predictors (high -> lower future returns); the opt-in quality legs
    # invert the band. asset_growth = Assets_t/Assets_{t-1}-1 over consecutive
    # fiscal ends; accruals = (NetIncome-CFO)/avg-assets (Sloan). UNFITTED priors.
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None
    # Total shareholder yield (PREDICTIVE_SIGNALS §5; Boudoukh et al. 2007 / Faber):
    # (dividends + net buybacks + net debt reduction) / market_cap — cash RETURNED to
    # owners, the legs fcf_yield misses. A POSITIVE predictor; the opt-in value leg
    # scores it straight (higher yield -> higher score). UNFITTED prior. Net debt issuers
    # can carry a NEGATIVE leg (debt issuance is the opposite of returning cash).
    shareholder_yield: Optional[float] = None

    # Leverage / coverage (ASSESSMENT_GAPS §2.7). Absolute USD. revenue feeds the
    # EBITDA-margin denominator floor; net_debt_to_ebitda is SIGNED (net cash -> <0)
    # and read raw by the over_leveraged gate (the display floor is serializer-only).
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
    eps_revision: Optional[float] = None     # trailing estimate revision trend
    realized_vol: Optional[float] = None     # annualized stdev of daily returns (risk, unscored)
    max_drawdown: Optional[float] = None     # trailing ~1y peak-to-trough, negative (risk, unscored)

    # Analyst sentiment
    rating_buy: Optional[int] = None
    rating_hold: Optional[int] = None
    rating_sell: Optional[int] = None

    # Insider activity (the "minimal insider selling" criterion lives here)
    insider_net_6m: Optional[float] = None     # net USD: buys positive, sells negative
    insider_sentiment: Optional[float] = None  # -1..1, Finnhub MSPR-style net signal
    # Insider conviction (Form 4 enrichment; None unless the conviction feature is on)
    insider_distinct_buyers: Optional[int] = None
    insider_role_weighted_buy_value: Optional[float] = None
    insider_planned_sell_value: Optional[float] = None

    # Fundamental-quality (Piotroski-inspired Core-6, asset-free). Raw counts; the
    # min-legs floor + sector masking + fraction are applied in scoring. None on
    # stacks without full statements (e.g. the lean screener path -> abstains).
    piotroski_f: Optional[int] = None        # tests won (0..6)
    piotroski_f_legs: Optional[int] = None   # tests evaluated (fraction denominator)

    # Filing-stream events (enrichment only; NOT scored — default None so the
    # screener merge.py never stamps phantom provenance for them). Set by the
    # harness bridge when snap.events is present.
    recent_8k: Optional[bool] = None
    activist_13d: Optional[bool] = None
    passive_13g: Optional[bool] = None
    planned_insider_sale_144: Optional[bool] = None
    filing_events: Optional[list] = None   # list of {form, filed, accession, url} dicts

    # Recent open-market insider Form-4 trades (enrichment only; NOT scored). Set by the
    # bridge from snap.insider.recent — a compact {date, name, role, kind, value} per trade
    # (newest-first) for the research brief's context, parallel to filing_events.
    insider_recent: Optional[list] = None

    # Up-to-5y financial series (newest-first), each entry a dict:
    # {fiscal_year, period_end, revenue, gross_profit, net_income,
    #  operating_cash_flow, free_cash_flow, diluted_eps, total_debt, diluted_shares}.
    # Plain list-of-dicts (not the data.Statements type) to avoid a core->data import;
    # research quant-context only, never read by the scorer. None on stacks without
    # full statements (e.g. the lean screener path).
    financial_series: Optional[list] = None

    # Short interest (FINRA consolidated; derived in bridge.py). Soft-flag inputs only.
    short_pct_outstanding: Optional[float] = None  # short_shares / (market_cap/price); under-states float
    days_to_cover: Optional[float] = None          # FINRA-supplied; 999.99 sentinel -> None
    short_interest_rising: Optional[bool] = None   # current > prior cycle; None across a split
    short_data_age_days: Optional[int] = None      # as_of - settlement_date (staleness guard input)

    # Social / retail attention (WSB via ApeWisdom; derived in bridge.py).
    # Soft-flag inputs only — NOT scored, never feed any sub-score or the composite.
    social_mentions: Optional[int] = None          # WSB mentions in the latest cycle
    social_mentions_rising: Optional[bool] = None  # mentions > 24h-ago
    social_mention_delta_pct: Optional[float] = None  # (mentions - prev)/prev
    social_rank: Optional[int] = None              # ApeWisdom volume rank (1 = most-mentioned)
    social_data_age_days: Optional[int] = None     # as_of - fetch date (staleness guard input)

    # Government contracts (USAspending via gov_contracts source; derived in bridge.py).
    # NOT scored in v1 — flat data + a research context line only (no sub-score, no flag).
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
    # Federal lobbying (Senate LDA via lobbying source; derived in bridge.py).
    # NOT scored in v1 — flat data + a research context line only (no sub-score, no flag).
    lobbying_ttm_usd: Optional[float] = None        # USD on federal lobbying, trailing 12m
    lobbying_prior_ttm_usd: Optional[float] = None  # USD, 12-24m
    lobbying_yoy_growth: Optional[float] = None     # (ttm - prior)/prior
    lobbying_filing_count: Optional[int] = None     # captured filings, 12m
    lobbying_registrant_count: Optional[int] = None  # distinct registrants in the sum
    lobbying_match_confidence: Optional[float] = None  # client match 0-1
    lobbying_truncated: Optional[bool] = None       # paging hit cap -> sum is partial
    lobbying_total_filings: Optional[int] = None    # pre-match filing count (search breadth)
    lobbying_data_age_days: Optional[int] = None    # days since latest filing
    # News flow (Finnhub company-news; derived in bridge.py). Soft-flag inputs only —
    # NOT scored, never feed any sub-score or the composite.
    news_count_7d: Optional[int] = None            # articles in the last 7d
    news_count_prior_7d: Optional[int] = None      # articles in days 7-14
    news_count_30d: Optional[int] = None           # articles in the 30d lookback
    news_flow_rising: Optional[bool] = None        # count_7d > prior 7d (None if truncated)
    news_truncated: Optional[bool] = None          # free-tier cap hit -> counts are lower bounds
    news_data_age_days: Optional[int] = None       # as_of - latest article date (staleness)
    # Earnings execution (Finnhub earnings surprises + calendar; derived in bridge.py).
    # NOT scored in v1 — flat data + a research context line only.
    earnings_beat_rate: Optional[float] = None     # fraction of recent quarters that beat
    earnings_beats: Optional[int] = None           # # recent quarters that beat (raw count)
    earnings_avg_surprise_pct: Optional[float] = None  # mean surprise % over recent quarters
    earnings_last_surprise_pct: Optional[float] = None # newest quarter's surprise %
    earnings_quarters: Optional[int] = None        # # recent quarters with a usable surprise
    earnings_days_to_next: Optional[int] = None    # days until the next scheduled report

    # Bookkeeping: which provider supplied each populated field
    sources: dict = field(default_factory=dict)

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
    """Why a ticker's data is thin. `providers` maps provider name -> status
    ("ok" | "gated_402" | "rate_limited_429" | "empty" | "error"); `unavailable` lists output fields
    that came out null (fact); `note` is interpretive prose for recognized
    patterns (e.g. FMP symbol gating). See coverage.py for assembly."""
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
    # Sector-aware applicability (appended after coverage so positional construction
    # through `insider` is unaffected). sic_bucket: resolved sector bucket (or
    # "unknown"); confidence: present-applicable component weight / applicable
    # weight; scored: above the validity floor (always True for unknown bucket);
    # abstentions: [{field, reason: inapplicable|missing, scope: leg|subscore}].
    sic_bucket: Optional[str] = None
    confidence: float = 1.0
    scored: bool = True
    abstentions: list = field(default_factory=list)
    # 7th sub-score (risk). Appended last so positional construction through the
    # leading fields is unaffected. Composite-only tilt; excluded from confidence.
    risk: Optional[float] = None
    # Display-only coverage advisory (confidence < ranking.thin_below). Derived from
    # confidence; never feeds rank_key/passed/composite. Appended last.
    thin: bool = False
    # Surfaced fundamental-quality score (won/legs). Display + advisory only; never
    # feeds composite/passed/scored. Appended last.
    piotroski_f: Optional[int] = None
    piotroski_f_legs: Optional[int] = None
    # Surfaced diluted-share-count CAGR (+ = dilution). Display + advisory only;
    # never feeds composite/passed/scored. Appended last.
    share_count_cagr: Optional[float] = None
    # Surfaced leverage metrics (display + advisory only; the GATE reads m.* directly).
    # net_debt_to_ebitda is signed here; screen.py applies the net-cash display floor.
    ebitda: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None

    @property
    def passed(self) -> bool:
        return not self.gates and self.scored


def rank_key(card) -> tuple:
    """Ranking order, descending: scored first, then composite, then confidence as a
    tiebreaker. composite is rounded to 0.1 (scoring.py), so confidence only decides
    exact ties — a higher composite always wins (we never bury a strong-but-thin name).
    getattr-based so it also works on the duck-typed cards enrich() accepts. Single
    source of truth for every sort site (screen, research, scout)."""
    return (getattr(card, "scored", True), card.composite, getattr(card, "confidence", 1.0))
