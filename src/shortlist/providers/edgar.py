from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

from ..models import StockMetrics
from ._form4 import aggregate_form4
from .base import Provider


class EdgarProvider(Provider):
    """Authoritative insider data straight from SEC Form 4 filings — the highest-
    quality source for the "minimal insider selling" criterion, and the primary
    record the paid APIs are derived from. Free, but SEC enforces ~10 req/s
    fair-access, so a large universe run must pace itself (see the harness's
    EdgarSource, which caps concurrency).

    Requires `edgartools` and a contact identity per SEC fair-access rules:
        pip install edgartools
        export SEC_IDENTITY="you@example.com"
    """

    name = "edgar"

    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        from edgar import set_identity  # imported lazily so the dep is optional

        set_identity(self.identity)

    def fetch(self, ticker: str) -> StockMetrics:
        from edgar import Company

        m = StockMetrics(ticker=ticker)
        company = Company(ticker)
        cutoff = date.today() - timedelta(days=self.lookback_days)

        summary = aggregate_form4(company.get_filings(form="4").latest(40), cutoff)
        if summary.found:
            m.insider_net_6m = summary.net_value

        return self._tag(m, "insider_net_6m")
