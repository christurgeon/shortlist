"""Portfolio-awareness leaf: given holdings and the ScoreCards the harness already
produced, compute exposure + monitoring alerts.

Pure and dependency-light (the ScoreCard type and the no_data predicate); no I/O. Safe on
the always-on bot path. Holdings come from the bot's position store (positions.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import ScoreCard
from .validation import no_data as _no_data


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: Optional[float]


@dataclass(frozen=True)
class Position:
    ticker: str
    shares: float
    price: Optional[float]
    value: Optional[float]
    weight: Optional[float]
    card: Optional[ScoreCard]
    no_data: bool = False


@dataclass(frozen=True)
class PortfolioSummary:
    positions: list[Position]                 # weight-desc, priceless last
    total_value: Optional[float]
    sector_weights: list[tuple[str, float]]   # (sic_bucket, weight) desc
    alerts: list[Position]
    priced_count: int
    unpriced: list[str]
    no_data_tickers: list[str]
    weighted_composite: Optional[float]


def _is_alert(card: Optional[ScoreCard], no_data: bool) -> bool:
    if no_data:
        return True
    return bool(card.gates) or bool(card.flags) or not getattr(card, "scored", True)


def summarize(holdings: list[Holding], cards: list[ScoreCard]) -> PortfolioSummary:
    """Pure. Join holdings to ScoreCards by ticker; compute exposure, sector mix and alerts.

    A holding with no card, or a no-data card (validation.no_data), is an alert and
    has no exposure, so a CSV typo cannot hide.

    Weights are NET exposure over the sum of priced values. One holding can exceed
    100%, a short gets a negative weight, and weights sum to 1.0. This is intended."""
    by_ticker = {c.ticker: c for c in cards}
    raw: list[dict] = []
    total = 0.0
    for h in holdings:
        card = by_ticker.get(h.ticker)
        nd = card is None or _no_data(card)
        price = None
        if card is not None and not nd:
            m = getattr(card, "metrics", None)
            p = getattr(m, "price", None) if m is not None else None
            price = p if p else None          # 0/None price -> unpriced
        value = h.shares * price if (price and h.shares is not None) else None
        if value is not None:
            total += value
        raw.append({"h": h, "card": card, "nd": nd, "price": price, "value": value})

    total_value = total if total > 0 else None
    positions: list[Position] = []
    sector_acc: dict[str, float] = {}
    wcomp = 0.0
    for r in raw:
        h, card, nd, price, value = r["h"], r["card"], r["nd"], r["price"], r["value"]
        weight = (value / total_value) if (value is not None and total_value) else None
        pos = Position(h.ticker, h.shares, price, value, weight, card, nd)
        positions.append(pos)
        if weight is not None:
            bucket = getattr(card, "sic_bucket", None) or "unknown"
            sector_acc[bucket] = sector_acc.get(bucket, 0.0) + weight
            wcomp += weight * card.composite

    positions.sort(key=lambda p: (p.weight is not None, p.weight or 0.0), reverse=True)
    sector_weights = sorted(sector_acc.items(), key=lambda kv: kv[1], reverse=True)
    alerts = [p for p in positions if _is_alert(p.card, p.no_data)]
    unpriced = [p.ticker for p in positions if p.value is None and not p.no_data]
    no_data_tickers = [p.ticker for p in positions if p.no_data]
    priced_count = sum(1 for p in positions if p.weight is not None)
    weighted_composite = wcomp if total_value else None
    return PortfolioSummary(positions, total_value, sector_weights, alerts,
                            priced_count, unpriced, no_data_tickers, weighted_composite)
