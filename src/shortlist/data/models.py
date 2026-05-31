from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Sections -------------------------------------------------------------

@dataclass
class Profile:
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    beta: Optional[float] = None
    description: Optional[str] = None


@dataclass
class Fundamentals:
    """TTM ratios + the quality/moat inputs an assessment needs."""
    pe_ttm: Optional[float] = None
    peg: Optional[float] = None
    roe: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    interest_coverage: Optional[float] = None
    current_ratio: Optional[float] = None
    fcf_yield: Optional[float] = None


@dataclass
class Statements:
    """Up to 5 fiscal years, most-recent-first, for trend/stability signals."""
    fiscal_years: list[int] = field(default_factory=list)
    revenue: list[float] = field(default_factory=list)
    gross_profit: list[float] = field(default_factory=list)
    net_income: list[float] = field(default_factory=list)
    operating_cash_flow: list[float] = field(default_factory=list)
    free_cash_flow: list[float] = field(default_factory=list)
    total_debt: list[float] = field(default_factory=list)
    total_equity: list[float] = field(default_factory=list)

    def gross_margins(self) -> list[float]:
        return [g / r for g, r in zip(self.gross_profit, self.revenue) if r]

    def revenue_cagr(self) -> Optional[float]:
        if len(self.revenue) >= 2 and self.revenue[-1] > 0:
            n = len(self.revenue) - 1
            return (self.revenue[0] / self.revenue[-1]) ** (1 / n) - 1.0
        return None


@dataclass
class Analyst:
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    target_median: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    consensus: Optional[str] = None


@dataclass
class InsiderTxn:
    date: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    kind: Optional[str] = None  # "buy" | "sell"
    shares: Optional[float] = None
    price: Optional[float] = None
    value: Optional[float] = None


@dataclass
class Insider:
    net_value_6m: Optional[float] = None       # buys positive, sells negative
    buy_count: Optional[int] = None
    sell_count: Optional[int] = None
    sentiment_mspr: Optional[float] = None      # -1..1 net signal
    recent: list[InsiderTxn] = field(default_factory=list)


@dataclass
class Price:
    price: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    ret_12m: Optional[float] = None
    rel_strength_6m: Optional[float] = None     # 6m return minus benchmark 6m return

    def price_vs_200dma(self) -> Optional[float]:
        if self.price and self.ma200:
            return self.price / self.ma200 - 1.0
        return None


# --- Snapshot -------------------------------------------------------------

# Which top-level objects must be present for a snapshot to be "assessment-ready".
KEY_OBJECTS = ("profile", "fundamentals", "statements", "analyst", "insider", "price")


@dataclass
class TickerSnapshot:
    ticker: str
    as_of: str = field(default_factory=utcnow_iso)
    profile: Optional[Profile] = None
    fundamentals: Optional[Fundamentals] = None
    statements: Optional[Statements] = None
    analyst: Optional[Analyst] = None
    insider: Optional[Insider] = None
    price: Optional[Price] = None

    raw: dict[str, dict[str, Any]] = field(default_factory=dict)        # source -> section -> payload
    provenance: dict[str, list[str]] = field(default_factory=dict)     # object -> [sources]
    errors: list[str] = field(default_factory=list)

    def coverage(self) -> float:
        """Fraction of populated *fields* across the key objects (0..1).
        This is the harness's honest answer to 'do we have what we need?'."""
        total = filled = 0
        for name in KEY_OBJECTS:
            obj = getattr(self, name)
            if obj is None:
                # Count the object's declared fields as all-missing.
                total += len(fields(_DEFAULTS[name]))
                continue
            for f in fields(obj):
                if f.name in ("recent",):
                    continue
                total += 1
                filled += getattr(obj, f.name) not in (None, [], "")
        return round(filled / total, 3) if total else 0.0

    def missing(self) -> list[str]:
        out = []
        for name in KEY_OBJECTS:
            obj = getattr(self, name)
            if obj is None:
                out.append(name)
                continue
            for f in fields(obj):
                if f.name in ("recent",):
                    continue
                if getattr(obj, f.name) in (None, [], ""):
                    out.append(f"{name}.{f.name}")
        return out

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


