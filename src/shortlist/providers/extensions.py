from __future__ import annotations

import os
from typing import Optional

from ..models import StockMetrics
from .base import Provider

# These two are scaffolded, not wired, on purpose — they're the "add value"
# extensions. Each has a clear reason to exist beyond the core three sources.


class QuiverProvider(Provider):
    """Differentiated alternative data: U.S. congressional trades, government
    contract awards, and lobbying. Government-contract flow is *directly*
    relevant to defense/industrial names (e.g. LMT, GEV) and isn't captured by
    any fundamentals feed. This is where a real edge can come from.

    Endpoint family: https://api.quiverquant.com/beta/  (paid)
    Suggested fields to populate: a `gov_contract_momentum` and a
    `congress_net_buy` signal — add them to StockMetrics and the scorer as a
    new sub-score weighted low until you've validated it.
    """

    name = "quiver"

    def __init__(self, api_key: Optional[str] = None):
        self.key = api_key or os.environ.get("QUIVER_API_KEY")

    def fetch(self, ticker: str) -> StockMetrics:
        raise NotImplementedError(
            "Quiver is a recommended add-on. Wire congress/gov-contract signals here."
        )


class FredProvider(Provider):
    """Free macro overlay (10y yield, fed funds, 2s10s curve). Not per-stock —
    use it to gate or tilt the *whole* run (e.g. de-emphasize rate-sensitive
    names when the curve is moving against them). Plug into screen.py as a
    context object rather than a per-ticker provider.

    pip install fredapi ; export FRED_API_KEY=...
    """

    name = "fred"

    def __init__(self, api_key: Optional[str] = None):
        self.key = api_key or os.environ.get("FRED_API_KEY")

    def fetch(self, ticker: str) -> StockMetrics:
        raise NotImplementedError("FRED is a macro overlay, not a per-ticker source.")
