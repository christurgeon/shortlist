from __future__ import annotations

import asyncio

from .models import TickerSnapshot, merge_snapshots
from .sources import Source, build_sources

# Default merge priority: EDGAR (when added) is authoritative for insider, FMP
# is the fundamentals backbone, Finnhub fills gaps and adds sentiment.
DEFAULT_PRIORITY = ["edgar", "fmp", "finnhub", "mock"]


async def collect_async(
    tickers: list[str],
    sources: list[Source],
    priority: list[str] | None = None,
    concurrency: int = 8,
) -> list[TickerSnapshot]:
    priority = priority or DEFAULT_PRIORITY
    sem = asyncio.Semaphore(concurrency)

    async def one(ticker: str) -> TickerSnapshot:
        async with sem:
            results = await asyncio.gather(*(s.fetch(ticker) for s in sources))
        return merge_snapshots(ticker, list(results), priority)

    try:
        return await asyncio.gather(*(one(t) for t in tickers))
    finally:
        await asyncio.gather(*(s.aclose() for s in sources), return_exceptions=True)


def collect(
    tickers: list[str],
    source_names: list[str],
    priority: list[str] | None = None,
) -> list[TickerSnapshot]:
    sources = build_sources(source_names)
    if not sources:
        return []
    return asyncio.run(collect_async([t.upper() for t in tickers], sources, priority))
