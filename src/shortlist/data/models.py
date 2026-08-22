from __future__ import annotations

import dataclasses
import warnings
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
    """Up to 5 fiscal years, most-recent-first, for trend/stability signals.

    A MERGED instance (`_merge_statements`) may carry `None` holes in a
    backfilled series where a lower-priority donor had no row for one of the
    spine's fiscal years (year-joined, not truncated) — every consumer
    (`piotroski_f`, `bridge._financial_series`, `cagr`, `[0]`-as-latest) is
    already `None`-tolerant, so this is part of the class contract, not a bug."""
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
    # Inventory BALANCE, newest-first. Research context only (the /deep inventory
    # line); never scored, never flagged. Excluded from coverage below.
    inventory: list[float] = field(default_factory=list)
    # Investment & earnings-quality fundamentals (PREDICTIVE_SIGNALS §3). total_assets
    # is plumbing; asset_growth/accruals are pre-computed scalars (the source aligns
    # NI/CFO/Assets by their own statement dates — the bridge can't, so it copies).
    total_assets: list[float] = field(default_factory=list)
    asset_growth: Optional[float] = None
    accruals: Optional[float] = None
    # Total shareholder yield financing legs (PREDICTIVE_SIGNALS §5). Latest-FY dollar
    # magnitudes; the bridge divides by market_cap to derive shareholder_yield (it can't
    # be pre-computed at extraction — market_cap is a price-merge product). None where
    # the source didn't supply financing rows (e.g. FMP-won statements).
    dividends_paid: Optional[float] = None
    repurchases: Optional[float] = None
    debt_repayments: Optional[float] = None
    debt_issuance: Optional[float] = None

    def gross_margins(self) -> list[float]:
        # Guard BOTH sides: FMP stores gross_profit verbatim, so a year can carry
        # truthy revenue with a None gross profit — `if r` alone would TypeError.
        return [g / r for g, r in zip(self.gross_profit, self.revenue, strict=False)
                if r and g is not None]


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
    # Rating REVISION over the recommendation window. Stored as CHANGES, never as
    # prior levels: this object merges field-by-field with `fmp` ahead of `finnhub`,
    # so a prior level here would be differenced against a level from a different
    # vendor's analyst panel. Deltas are computed inside one source's payload.
    rating_months: Optional[int] = None   # span of the window; None => no drift known
    buy_delta: Optional[int] = None
    hold_delta: Optional[int] = None
    sell_delta: Optional[int] = None


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
    # The four ret_* horizons feed NO SCORING LEG — set by FMP (sources/fmp.py) and mock,
    # plus Yahoo for ret_6m only; the bridge, scorer and every backtest source ignore them
    # (`momentum_score` is `price_vs_200dma` + `rel_strength_6m`). Do not wire a leg to
    # them assuming they are populated everywhere; only `rel_strength_6m` is load-bearing.
    #
    # They are NOT inert, though, and deleting them is NOT cosmetic: they are absent from
    # _NON_SIGNAL_FIELDS, so `coverage()`/`missing()` count all four in the Price
    # denominator (4 of 13). Removing them would shift every snapshot's coverage ratio and
    # therefore the accumulate.py GATED / THIN / CAPTURED classification — measured
    # 2026-08-04 against the 1,432-snapshot store: +0.016 mean coverage, flipping
    # THIN/CAPTURED for 233 snapshots (16%). `ret_6m` is populated in ~700 of them, so it
    # is a filled numerator entry, not denominator padding.
    #
    # That, and NOT store back-compat, is why they stay: `from_dict` drops unknown keys by
    # design, so old persisted snapshots would read back fine. If you want them gone, move
    # them to _NON_SIGNAL_FIELDS deliberately and re-baseline coverage — never silently delete.
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    ret_12m: Optional[float] = None
    rel_strength_6m: Optional[float] = None     # 6m return minus benchmark 6m return
    realized_vol: Optional[float] = None        # annualized stdev of daily returns
    max_drawdown: Optional[float] = None        # trailing ~1y peak-to-trough, negative
    # Residual (idiosyncratic) momentum: 12-1 momentum of CAPM (vs SPY) residuals,
    # standardized by residual vol (Blitz-Huij-Martens 2011, PREDICTIVE_SIGNALS §2).
    # Needs the DATE-ALIGNED stock + SPY series, so it is set only on the dated seam
    # (snapshot_from_closes_dated), None on the scalar live-merge path that lacks dates.
    residual_momentum: Optional[float] = None
    # PREDICTIVE_SIGNALS §2 price-refinement MEASUREMENT axes (backtest-only; no production
    # leg reads them). Pure closes functions, so unlike residual_momentum they populate on
    # EVERY path — hence excluded from coverage via _NON_SIGNAL_FIELDS below.
    pct_to_52w_high: Optional[float] = None       # closes[-1]/max(last 252) in (0,1] (George-Hwang)
    max_daily_return: Optional[float] = None      # largest daily return, last ~21d (Bali MAX; negative pred.)
    vol_scaled_momentum: Optional[float] = None   # mom_12_1 / 6m realized vol (Barroso-Santa-Clara)
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
    last_report_date: Optional[str] = None  # APPROX last-announcement date (ISO); see _earnings
    # True when last_report_date is the quarter-END proxy (not a real print date) — lets
    # the bridge refine the SUE decay anchor with the EDGAR 10-Q/10-K filed date. Defaults
    # True so legacy persisted snapshots (free-tier era: calendar never had past entries)
    # also get the refinement on replay.
    last_report_date_estimated: bool = True


