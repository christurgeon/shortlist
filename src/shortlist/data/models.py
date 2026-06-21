from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_present(v: Any) -> bool:
    """Whether a field value counts as supplied by a source. The single definition
    of the merge/coverage 'present-ness' convention: None, the empty list, and the
    empty string all read as absent (so a lower-priority source can fill the field).
    Reused by merge (`_merge_flat`/`_merge_insider`/`_has_data`) and coverage()/
    missing() so the rule lives in exactly one place."""
    return v not in (None, [], "")


# --- Sections -------------------------------------------------------------

@dataclass
class Profile:
    """Company identity + classification; market_cap in absolute dollars, beta unitless."""
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    sic: Optional[str] = None
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
    pe_median_5y: Optional[float] = None   # 5y median PE, the pe_vs_history() anchor
    peg: Optional[float] = None
    roe: Optional[float] = None
    roic: Optional[float] = None
    roic_5y_avg: Optional[float] = None    # 5y mean ROIC, smooths a TTM spike for moat
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
    diluted_eps: list[float] = field(default_factory=list)
    diluted_shares: list[float] = field(default_factory=list)   # weighted-avg, newest-first
    fiscal_period_end: list[str] = field(default_factory=list)  # ISO dates, newest-first
    # Leverage / coverage (ASSESSMENT_GAPS §2.7), absolute USD, newest-first.
    operating_income: list[float] = field(default_factory=list)
    dep_amort: list[float] = field(default_factory=list)
    interest_expense: list[float] = field(default_factory=list)
    ebitda: list[float] = field(default_factory=list)   # operating_income + D&A, date-aligned
    cash_and_equivalents: list[float] = field(default_factory=list)
    # Investment & earnings-quality fundamentals (PREDICTIVE_SIGNALS §3). total_assets
    # is plumbing; asset_growth/accruals are pre-computed scalars (the source aligns
    # NI/CFO/Assets by their own statement dates — the bridge can't, so it copies).
    total_assets: list[float] = field(default_factory=list)
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None

    def gross_margins(self) -> list[float]:
        return [g / r for g, r in zip(self.gross_profit, self.revenue, strict=False) if r]


@dataclass
class Analyst:
    """Sell-side recommendation counts + price targets (targets in dollars)."""
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    target_median: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    consensus: Optional[str] = None


@dataclass
class InsiderTxn:
    """One open-market insider trade; price/value in dollars, value = shares * price."""
    date: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    kind: Optional[str] = None  # "buy" | "sell"
    shares: Optional[float] = None
    price: Optional[float] = None
    value: Optional[float] = None


@dataclass
class Insider:
    """Aggregated Form 4 insider flow; dollar values signed (buys +, sells -), mspr in -1..1."""
    net_value_6m: Optional[float] = None       # buys positive, sells negative
    buy_count: Optional[int] = None
    sell_count: Optional[int] = None
    sentiment_mspr: Optional[float] = None      # -1..1 net signal
    recent: list[InsiderTxn] = field(default_factory=list)
    # Conviction enrichment — default None (NOT 0) so an empty Insider() does not trip
    # the _merge_insider wholesale branch. Derived from the SAME Form-4 txn set as the
    # counts above -> travel wholesale with them.
    distinct_buyers: Optional[int] = None
    role_weighted_buy_value: Optional[float] = None
    planned_sell_value: Optional[float] = None


@dataclass
class Price:
    """OHLCV-derived price/momentum/risk; returns as fractions, max_drawdown is negative."""
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
    realized_vol: Optional[float] = None        # annualized stdev of daily returns
    max_drawdown: Optional[float] = None        # trailing ~1y peak-to-trough, negative
    # ~monthly-sampled (date, close) pairs over the fetch window, oldest->newest.
    # Lets the bridge align EDGAR fiscal-year-end dates to a historical price.
    monthly_closes: list[list] = field(default_factory=list)

    def price_vs_200dma(self) -> Optional[float]:
        if self.price and self.ma200:
            return self.price / self.ma200 - 1.0
        return None


@dataclass
class ShortInterest:
    """FINRA consolidated short interest for one symbol, as-of a settlement cycle.
    Raw facts only; short_pct_outstanding is DERIVED in the bridge (needs market cap)."""
    settlement_date: Optional[str] = None        # ISO; the cycle this data is AS-OF (point-in-time)
    short_shares: Optional[float] = None          # currentShortPositionQuantity
    prev_short_shares: Optional[float] = None     # previousShortPositionQuantity (prior cycle)
    avg_daily_volume: Optional[float] = None      # averageDailyVolumeQuantity
    days_to_cover: Optional[float] = None          # daysToCoverQuantity — FINRA-supplied, NOT recomputed
    split_flag: Optional[bool] = None    # stockSplitFlag — counts not comparable across a split
    revised: Optional[bool] = None       # revisionFlag — figure revised after publication


