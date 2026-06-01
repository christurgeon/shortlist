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
    price: Optional[float] = None
    market_cap: Optional[float] = None

    # Valuation
    pe_ttm: Optional[float] = None
    pe_median_5y: Optional[float] = None
    fcf_yield: Optional[float] = None
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

    # Moat proxies
    gross_margin_stability: Optional[float] = None  # 0..1, higher = steadier margins
    roic_5y_avg: Optional[float] = None

    # Growth (fundamental compounding — distinct from price momentum)
    revenue_cagr: Optional[float] = None
    fcf_cagr: Optional[float] = None
    eps_cagr: Optional[float] = None              # net-income CAGR proxy (no share-count series yet)
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

    # Filing-stream events (enrichment only; NOT scored — default None so the
    # screener merge.py never stamps phantom provenance for them). Set by the
    # harness bridge when snap.events is present.
    recent_8k: Optional[bool] = None
    activist_13d: Optional[bool] = None
    passive_13g: Optional[bool] = None
    planned_insider_sale_144: Optional[bool] = None
    filing_events: Optional[list] = None   # list of {form, filed, accession, url} dicts

    # Short interest (FINRA consolidated; derived in bridge.py). Soft-flag inputs only.
    short_pct_outstanding: Optional[float] = None  # short_shares / (market_cap/price); under-states float
    days_to_cover: Optional[float] = None          # FINRA-supplied; 999.99 sentinel -> None
    short_interest_rising: Optional[bool] = None   # current > prior cycle; None across a split
    short_data_age_days: Optional[int] = None      # as_of - settlement_date (staleness guard input)

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
    ("ok" | "gated_402" | "empty" | "error"); `unavailable` lists output fields
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
    opportunity: Optional[float]  # max(momentum, value): qualifies on either axis
    insider: Optional[float]
    gates: list[str] = field(default_factory=list)  # tripped hard filters
    flags: list[str] = field(default_factory=list)  # soft advisories (e.g. crowded_short); NOT disqualifying
    metrics: Optional[StockMetrics] = None
    coverage: Optional["Coverage"] = None

    @property
    def passed(self) -> bool:
        return not self.gates
