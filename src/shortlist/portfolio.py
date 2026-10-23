"""Portfolio-awareness leaf: load a hand-maintained holdings file and, given the
ScoreCards the harness already produced, compute exposure + monitoring alerts.

Pure and dependency-light: stdlib, the ScoreCard type and the no_data predicate.
Only I/O is reading the holdings file. Safe on the always-on bot path.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import ScoreCard
from .validation import no_data as _no_data


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: Optional[float]


def load_holdings(path) -> tuple[list[Holding], list[str]]:
    """Parse `ticker,shares` rows leniently. Returns (holdings, warnings).

    - A header row (first column "TICKER", any case) is skipped silently.
      Any other row with non-numeric shares is skipped with a warning.
    - Negative shares (shorts) are accepted.
    - Blank lines and `#` lines are skipped silently. Extra columns are ignored.
    - Tickers are upper-cased and stripped. Duplicates are summed with a warning.
    - Missing or unreadable file -> ([], [warning]); never raises.
    """
    p = Path(path)
    if not p.exists():
        return [], [f"Portfolio file not found: {p}"]
    warnings: list[str] = []
    totals: dict[str, float] = {}
    order: list[str] = []
    try:
        rows = list(csv.reader(p.read_text(encoding="utf-8-sig").splitlines()))
    except OSError as e:
        return [], [f"Could not read {p}: {e}"]
    for raw in rows:
        if not raw or not "".join(raw).strip():
            continue
        if raw[0].lstrip().startswith("#"):
            continue                          # annotations in a hand-kept file
        ticker = raw[0].strip().upper()
        shares_s = (raw[1] if len(raw) > 1 else "").strip()
        if not ticker or len(raw) < 2:
            warnings.append(f"Skipped row (missing ticker/shares): {raw}")
            continue
        try:
            shares = float(shares_s)
        except ValueError:
            if ticker == "TICKER":
                continue
            warnings.append(f"Skipped row (shares not a number): {raw}")
            continue
        if ticker not in totals:
            order.append(ticker)
            totals[ticker] = shares
        else:
            totals[ticker] += shares
            warnings.append(f"Duplicate ticker {ticker} (row folded into earlier entry; shares summed).")
    return [Holding(t, totals[t]) for t in order], warnings


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
