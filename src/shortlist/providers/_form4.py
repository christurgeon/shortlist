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


# Role classification: ordered substring match. c_suite (CEO/CFO) checked before the
# generic officer bucket; a person who is officer AND director carries their officer
# title (so resolves to officer). Inputs are the role strings _owner_role builds.
def classify_role(role: Optional[str]) -> str:
    r = (role or "").strip().lower()
    if not r:
        return "unknown"
    if ("cfo" in r or "chief financial" in r or "principal financial" in r
            or "ceo" in r or "chief executive" in r):
        return "c_suite"
    if "10%" in r or "ten percent" in r:
        return "ten_pct"
    if "director" in r:
        return "director"
    return "officer"


# 10b5-1 detection: footnote-text heuristic only (edgartools parses no structured
# checkbox). HIGH PRECISION, LOW RECALL — detected => almost certainly a planned trade;
# absence proves nothing. Reimplemented here to keep this leaf dependency-free.
_10B5_1_PATTERNS = ("10b5-1", "10b-5-1", "rule 10b5", "rule 10b-5", "10b5 plan", "10b-5 plan")


def is_10b5_1(footnotes_text: Optional[str]) -> bool:
    if not footnotes_text or not footnotes_text.strip():
        return False
    text = footnotes_text.lower()
    return any(p in text for p in _10B5_1_PATTERNS)


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
    planned: bool = False            # True iff a 10b5-1 footnote was detected on this trade (only sells feed planned_sell_value)


@dataclass
class Form4Summary:
    """Net insider flow over a set of Form 4 filings. `net_value` is signed
    (buys positive, sells negative); `found` is True iff any P/S trade was seen."""
    net_value: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    txns: list[Txn] = field(default_factory=list)
    found: bool = False
    # --- v2 conviction aggregates (no-signal defaults; computed only when a
    # conviction config is passed to summarize) ---
    distinct_buyers: int = 0
    role_weighted_buy_value: float = 0.0
    planned_sell_value: float = 0.0


def summarize(rows: Iterable[tuple], conviction: Optional[dict] = None) -> Form4Summary:
    """Aggregate open-market (P/S) transactions. Each row is
    (shares, price, code, date, name, role, planned); non-P/S codes are ignored.
    When `conviction` is None the v2 fields stay at their no-signal defaults and the
    result is identical to the pre-conviction scorer (back-compat). When a conviction
    dict (role_weights, min_cluster_buy_value) is supplied, three extra aggregates are
    computed in the same single pass."""
    s = Form4Summary()
    buyer_value: dict[str, float] = {}   # normalized name -> total in-window buy $
    buyer_tier: dict[str, str] = {}      # normalized name -> role tier (first seen)
    for shares, price, code, dt, name, role, planned in rows:
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
            planned=bool(planned),
        ))
        if conviction is not None:
            if is_buy:
                tier = classify_role(role)
                weights = conviction.get("role_weights") or {}
                s.role_weighted_buy_value += weights.get(tier, 1.0) * value
                if name:
                    key = " ".join(str(name).upper().split())
                    buyer_value[key] = buyer_value.get(key, 0.0) + value
                    buyer_tier.setdefault(key, tier)
            elif planned:
                s.planned_sell_value += value
    if conviction is not None:
        floor = conviction.get("min_cluster_buy_value", 0) or 0
        s.distinct_buyers = sum(
            1 for k, v in buyer_value.items()
            if buyer_tier.get(k) in ("c_suite", "officer", "director") and v >= floor
        )
    return s


def _frame_rows(market_trades: Any, name: Optional[str], role: Optional[str],
                resolve_footnotes: Any = None) -> list[tuple]:
    """Pull (shares, price, code, date, name, role, planned) tuples from an edgartools
    `market_trades` DataFrame (cols: Shares, Price, Code, Date, footnotes, ...).
    `planned` is the 10b5-1 footnote heuristic, resolved via `resolve_footnotes` (the
    duck-typed form4._resolve_footnotes; None in tests/absence -> planned False).
    Guards the DataFrame truth-value trap (`if df:` raises)."""
    if market_trades is None or getattr(market_trades, "empty", True):
        return []
    rows = []
    for r in market_trades.itertuples(index=False):
        planned = False
        if resolve_footnotes is not None:
            ids = getattr(r, "footnotes", None)
            if ids:
                try:
                    planned = is_10b5_1(resolve_footnotes(ids))
                except Exception:
                    planned = False
        rows.append((
            getattr(r, "Shares", None),
            getattr(r, "Price", None),
            getattr(r, "Code", None),
            getattr(r, "Date", None),
            name, role, planned,
        ))
    return rows


def _owner_role(owner: Any) -> Optional[str]:
    """Build a role string from an edgartools reporting Owner. officer_title wins;
    else fall back to the structured relationship booleans. Returns None when unknown."""
    if owner is None:
        return None
    title = getattr(owner, "officer_title", None)
    if title:
        return title
    if getattr(owner, "is_director", False):
        return "director"
    if getattr(owner, "is_ten_pct_owner", False):
        return "10% owner"
    if getattr(owner, "is_officer", False):
        return "officer"
    return None


def _form_role(form4: Any) -> Optional[str]:
    """Role of the filing's primary reporting owner (owners[0]); >1 owner is rare and
    co-owners are dropped. Fully guarded — any absence degrades to None."""
    owners = getattr(getattr(form4, "reporting_owners", None), "owners", None)
    if not owners:
        return None
    try:
        return _owner_role(owners[0])
    except Exception:
        return None


def aggregate_form4(filings: Iterable[Any], cutoff: date,
                    conviction: Optional[dict] = None) -> Form4Summary:
    """Aggregate Form 4 filings (newest-first) until one predates `cutoff`. A filing
    that fails to parse is skipped. When `conviction` is supplied, role + 10b5-1
    extraction feed the v2 aggregates; when None, behavior is identical to before."""
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
            _form_role(form4),
            getattr(form4, "_resolve_footnotes", None),
        ))
    return summarize(rows, conviction)