@dataclass
class SocialSentiment:
    """WSB social-media mention data for one symbol, as-of a fetch date (ApeWisdom).
    Raw facts only; rising/delta/staleness are DERIVED in the bridge."""
    as_of: Optional[str] = None              # ISO date the mention data was fetched
    mentions: Optional[int] = None
    mentions_24h_ago: Optional[int] = None
    upvotes: Optional[int] = None
    rank: Optional[int] = None
    rank_24h_ago: Optional[int] = None


@dataclass
class GovContracts:
    """USAspending federal procurement-contract obligations for one recipient,
    window-scoped. Raw facts only — rates/ratios are DERIVED in the bridge.
    Auxiliary (NOT a KEY_OBJECT): sparse, never moves coverage."""
    as_of: Optional[str] = None             # query date "YYYY-MM-DD"
    latest_action: Optional[str] = None         # newest captured Action Date (staleness)
    ttm_obligated: Optional[float] = None       # NET USD obligated, 0-12m (incl. de-obligations)
    prior_ttm_obligated: Optional[float] = None # NET USD obligated, 12-24m
    award_count_ttm: Optional[int] = None       # captured txn count, 0-12m
    matched_recipient: Optional[str] = None     # audit: PRIMARY recipient (largest single action)
    match_confidence: Optional[float] = None    # audit: primary recipient's match 0-1
    recipient_count: Optional[int] = None       # distinct matched recipients in the sum
    truncated: bool = False                     # paging hit the cap -> sum is a partial/approx
    total_txns: Optional[int] = None            # from _count endpoint (pre-match search breadth)


@dataclass
class Lobbying:
    """Senate LDA federal lobbying-disclosure spend for one client, window-scoped.
    Raw facts only — YoY/staleness are DERIVED in the bridge. Auxiliary (NOT a
    KEY_OBJECT): sparse, never moves coverage."""
    as_of: Optional[str] = None             # query date "YYYY-MM-DD"
    latest_filing: Optional[str] = None         # newest dt_posted date (staleness)
    ttm_spend: Optional[float] = None           # USD, 0-12m (income-or-expenses summed)
    prior_ttm_spend: Optional[float] = None     # USD, 12-24m
    filing_count_ttm: Optional[int] = None      # captured filings, 0-12m
    matched_client: Optional[str] = None        # audit: best-matched client name
    match_confidence: Optional[float] = None    # audit: 0-1
    registrant_count: Optional[int] = None      # distinct registrants in the TTM sum
    truncated: bool = False                     # paging hit the cap -> sum is partial
    total_filings: Optional[int] = None         # pre-match count across queried years
@dataclass
class NewsFlow:
    """Finnhub company-news volume for one symbol, window-scoped. Raw counts only —
    rising/staleness are DERIVED in the bridge. Auxiliary (NOT a KEY_OBJECT):
    sparse attention signal, never moves coverage."""
    as_of: Optional[str] = None         # query date "YYYY-MM-DD"
    count_recent: Optional[int] = None     # articles in the last 7d (lower bound if truncated)
    count_prior: Optional[int] = None      # articles in days 7-14 (None when truncated -> unreliable)
    count_window: Optional[int] = None     # articles in the 30d lookback (lower bound if truncated)
    latest_dt: Optional[str] = None        # newest article date (staleness)
    truncated: bool = False                # free-tier ~250-article cap hit -> prior/rising unreliable
@dataclass
class Earnings:
    """Finnhub earnings-surprise history + next-report date for one symbol. Raw facts
    only — beat_rate/avg/days-to-next are DERIVED in the bridge. Auxiliary (NOT a
    KEY_OBJECT): sparse, never moves coverage."""
    as_of: Optional[str] = None
    recent_surprise_pcts: list = field(default_factory=list)  # newest-first surprisePercent (None skipped)
    quarters: Optional[int] = None        # # quarters with a usable surprise
    beats: Optional[int] = None           # # of those with surprise > 0
    last_surprise_pct: Optional[float] = None  # newest quarter's surprise %
    next_date: Optional[str] = None       # next earnings date (ISO) or None


@dataclass
class FilingEvent:
    form: str                          # "8-K", "SC 13D", "SC 13G", "144", ...
    filed: str                         # ISO date (filing date)
    accession: Optional[str] = None
    url: Optional[str] = None          # public SEC index URL — carries no key


@dataclass
class Events:
    """Recent SEC filing-stream events (enrichment, not a scored section)."""
    recent: list[FilingEvent] = field(default_factory=list)  # in-window, newest-first
    recent_8k: bool = False
    activist_13d: bool = False         # SC 13D / SCHEDULE 13D (and /A) in window
    passive_13g: bool = False          # SC 13G / SCHEDULE 13G (and /A) in window
    planned_insider_sale_144: bool = False  # Form 144 (and /A) in window


# --- Snapshot -------------------------------------------------------------

# Which top-level objects must be present for a snapshot to be "assessment-ready".
KEY_OBJECTS = ("profile", "fundamentals", "statements", "analyst", "insider", "price")