@dataclass
class FilingEvent:
    form: str                          # "8-K", "SC 13D", "SC 13G", "144", ...
    filed: str                         # ISO date (filing date)
    accession: Optional[str] = None
    url: Optional[str] = None          # public SEC index URL — carries no key
    # Comma-separated 8-K item codes ("2.02,9.01") as edgartools' filings index
    # supplies them; None for forms that carry no items. Defaulted, so snapshots
    # persisted before this field load unchanged (from_dict filters to known names).
    items: Optional[str] = None


@dataclass
class Events:
    """Recent SEC filing-stream events (enrichment, not a scored section)."""
    recent: list[FilingEvent] = field(default_factory=list)  # in-window, newest-first
    recent_8k: bool = False
    activist_13d: bool = False         # SC 13D / SCHEDULE 13D (and /A) in window
    passive_13g: bool = False          # SC 13G / SCHEDULE 13G (and /A) in window
    planned_insider_sale_144: bool = False  # Form 144 (and /A) in window
    # Latest exact-form 10-Q/10-K filed date (ISO) — the bridge's SUE decay-anchor
    # refinement (a ~0-5d announcement proxy). NOT an advisory: kept out of `recent`
    # so the research filing-events line and the presence flags are untouched.
    last_report_filed: Optional[str] = None


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
                      "total_assets", "asset_growth", "accruals",
                      # Rating-revision deltas: DERIVED, and only Finnhub-with->=2-periods
                      # can supply them. `analyst` is a KEY_OBJECT, so leaving them in
                      # would dilute the coverage denominator of every snapshot ever
                      # taken and shift accumulate's GATED/THIN/CAPTURED split — the
                      # same trap the `inventory` and ret_* notes describe.
                      "rating_months", "buy_delta", "hold_delta", "sell_delta",
                      # Inventory is a /deep research context input only. MUST stay
                      # excluded: `statements` is in KEY_OBJECTS, so an un-excluded
                      # field moves the coverage DENOMINATOR for every snapshot ever
                      # taken (measured: mock GEV 0.855 -> 0.825), which shifts
                      # accumulate.py's THIN_MARK CAPTURED/THIN split -- the same
                      # class of break documented above at 16% of 1,432 snapshots.
                      "inventory",
                      # Shareholder-yield financing legs (§5) — plumbing the bridge
                      # divides by market_cap; surfaced via StockMetrics, not here.
                      "dividends_paid", "repurchases", "debt_repayments", "debt_issuance",
                      # PREDICTIVE_SIGNALS §2 price-refinement measurement axes — populated on
                      # every screen (pure closes fns), surfaced via StockMetrics, NOT coverage-
                      # accounted here (mirrors asset_growth/accruals).
                      "pct_to_52w_high", "max_daily_return", "vol_scaled_momentum")


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
            # A corrupt store file can carry a non-dict section payload (list/str);
            # treat it as absent rather than AttributeError-ing the whole load —
            # but LOUDLY, or a store-format regression silently thins every
            # snapshot the replay backtest reads.
            if not isinstance(payload, dict):
                if payload is not None:
                    warnings.warn(
                        f"snapshot {d.get('ticker', '?')}: section "
                        f"{klass.__name__} has non-dict payload "
                        f"({type(payload).__name__}); treating as absent",
                        stacklevel=2,
                    )
                return None
            names = {f.name for f in fields(klass)}
            return klass(**{k: v for k, v in payload.items() if k in names})

        snap = cls(ticker=d.get("ticker", "?"))
        snap.as_of = d.get("as_of", snap.as_of)
        for name, klass in _DEFAULTS.items():
            setattr(snap, name, _build(klass, d.get(name)))
        for name, klass in _AUX_DEFAULTS.items():
            setattr(snap, name, _build(klass, d.get(name)))
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


