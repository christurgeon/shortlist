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

    # Momentum
    price_vs_200dma: Optional[float] = None  # (price / 200dma) - 1
    rel_strength_6m: Optional[float] = None  # 6m return minus benchmark 6m return
    eps_revision: Optional[float] = None     # trailing estimate revision trend

    # Analyst sentiment
    rating_buy: Optional[int] = None
    rating_hold: Optional[int] = None
    rating_sell: Optional[int] = None

    # Insider activity (the "minimal insider selling" criterion lives here)
    insider_net_6m: Optional[float] = None     # net USD: buys positive, sells negative
    insider_sentiment: Optional[float] = None  # -1..1, Finnhub MSPR-style net signal

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
class ScoreCard:
    ticker: str
    composite: float
    quality: Optional[float]
    moat: Optional[float]
    momentum: Optional[float]
    value: Optional[float]
    opportunity: Optional[float]  # max(momentum, value): qualifies on either axis
    insider: Optional[float]
    gates: list[str] = field(default_factory=list)  # tripped hard filters
    metrics: Optional[StockMetrics] = None

    @property
    def passed(self) -> bool:
        return not self.gates