# `recent` is illustrative; the rest are internal derivation plumbing, not
# assessment-ready signals -> excluded from coverage/missing accounting.
_NON_SIGNAL_FIELDS = ("recent", "diluted_eps", "diluted_shares", "fiscal_period_end", "monthly_closes",
                      "distinct_buyers", "role_weighted_buy_value", "planned_sell_value",
                      # Leverage derivation plumbing (feed net_debt_to_ebitda / coverage,
                      # not assessment-ready signals themselves) — §2.7.
                      "operating_income", "dep_amort", "interest_expense", "ebitda",
                      "cash_and_equivalents",
                      # Asset-growth / accruals plumbing + pre-computed scalars (§3) —
                      # surfaced via StockMetrics, not coverage-accounted here.
                      "total_assets", "asset_growth", "accruals")


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
    short_interest: Optional["ShortInterest"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    events: Optional[Events] = None    # auxiliary — NOT a KEY_OBJECT (see _AUX_DEFAULTS)
    social: Optional["SocialSentiment"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    gov_contracts: Optional["GovContracts"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    lobbying: Optional["Lobbying"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    news: Optional["NewsFlow"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    earnings: Optional["Earnings"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)

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
                # Count only signal fields as all-missing (consistent with the present
                # branch below) so non-signal plumbing fields never move coverage.
                total += len(_signal_fields(_DEFAULTS[name]))
                continue
            for f in _signal_fields(obj):
                total += 1
                filled += _is_present(getattr(obj, f.name))
        return round(filled / total, 3) if total else 0.0

    def missing(self) -> list[str]:
        out = []
        for name in KEY_OBJECTS:
            obj = getattr(self, name)
            if obj is None:
                out.append(name)
                continue
            for f in _signal_fields(obj):
                if not _is_present(getattr(obj, f.name)):
                    out.append(f"{name}.{f.name}")
        return out

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TickerSnapshot":
        """Inverse of to_dict: rebuild a snapshot (and its nested sections) from a
        persisted JSON dict. Unknown keys are ignored so the model can evolve."""
        def _build(klass, payload):
            if payload is None:
                return None
            names = {f.name for f in fields(klass)}
            return klass(**{k: v for k, v in payload.items() if k in names})

        snap = cls(ticker=d.get("ticker", "?"))
        snap.as_of = d.get("as_of", snap.as_of)
        for name, klass in _DEFAULTS.items():
            snap.__dict__[name] = _build(klass, d.get(name))
        for name, klass in _AUX_DEFAULTS.items():
            snap.__dict__[name] = _build(klass, d.get(name))
        ins = d.get("insider")
        if snap.insider is not None and ins and ins.get("recent"):
            snap.insider.recent = [_build(InsiderTxn, t) for t in ins["recent"]]
        ev = d.get("events")
        if snap.events is not None and ev and ev.get("recent"):
            snap.events.recent = [_build(FilingEvent, e) for e in ev["recent"]]
        snap.raw = d.get("raw", {}) or {}
        snap.provenance = d.get("provenance", {}) or {}
        snap.errors = d.get("errors", []) or []
        return snap


_DEFAULTS = {
    "profile": Profile, "fundamentals": Fundamentals, "statements": Statements,
    "analyst": Analyst, "insider": Insider, "price": Price,
}


# Auxiliary sections live on the snapshot and are merged, but are DELIBERATELY excluded
# from KEY_OBJECTS so they never move coverage()/missing() (sparse signals, not
# assessment-ready fundamentals). from_dict round-trips them via this map.
_AUX_DEFAULTS = {"short_interest": ShortInterest, "events": Events,
                 "social": SocialSentiment, "gov_contracts": GovContracts,
                 "lobbying": Lobbying, "news": NewsFlow, "earnings": Earnings}


def _signal_fields(obj_or_cls: Any) -> list:
    """Declared dataclass fields minus the non-signal plumbing ones."""
    return [f for f in fields(obj_or_cls) if f.name not in _NON_SIGNAL_FIELDS]


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
            if _is_present(v):
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
    return any(_is_present(getattr(obj, f.name)) for f in fields(obj))


# The transaction facts in Insider are one accounting derived from a single set
# of Form 4 trades; they must come from ONE source or the dollar figure and the
# counts could describe different trades. `sentiment_mspr` is an independent
# signal (Finnhub's MSPR) that no transaction source supplies.
_INSIDER_TXN_FIELDS = ("net_value_6m", "buy_count", "sell_count", "recent",
                       "distinct_buyers", "role_weighted_buy_value", "planned_sell_value")


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
        if any(_is_present(getattr(obj, f)) for f in _INSIDER_TXN_FIELDS):
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

    # Auxiliary (non-coverage) sections: pick-first from the highest-priority source with data.
    for name in _AUX_DEFAULTS:
        instances = [(r.source, getattr(r.partial, name, None)) for r in ordered if r.partial]
        merged, contributors = _pick_first(instances)
        if merged is not None:
            setattr(snap, name, merged)
            snap.provenance[name] = contributors

    for r in ordered:
        if r.raw:
            snap.raw[r.source] = r.raw
        snap.errors.extend(r.errors)
    return snap
