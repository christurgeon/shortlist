from __future__ import annotations

import asyncio

from ..env import redact_secrets
from .models import SourceResult, TickerSnapshot, merge_snapshots
from .sources import Source, build_sources

# Default merge priority: Yahoo leads for price/momentum (keyless, auditable,
# gating-immune); EDGAR is authoritative for insider; FMP is the fundamentals
# backbone; Finnhub fills gaps and adds sentiment.
DEFAULT_PRIORITY = ["yahoo", "edgar", "fmp", "finnhub", "mock"]


async def collect_async(
    tickers: list[str],
    sources: list[Source],
    priority: list[str] | None = None,
    concurrency: int = 8,
) -> list[TickerSnapshot]:
    priority = priority or DEFAULT_PRIORITY
    sem = asyncio.Semaphore(concurrency)

    async def safe_fetch(s: Source, ticker: str) -> SourceResult:
        """Sources are documented never-raises, but a normalizer bug outside a
        per-section try would otherwise kill the whole multi-ticker gather. On an
        escape, degrade to an errored-empty SourceResult (the same shape a source's
        own except branch returns) so coverage reports it instead of crashing."""
        try:
            return await s.fetch(ticker)
        except Exception as e:
            return SourceResult(
                source=s.name,
                partial=TickerSnapshot(ticker=ticker),
                errors=[f"{s.name}: {redact_secrets(e)}"],
            )

    async def one(ticker: str) -> TickerSnapshot:
        async with sem:
            results = await asyncio.gather(*(safe_fetch(s, ticker) for s in sources))
        return merge_snapshots(ticker, list(results), priority)

    try:
        return await asyncio.gather(*(one(t) for t in tickers))
    finally:
        await asyncio.gather(*(s.aclose() for s in sources), return_exceptions=True)


def collect(
    tickers: list[str],
    source_names: list[str],
    priority: list[str] | None = None,
    config: dict | None = None,
) -> list[TickerSnapshot]:
    sources = build_sources(source_names, config=config)
    if not sources:
        return []
    return asyncio.run(collect_async([t.upper() for t in tickers], sources, priority))
