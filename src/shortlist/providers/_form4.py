from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

# Shared SEC Form 4 aggregation, used by BOTH the synchronous screener provider
# (providers/edgar.py) and the async harness source (data/sources.py). Kept as a
# dependency-free leaf module: it imports nothing from `edgar`/`edgartools` (it
# operates on already-fetched objects) and nothing from the data layer (it returns
# a neutral intermediate, not the data layer's InsiderTxn), so neither layer takes
# a dependency on the other.

# Open-market transaction codes we score: 'P' = purchase (buy), 'S' = sale.
_BUY, _SELL = "P", "S"


def classify_code(code: str) -> str:
    """Map a Form 4 transaction code to 'buy' | 'sell' | 'other'.
    'P' = open-market purchase, 'S' = open-market sale; others are non-signal."""
    c = (code or "").strip().upper()
    if c == "P":
        return "buy"
    if c == "S":
        return "sell"
    return "other"


@dataclass
class Txn:
    """One open-market insider transaction (neutral intermediate)."""
    date: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    kind: Optional[str] = None       # "buy" | "sell"
    shares: Optional[float] = None
    price: Optional[float] = None
    value: Optional[float] = None    # unsigned transaction value (shares * price)


@dataclass
class Form4Summary:
    """Net insider flow over a set of Form 4 filings. `net_value` is signed
    (buys positive, sells negative); `found` is True iff any P/S trade was seen."""
    net_value: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    txns: list[Txn] = field(default_factory=list)
    found: bool = False


def summarize(rows: Iterable[tuple]) -> Form4Summary:
    """Aggregate open-market (P/S) transactions. The scored core — pure, no
    pandas — so it's unit-testable in isolation. Each row is
    (shares, price, code, date, name, role); non-P/S codes are ignored."""
    s = Form4Summary()
    for shares, price, code, dt, name, role in rows:
        classification = classify_code(str(code or ""))
        if classification == "other":
            continue
        shares = shares or 0
        price = price or 0
        value = shares * price
        is_buy = classification == "buy"
        s.found = True
        s.net_value += value if is_buy else -value
        s.buy_count += is_buy
        s.sell_count += not is_buy
        s.txns.append(Txn(
            date=str(dt) if dt is not None else None,
            name=name, role=role,
            kind="buy" if is_buy else "sell",
            shares=shares or None, price=price or None, value=value,
        ))
    return s


def _frame_rows(market_trades: Any, name: Optional[str], role: Optional[str]) -> list[tuple]:
    """Pull (shares, price, code, date, name, role) tuples from an edgartools
    `market_trades` pandas DataFrame (cols: Shares, Price, Code, Date, ...).
    Returns [] for None or an empty frame — guarding the DataFrame truth-value
    trap (`if df:` raises 'truth value is ambiguous')."""
    if market_trades is None or getattr(market_trades, "empty", True):
        return []
    rows = []
    for r in market_trades.itertuples(index=False):
        rows.append((
            getattr(r, "Shares", None),
            getattr(r, "Price", None),
            getattr(r, "Code", None),
            getattr(r, "Date", None),
            name, role,
        ))
    return rows


def aggregate_form4(filings: Iterable[Any], cutoff: date) -> Form4Summary:
    """Aggregate Form 4 filings (newest-first) until one predates `cutoff`.
    `filings` are edgartools EntityFiling objects; a filing that fails to parse
    is skipped rather than aborting the run."""
    rows: list[tuple] = []
    for filing in filings:
        if filing.filing_date < cutoff:
            break
        try:
            form4 = filing.obj()
        except Exception:
            continue
        rows.extend(_frame_rows(
            getattr(form4, "market_trades", None),
            getattr(form4, "insider_name", None),
            None,
        ))
    return summarize(rows)
