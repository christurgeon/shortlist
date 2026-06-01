from __future__ import annotations

from ..models import StockMetrics
from .base import Provider

# Illustrative sample data for offline demo / tests. Prices and targets are
# approximately as of late May 2026; margins, ROIC, and insider figures are
# ROUGH PLACEHOLDERS so the pipeline produces sensible output without API keys.
# Do NOT treat these as verified fundamentals — run a live provider for that.
_SAMPLE: dict[str, dict] = {
    "GEV": dict(
        name="GE Vernova", sector="AI power", price=969, market_cap=260e9,
        pe_ttm=28.2, pe_median_5y=30.0, fcf_yield=0.022, target_median=1217,
        roe=0.44, roic=0.18, roic_5y_avg=0.14, gross_margin=0.20, net_margin=0.13,
        debt_to_equity=0.12, interest_coverage=20, fcf_positive=True,
        revenue_cagr=0.15, fcf_cagr=0.40, eps_cagr=0.50, revenue_growth_persistence=1.0,
        gross_margin_stability=0.70, price_vs_200dma=0.25, rel_strength_6m=0.20,
        eps_revision=0.04, rating_buy=29, rating_hold=6, rating_sell=0,
        insider_net_6m=-9.0e6, insider_sentiment=-0.25,
    ),
    "LMT": dict(
        name="Lockheed Martin", sector="Defense", price=531, market_cap=122e9,
        pe_ttm=20.0, pe_median_5y=17.0, fcf_yield=0.046, target_median=615,
        roe=0.80, roic=0.20, roic_5y_avg=0.22, gross_margin=0.12, net_margin=0.067,
        debt_to_equity=2.6, interest_coverage=8, fcf_positive=True,
        revenue_cagr=0.03, fcf_cagr=0.02, eps_cagr=0.04, revenue_growth_persistence=0.75,
        gross_margin_stability=0.88, price_vs_200dma=-0.10, rel_strength_6m=-0.05,
        eps_revision=-0.02, rating_buy=6, rating_hold=14, rating_sell=1,
        insider_net_6m=-0.4e6, insider_sentiment=0.0,
    ),
    "SCHW": dict(
        name="Charles Schwab", sector="Financials", price=87, market_cap=152e9,
        pe_ttm=17.0, pe_median_5y=20.0, fcf_yield=None, target_median=115,
        roe=0.17, roic=None, roic_5y_avg=None, gross_margin=None, net_margin=0.36,
        debt_to_equity=None, interest_coverage=None, fcf_positive=True,
        revenue_cagr=0.08, eps_cagr=0.06, revenue_growth_persistence=0.50,
        gross_margin_stability=None, price_vs_200dma=-0.05, rel_strength_6m=-0.08,
        eps_revision=0.06, rating_buy=17, rating_hold=2, rating_sell=1,
        insider_net_6m=-1.2e6, insider_sentiment=-0.05,
    ),
    "TMO": dict(
        name="Thermo Fisher", sector="Healthcare", price=450, market_cap=170e9,
        pe_ttm=22.0, pe_median_5y=26.0, fcf_yield=0.045, target_median=560,
        roe=0.14, roic=0.10, roic_5y_avg=0.11, gross_margin=0.42, net_margin=0.15,
        debt_to_equity=0.7, interest_coverage=8, fcf_positive=True,
        revenue_cagr=0.07, fcf_cagr=0.06, eps_cagr=0.05, revenue_growth_persistence=0.75,
        gross_margin_stability=0.92, price_vs_200dma=-0.05, rel_strength_6m=-0.10,
        eps_revision=0.01, rating_buy=20, rating_hold=4, rating_sell=0,
        insider_net_6m=0.2e6, insider_sentiment=0.05,
    ),
    "GOOGL": dict(
        name="Alphabet", sector="Tech", price=380, market_cap=4.6e12,
        pe_ttm=28.7, pe_median_5y=23.0, fcf_yield=0.030, target_median=431,
        roe=0.32, roic=0.28, roic_5y_avg=0.26, gross_margin=0.58, net_margin=0.30,
        debt_to_equity=0.10, interest_coverage=100, fcf_positive=True,
        revenue_cagr=0.14, fcf_cagr=0.10, eps_cagr=0.16, revenue_growth_persistence=1.0,
        gross_margin_stability=0.95, price_vs_200dma=0.20, rel_strength_6m=0.18,
        eps_revision=0.03, rating_buy=57, rating_hold=8, rating_sell=0,
        insider_net_6m=-40e6, insider_sentiment=-0.30,
    ),
}


class MockProvider(Provider):
    name = "mock"

    def fetch(self, ticker: str) -> StockMetrics:
        data = _SAMPLE.get(ticker.upper())
        if not data:
            return StockMetrics(ticker=ticker)
        m = StockMetrics(ticker=ticker, **data)
        return self._tag(m, *[k for k in data if getattr(m, k, None) is not None])
