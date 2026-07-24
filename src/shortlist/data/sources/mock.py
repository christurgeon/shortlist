from __future__ import annotations

from ..models import SourceResult, TickerSnapshot
from .base import Source

# --- Mock: offline demo (illustrative, not verified) ----------------------

class MockSource(Source):
    name = "mock"

    async def fetch(self, ticker: str) -> SourceResult:
        from ..mockdata import SAMPLE
        data = SAMPLE.get(ticker.upper())
        res = SourceResult(source=self.name)
        if not data:
            res.errors.append(f"mock: no sample for {ticker}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res
        res.raw = {"sample": data["raw_echo"]}
        res.partial = data["snapshot"](ticker)
        return res