_DEFAULTS = {
    "profile": Profile, "fundamentals": Fundamentals, "statements": Statements,
    "analyst": Analyst, "insider": Insider, "price": Price,
}


# --- Source output + merge ------------------------------------------------

@dataclass
class SourceResult:
    source: str
    raw: dict[str, Any] = field(default_factory=dict)        # section -> raw payload
    partial: Optional[TickerSnapshot] = None
    errors: list[str] = field(default_factory=list)


T = TypeVar("T")


def _merge_flat(instances: list[tuple[str, T]]) -> tuple[Optional[T], list[str]]:
    """Field-level merge of flat dataclasses in priority order: each field takes
    the first source that has a non-None value. Returns (merged, contributing_sources)."""
    present = [(s, o) for s, o in instances if o is not None]
    if not present:
        return None, []
    merged = dataclasses.replace(present[0][1])
    contributors: list[str] = []
    for f in fields(merged):
        for src, obj in present:
            v = getattr(obj, f.name)
            if v not in (None, [], ""):
                setattr(merged, f.name, v)
                if src not in contributors:
                    contributors.append(src)
                break
    return merged, contributors


def _pick_first(instances: list[tuple[str, T]]) -> tuple[Optional[T], list[str]]:
    for src, obj in instances:
        if obj is not None and _has_data(obj):
            return obj, [src]
    return None, []


def _has_data(obj: Any) -> bool:
    return any(getattr(obj, f.name) not in (None, [], "") for f in fields(obj))


# The transaction facts in Insider are one accounting derived from a single set
# of Form 4 trades; they must come from ONE source or the dollar figure and the
# counts could describe different trades. `sentiment_mspr` is an independent
# signal (Finnhub's MSPR) that no transaction source supplies.
_INSIDER_TXN_FIELDS = ("net_value_6m", "buy_count", "sell_count", "recent")


def _merge_insider(instances: list[tuple[str, Optional["Insider"]]]) -> tuple[Optional["Insider"], list[str]]:
    """Take the coupled transaction group wholesale from the highest-priority
    source that actually has trades (keeping net/counts/recent coherent), then
    fill `sentiment_mspr` independently from the highest-priority source that has
    it. This composes EDGAR's authoritative flow with Finnhub's sentiment without
    ever mixing two sources' transaction sets into one incoherent aggregate."""
    present = [(s, o) for s, o in instances if o is not None]
    if not present:
        return None, []
    merged = Insider()
    contributors: list[str] = []
    for src, obj in present:
        if any(getattr(obj, f) not in (None, [], "") for f in _INSIDER_TXN_FIELDS):
            for f in _INSIDER_TXN_FIELDS:
                setattr(merged, f, getattr(obj, f))
            contributors.append(src)
            break
    for src, obj in present:
        if obj.sentiment_mspr is not None:
            merged.sentiment_mspr = obj.sentiment_mspr
            if src not in contributors:
                contributors.append(src)
            break
    if not contributors:
        return None, []
    return merged, contributors


# Flat objects merge field-by-field; `insider` has a bespoke merger (above);
# remaining list-bearing objects (statements) take the best whole source.
_FLAT = {"profile", "fundamentals", "analyst", "price"}


def merge_snapshots(ticker: str, results: list[SourceResult], priority: list[str]) -> TickerSnapshot:
    rank = {s: i for i, s in enumerate(priority)}
    ordered = sorted(results, key=lambda r: rank.get(r.source, len(priority)))

    snap = TickerSnapshot(ticker=ticker)
    for name in KEY_OBJECTS:
        instances = [(r.source, getattr(r.partial, name)) for r in ordered if r.partial]
        if name == "insider":
            merger = _merge_insider
        elif name in _FLAT:
            merger = _merge_flat
        else:
            merger = _pick_first
        merged, contributors = merger(instances)
        if merged is not None:
            setattr(snap, name, merged)
            snap.provenance[name] = contributors

    for r in ordered:
        if r.raw:
            snap.raw[r.source] = r.raw
        snap.errors.extend(r.errors)
    return snap
