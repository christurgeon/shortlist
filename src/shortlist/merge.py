from __future__ import annotations

from dataclasses import fields

from .models import StockMetrics

# Which source wins for each field, in order. Falls back to `default` ordering
# for any field not listed. Insider data prefers EDGAR (authoritative) then
# Finnhub (clean sentiment) then FMP; fundamentals prefer FMP.
DEFAULT_PRIORITY: dict[str, list[str]] = {
    "insider_net_6m": ["edgar", "finnhub", "fmp"],
    "insider_sentiment": ["finnhub", "fmp"],
    "eps_revision": ["finnhub", "fmp"],
    "default": ["fmp", "finnhub", "edgar", "mock"],
}


def merge(per_provider: list[StockMetrics], priority: dict | None = None) -> StockMetrics:
    """Combine several providers' StockMetrics for one ticker into a single
    record, taking each field from the highest-priority source that has it."""
    priority = priority or DEFAULT_PRIORITY
    ticker = per_provider[0].ticker if per_provider else ""
    out = StockMetrics(ticker=ticker)

    for f in fields(StockMetrics):
        if f.name in ("ticker", "sources"):
            continue
        order = priority.get(f.name, priority["default"])
        chosen = _pick(per_provider, f.name, order)
        if chosen is not None:
            value, src = chosen
            setattr(out, f.name, value)
            out.sources[f.name] = src
    return out


def _pick(metrics: list[StockMetrics], field_name: str, order: list[str]):
    # Try sources in priority order, then any remaining provider.
    ranked = sorted(
        metrics,
        key=lambda m: order.index(_provider_of(m)) if _provider_of(m) in order else len(order),
    )
    for m in ranked:
        v = getattr(m, field_name, None)
        if v is not None:
            return v, _provider_of(m)
    return None


def _provider_of(m: StockMetrics) -> str:
    # The provider stamps its name on every field it set; use the most common.
    if not m.sources:
        return "unknown"
    return max(set(m.sources.values()), key=list(m.sources.values()).count)