_DEFAULTS: dict[str, type] = {
    "profile": Profile, "fundamentals": Fundamentals, "statements": Statements,
    "analyst": Analyst, "insider": Insider, "price": Price,
}


# Auxiliary sections live on the snapshot and are merged, but are DELIBERATELY excluded
# from KEY_OBJECTS so they never move coverage()/missing() (sparse signals, not
# assessment-ready fundamentals). from_dict round-trips them via this map.
_AUX_DEFAULTS: dict[str, type] = {
    "short_interest": ShortInterest, "events": Events,
    "social": SocialSentiment, "gov_contracts": GovContracts,
    "lobbying": Lobbying, "news": NewsFlow, "earnings": Earnings,
}


def _signal_fields(obj_or_cls: Any) -> list[dataclasses.Field]:
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


# --- statements merge helpers --------------------------------------------
# `statements` is the one list-bearing section merged across sources. Every
# consumer aligns its parallel series by LIST POSITION (piotroski_f,
# bridge._financial_series, cagr, `[0]`-as-latest), so a backfill must join on
# the fiscal YEAR key or it silently pairs one source's 2022 revenue with
# another's 2023 share count.

def _newest_year(years: list[Optional[int]]) -> Optional[int]:
    """Newest real fiscal year in a spine, ignoring None holes. None if there
    are no usable years (never assumes newest-first ordering)."""
    real = [y for y in years if y is not None]
    return max(real) if real else None


def _usable_years(st: "Statements") -> Optional[list[Optional[int]]]:
    """A Statements' fiscal-year spine, or None when it cannot serve as a join
    key: empty (nothing to key on) or containing duplicates (ambiguous — a
    52/53-week fiscal can put two period ends in one calendar year).

    An ALL-`None` spine is deliberately NOT rejected here even though it is
    equally unusable as a key. It does not need to be: `_reindex_by_year` treats
    a None year as a non-key on both sides, so an all-None spine lands nothing
    and returns [], which `_is_present` reads as absent — the same observable
    outcome as returning None from here. The branch would be dead weight."""
    years = st.fiscal_years
    if not years:
        return None
    real = [y for y in years if y is not None]
    if len(set(real)) != len(real):
        return None
    return years


def _reindex_by_year(donor_years: list[Optional[int]],
                     donor_values: list, spine_years: list[Optional[int]]) -> list:
    """Re-index a donor series onto the spine's fiscal-year keys: the returned
    list is spine-length and spine-ordered, with None wherever the donor has no
    row for that year. A None year is NOT a key (an unparseable date on both
    sides must not join to itself). Returns [] when nothing lands, so
    `_is_present` still reads the field as absent rather than as a list of
    Nones. Ragged inputs are tolerated, never raised on."""
    by_year: dict[int, object] = {}
    for y, v in zip(donor_years, donor_values, strict=False):
        if y is not None:
            by_year[y] = v
    out = [by_year.get(y) if y is not None else None for y in spine_years]
    return out if any(v is not None for v in out) else []


