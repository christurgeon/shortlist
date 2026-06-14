from __future__ import annotations

import os
from typing import Optional

from ..models import StockMetrics
from .base import Provider

# QuiverProvider is the one genuinely unwired scaffold left — the "add value"
# extension still to implement. FRED has since shipped as a run-level macro overlay
# (data/macro.py:fetch_macro), so FredProvider below is a vestigial stub whose
# fetch() raises; it is kept only as a signpost to the overlay.


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
    """Vestigial stub — FRED already SHIPPED as a run-level macro overlay
    (data/macro.py:fetch_macro: 10y yield, fed funds, 2s10s curve, HY OAS, VIX →
    risk-off regime, display + advisory only). It is not a per-ticker source, so
    fetch() raises; use the macro overlay instead.

    export FRED_API_KEY=...
    """

    name = "fred"

    def __init__(self, api_key: Optional[str] = None):
        self.key = api_key or os.environ.get("FRED_API_KEY")

    def fetch(self, ticker: str) -> StockMetrics:
        raise NotImplementedError("FRED is a macro overlay, not a per-ticker source.")