# Pre-computed latest-fiscal-year scalars. The SOURCE aligns their inputs by its
# own statement dates (the bridge can't), so they carry no positional risk — but
# a latest-FY scalar attached to a NEWER spine would read as current in
# --json/CSV with nothing marking the vintage. Copied only on a newest-year
# match; abstain otherwise.
_STATEMENTS_LATEST_FY_SCALARS = (
    "asset_growth", "accruals", "dividends_paid", "repurchases",
    "debt_repayments", "debt_issuance",
)


def _merge_statements(
    instances: list[tuple[str, Optional["Statements"]]],
) -> tuple[Optional["Statements"], list[str]]:
    """Priority-ordered, fiscal-year-joined merge of the one list-bearing
    section. The highest-priority source with data wins the object outright and
    its `fiscal_years` becomes the join key — so the spine's own series (and
    every growth leg derived from them) are byte-identical to the old
    whole-source pick. Fields the spine left EMPTY are then backfilled from
    lower-priority sources, re-indexed onto that spine by YEAR, never by list
    position: every consumer of Statements reads its parallel series by index,
    so a positional backfill would pair one source's 2022 revenue with another's
    2023 share count with no test failing. Source-agnostic: it composes whatever
    `harness_sources` order is configured.

    Abstains (leaves a field empty) rather than guessing: a spine with no or
    duplicate fiscal years disables backfill entirely; an individual donor with
    the same problem is skipped without vetoing the donors after it."""
    present = [(s, o) for s, o in instances if o is not None and _has_data(o)]
    if not present:
        return None, []
    spine_src, spine = present[0]
    merged = dataclasses.replace(spine)      # copy: never alias SourceResult.partial
    # NOTE: dataclasses.replace() is a SHALLOW copy — any list attribute this loop
    # does not `setattr` stays the SAME object as the spine's (i.e. the source's
    # SourceResult.partial). Safe only under the repo-wide invariant that Statements
    # series are never mutated in place — always rebound wholesale (`setattr`/`=`),
    # never `.append()`/`[i] =` on an existing list.
    contributors = [spine_src]

    spine_years = _usable_years(spine)
    if spine_years is None:
        return merged, contributors          # no join key -> pre-change behaviour

    spine_newest = _newest_year(spine_years)
    list_fields = [f.name for f in fields(merged)
                   if f.name != "fiscal_years"
                   and isinstance(getattr(merged, f.name), list)]

    for src, donor in present[1:]:
        donor_years = _usable_years(donor)
        if donor_years is None:
            continue
        used = False
        for name in list_fields:
            if _is_present(getattr(merged, name)):
                continue                     # the spine already supplied it
            donor_vals = getattr(donor, name)
            if not _is_present(donor_vals):
                continue
            filled = _reindex_by_year(donor_years, donor_vals, spine_years)
            if filled:
                setattr(merged, name, filled)
                used = True
        if spine_newest is not None and _newest_year(donor_years) == spine_newest:
            for name in _STATEMENTS_LATEST_FY_SCALARS:
                if getattr(merged, name) is None and getattr(donor, name) is not None:
                    setattr(merged, name, getattr(donor, name))
                    used = True
        if used and src not in contributors:
            contributors.append(src)
    return merged, contributors


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


# Flat objects merge field-by-field; `insider` and `statements` have bespoke
# mergers (above); the aux sections take the best whole source.
_FLAT = {"profile", "fundamentals", "analyst", "price"}


def merge_snapshots(ticker: str, results: list[SourceResult], priority: list[str]) -> TickerSnapshot:
    rank = {s: i for i, s in enumerate(priority)}
    ordered = sorted(results, key=lambda r: rank.get(r.source, len(priority)))

    snap = TickerSnapshot(ticker=ticker)
    for name in KEY_OBJECTS:
        instances = [(r.source, getattr(r.partial, name)) for r in ordered if r.partial]
        if name == "insider":
            merger = _merge_insider
        elif name == "statements":
            merger = _merge_statements
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
