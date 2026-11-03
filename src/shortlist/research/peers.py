"""Peer comparison for the research brief: the company against up to `max_peers` competitors
on one vendor basis (Finnhub `stock/peers` and `stock/metric`).

Vendor data, not filing data: never a grounding segment and never a score, gate or flag. With
`in_prompt` on, the table is fetched before `assess()` and `context_line` puts it in the prompt;
off, it is fetched after and the model never sees it. `PeerTable.in_prompt` records which.

Behaviour, the two derived lines and their rules: docs/RESEARCH.md → the peer-comparison
section.

NEVER RAISES (`fetch_section`). An outage reads "unavailable", never as an empty table.
"""
from __future__ import annotations

import math
import os
import re
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .models import PeerRow, PeerTable

BASE = "https://finnhub.io/api/v1"
HEADING = "Peer comparison — vendor data (Finnhub), not from filings; not seen by the model"
HEADING_IN_PROMPT = ("Peer comparison — vendor data (Finnhub), not from filings; given to the "
                     "model as context")
SHOWN, NOT_SHOWN, UNAVAILABLE = "shown", "not_shown", "unavailable"
SUB_INDUSTRY = "Finnhub sub-industry"
WIDER_TOO_FEW = "Finnhub sub-industry + industry (too few sub-industry peers)"
WIDER_SMALLER = "Finnhub sub-industry + industry (sub-industry peers were much smaller)"

NO_KEY_TEXT = "Not shown: no Finnhub API key is configured."
UNAVAILABLE_TEXT = ("Not shown: the vendor data was unavailable for this brief (an error, a rate "
                    "limit or the time limit). This says nothing about the company's peers.")
NO_TARGET_TEXT = "Not shown: the vendor has no metrics for this company."
NO_PEERS_TEXT = "Not shown: the vendor lists no peers for this company."
TOO_FEW_TEXT = ("Not shown: fewer than {n} of the vendor's listed peers have vendor metrics and "
                "a market cap of at least {floor}.")
RANK_NOTE = ("Rank counts {ticker} with the peers that report the measure. For the multiples, "
             "lower is cheaper on that measure, which is not a judgement of value; n/m is a "
             "multiple or ratio on negative earnings, EBITDA or equity, and is not ranked.")

_DEFAULTS: dict = {
    "enabled": False, "in_prompt": False, "max_candidates": 12, "max_calls": 20, "max_peers": 6, "min_peers": 3,
    "size_mismatch_ratio": 0.1, "bucket_capacity": 40, "refill_per_s": 0.9, "timeout": 30,
    "masks": {"default": ["pb", "roe"]},
}
# Shared with research/news.py and research/durability.py: the research phase's slack.
MAX_DEADLINE_S = 45.0
_REQUEST_TIMEOUT_S = 10.0
# The company, its peer list and one peer: below this a brief cannot show a table.
_MIN_CALLS = 3
_DEFAULT_FLOOR_USD = 3.0e8
# "About the same" band, in percentage points, for both derived lines.
_SMALL_GAP = 5.0
_SHARE_BAND = 1.0
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    field: str                 # Finnhub `metric` key
    kind: str                  # usd_m | multiple | pct | signed_pct | ratio
    low_first: bool = False    # rank from the low end ("lowest") rather than the high end
    # A negative value is n/m (negative earnings, EBITDA or equity), never cheap or safe.
    # Zero is n/m for a multiple and real for debt/equity (no debt).
    nonneg_only: bool = False


COLUMNS = (
    Column("market_cap", "Market cap", "marketCapitalization", "usd_m"),
    Column("pe", "P/E (TTM)", "peTTM", "multiple", True, True),
    Column("ev_ebitda", "EV/EBITDA (TTM)", "evEbitdaTTM", "multiple", True, True),
    Column("ps", "P/S (TTM)", "psTTM", "multiple", True, True),
    Column("pb", "P/B", "pb", "multiple", True, True),
    Column("gross_margin", "Gross margin (TTM)", "grossMarginTTM", "pct"),
    Column("operating_margin", "Operating margin (TTM)", "operatingMarginTTM", "pct"),
    Column("revenue_growth", "Revenue growth (TTM, y/y)", "revenueGrowthTTMYoy", "signed_pct"),
    Column("revenue_growth_3y", "Revenue growth (3y, per year)", "revenueGrowth3Y", "signed_pct"),
    Column("roi", "ROI (TTM)", "roiTTM", "pct"),
    Column("roe", "ROE (TTM)", "roeTTM", "pct"),
    Column("debt_to_equity", "Debt/equity (latest quarter)", "totalDebt/totalEquityQuarterly",
           "ratio", True, True),
    Column("return_52w", "52-week price return", "52WeekPriceReturnDaily", "signed_pct"),
)
_FIELDS = tuple(c.field for c in COLUMNS)
_BY_KEY = {c.key: c for c in COLUMNS}


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.peers` block; an absent block is off."""
    block = ((config or {}).get("research") or {}).get("peers")
    return {**_DEFAULTS, **(block if isinstance(block, dict) else {})}


def market_cap_floor_m(config: Optional[dict], cfg: dict) -> float:
    """The peer market-cap floor in $ millions (the vendor's unit): `research.peers.min_market_cap`,
    else `gates.min_market_cap`, both in dollars."""
    for raw in (cfg.get("min_market_cap"), ((config or {}).get("gates") or {}).get("min_market_cap")):
        v = _num(raw)
        if v is not None and v >= 0:
            return v / 1e6
    return _DEFAULT_FLOOR_USD / 1e6


def hidden_columns(cfg: dict, bucket: Optional[str]) -> set[str]:
    masks = cfg.get("masks") if isinstance(cfg.get("masks"), dict) else {}
    keys = masks.get(bucket) if bucket in masks else masks.get("default")
    return {str(k) for k in (keys or [])}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def peer_list(body: Any, ticker: str) -> list[str]:
    """Distinct, well-formed tickers from a `stock/peers` body, in vendor order, without the
    company itself. Raises ValueError when the body is not a list."""
    if not isinstance(body, list):
        raise ValueError("stock/peers body is not a list")
    out: list[str] = []
    for raw in body:
        t = str(raw).strip().upper() if isinstance(raw, str) else ""
        if _TICKER.match(t) and t != ticker.upper() and t not in out:
            out.append(t)
    return out


def metrics_from(body: Any, ticker: str) -> Optional[dict]:
    """The table's fields from a `stock/metric` body, or None when it has none for `ticker`."""
    if not isinstance(body, dict) or not isinstance(body.get("metric"), dict):
        return None
    sym = body.get("symbol")
    # Also the currency guard: a foreign listing answers under its home symbol in local
    # currency (TSM as 2330.TW), which would not compare with dollar figures.
    if isinstance(sym, str) and sym.upper() != ticker.upper():
        return None
    m = body["metric"]
    out = {f: _num(m.get(f)) for f in _FIELDS}
    return out if any(v is not None for v in out.values()) else None


def _same_company(a: dict, b: dict) -> bool:
    """Two share classes of one company: the vendor gives both the same total market cap and
    the same revenue growth."""
    keys = ("marketCapitalization", "revenueGrowthTTMYoy")
    return all(a.get(k) is not None and a.get(k) == b.get(k) for k in keys)


def _sort(candidates: list[tuple[str, Optional[dict]]], target: dict,
          floor_m: float) -> tuple[list[tuple[str, dict]], dict[str, int]]:
    """The usable candidates in vendor order, and how many were dropped for each reason."""
    out: list[tuple[str, dict]] = []
    drops = {"no_data": 0, "below": 0, "share_class": 0}
    for t, m in candidates:
        cap = (m or {}).get("marketCapitalization")
        if m is None or cap is None:
            drops["no_data"] += 1
        elif cap < floor_m:
            drops["below"] += 1
        elif _same_company(m, target) or any(_same_company(m, k) for _, k in out):
            drops["share_class"] += 1
        else:
            out.append((t, m))
    return out, drops


def usable(candidates: list[tuple[str, Optional[dict]]], target: dict,
           floor_m: float) -> list[tuple[str, dict]]:
    """Candidates with metrics and a market cap at or above the floor, that are not another
    share class of the company or of a peer already kept. Vendor order is kept."""
    return _sort(candidates, target, floor_m)[0]


def _dropped(drops: dict[str, int], floor_m: float) -> str:
    parts = [f"{drops['below']} below {fmt('usd_m', floor_m)} market cap" if drops["below"] else "",
             f"{drops['no_data']} without vendor metrics or market cap" if drops["no_data"] else "",
             f"{drops['share_class']} another share class of a kept name"
             if drops["share_class"] else ""]
    parts = [p for p in parts if p]
    return "dropped " + ", ".join(parts) if parts else "none dropped"


def closest_in_size(peers: list[tuple[str, dict]], target_cap_m: Optional[float],
                    max_peers: int) -> list[tuple[str, dict]]:
    """Up to `max_peers` peers nearest the company in log market cap (vendor order breaks ties,
    and is the order when the company's cap is unknown), largest first."""
    if target_cap_m and target_cap_m > 0:
        ranked = sorted(enumerate(peers), key=lambda ip: (
            abs(math.log(ip[1][1]["marketCapitalization"]) - math.log(target_cap_m))
            if ip[1][1]["marketCapitalization"] > 0 else math.inf, ip[0]))
        kept = [p for _, p in ranked[:max(0, max_peers)]]
    else:
        kept = peers[:max(0, max_peers)]
    return sorted(kept, key=lambda p: -p[1]["marketCapitalization"])


def fmt(kind: str, v: Optional[float]) -> str:
    if v is None:
        return "—"
    if kind == "usd_m":
        for scale, unit in ((1e6, "T"), (1e3, "B")):
            if abs(v) >= scale:
                return f"${v / scale:,.2f}{unit}" if unit == "T" else f"${v / scale:,.1f}{unit}"
        return f"${v:,.0f}M"
    if kind == "multiple":
        return f"{v:,.1f}x"
    if kind == "pct":
        return f"{v:.1f}%"
    if kind == "signed_pct":
        return f"{v:+.1f}%"
    return f"{v:.2f}"


def _ordinal(k: int) -> str:
    if k == 1:
        return ""
    suffix = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
    return f"{k}{suffix} "


def row(col: Column, company: Optional[float], peer_values: list[Optional[float]]) -> PeerRow:
    """One measure: the company's value, the median of the peers that report it, and the
    company's rank among those peers plus itself. Ties share the better rank."""
    def ok(v: Optional[float]) -> bool:
        if v is None or not col.nonneg_only:
            return v is not None
        return v > 0 if col.kind == "multiple" else v >= 0

    vals = [v for v in peer_values if ok(v)]
    med = statistics.median(vals) if vals else None
    if not ok(company):
        shown = "n/m" if company is not None else "—"
        return PeerRow(col.label, shown, fmt(col.kind, med), len(vals), "")
    if not vals:
        return PeerRow(col.label, fmt(col.kind, company), fmt(col.kind, med), 0, "")
    better = sum(1 for v in vals if (v < company if col.low_first else v > company))
    word = ("lowest" if col.low_first else "largest" if col.key == "market_cap" else "highest")
    return PeerRow(col.label, fmt(col.kind, company), fmt(col.kind, med), len(vals),
                   f"{_ordinal(better + 1)}{word} of {len(vals) + 1}")


def _pts(v: float) -> str:
    return f"{v:+.1f} points"


def relative_line(ticker: str, company: Optional[float], peer_returns: list[Optional[float]],
                  spy: Optional[float], min_peers: int) -> str:
    """The 52-week relative-performance sentence. Rule: split the company's move into its gap
    to the peer median and the peer median's own gap (to the S&P 500 when known, else to zero);
    the larger part names the move. Both under 5 points reads as small."""
    vals = [v for v in peer_returns if v is not None]
    if company is None:
        return "Not shown: the vendor has no 52-week return for this company."
    if len(vals) < min_peers:
        return f"Not shown: fewer than {min_peers} peers have a 52-week return."
    med = statistics.median(vals)
    own, shared = company - med, med - (spy if spy is not None else 0.0)
    head = (f"52-week price return: {ticker} {company:+.1f}%, peer median {med:+.1f}% "
            f"({len(vals)} peers)")
    if spy is not None:
        head += f", S&P 500 (SPY, price only) {spy:+.1f}%. "
        split = (f"{ticker} minus the peer median: {_pts(own)}; the peer median minus the "
                 f"S&P 500: {_pts(shared)}. ")
    else:
        head += "; no S&P 500 figure was available. "
        split = f"{ticker} minus the peer median: {_pts(own)}; the peer median's own move: {_pts(shared)}. "
    if max(abs(own), abs(shared)) < _SMALL_GAP:
        verdict = "Neither part is as large as 5 points."
    elif abs(own) >= abs(shared):
        verdict = "The larger part is the company's own, so the move is mostly company-specific."
    else:
        verdict = "The larger part is the peer set's, so the move is mostly shared with the peers."
    return head + split + verdict


def growth_line(ticker: str, company: Optional[float], peers: list[dict], min_peers: int) -> str:
    """Company TTM revenue growth against the peers' combined revenue growth, where revenue TTM
    = market cap / P/S (each in the vendor's units) and the prior year is revenue / (1 + g)."""
    if company is None:
        return "Not shown: the vendor has no TTM revenue growth for this company."
    now = before = 0.0
    n = 0
    for m in peers:
        cap, ps, g = m.get("marketCapitalization"), m.get("psTTM"), m.get("revenueGrowthTTMYoy")
        if cap is None or ps is None or g is None or cap <= 0 or ps <= 0 or g <= -100:
            continue
        rev = cap / ps
        now, before, n = now + rev, before + rev / (1 + g / 100), n + 1
    if n < min_peers or before <= 0:
        return f"Not shown: fewer than {min_peers} peers have revenue and revenue growth."
    combined = (now / before - 1) * 100
    gap = company - combined
    if abs(gap) < _SHARE_BAND:
        share = "about in line, so its share of this set's revenue was about unchanged"
    elif gap > 0:
        share = f"faster by {gap:.1f} points, so its share of this set's revenue rose"
    else:
        share = f"slower by {-gap:.1f} points, so its share of this set's revenue fell"
    return (f"TTM revenue growth: {ticker} {company:+.1f}% against {combined:+.1f}% for the "
            f"combined revenue of {n} peers (revenue = market cap ÷ P/S). Within this peer set "
            f"only: {ticker} grew {share}.")


def build(ticker: str, bucket: Optional[str], raw: dict, cfg: dict, floor_m: float,
          as_of: str) -> PeerTable:
    """The section from collected vendor data. Pure."""
    target = raw.get("target")
    if not target:
        return PeerTable(NOT_SHOWN, NO_TARGET_TEXT, as_of, ticker)
    candidates = raw.get("candidates") or []
    if not candidates:
        return PeerTable(NOT_SHOWN, NO_PEERS_TEXT, as_of, ticker)
    min_peers, max_peers = int(cfg["min_peers"]), int(cfg["max_peers"])
    ok, drops = _sort(candidates, target, floor_m)
    if len(ok) < min_peers:
        floor = fmt("usd_m", floor_m)
        return PeerTable(NOT_SHOWN, TOO_FEW_TEXT.format(n=min_peers, floor=floor), as_of, ticker)
    kept = closest_in_size(ok, target.get("marketCapitalization"), max_peers)
    hidden = hidden_columns(cfg, bucket)
    cols = [c for c in COLUMNS if c.key not in hidden]
    rows = [row(c, target.get(c.field), [m.get(c.field) for _, m in kept]) for c in cols]
    basis = (f"Peer source: {raw.get('grouping') or SUB_INDUSTRY}. Of {len(candidates)} "
             f"examined, {_dropped(drops, floor_m)}; the {len(kept)} closest to {ticker} in "
             f"market cap are kept.")
    relative = (relative_line(ticker, target.get("52WeekPriceReturnDaily"),
                              [m.get("52WeekPriceReturnDaily") for _, m in kept],
                              raw.get("spy"), min_peers)
                if "return_52w" not in hidden else "Not shown: masked for this sector.")
    growth = (growth_line(ticker, target.get("revenueGrowthTTMYoy"), [m for _, m in kept],
                          min_peers)
              if "revenue_growth" not in hidden else
              "Not shown: vendor revenue growth is not meaningful for this sector.")
    return PeerTable(
        status=SHOWN, as_of=as_of, ticker=ticker, basis=basis, peers=[t for t, _ in kept],
        rows=rows, masked=[c.label for c in COLUMNS if c.key in hidden and c.key not in
                           hidden_columns(cfg, None)],
        relative=relative, growth=growth,
        values={ticker: target, **dict(kept)})


class _RateLimited(RuntimeError):
    pass


class _Stopped(RuntimeError):
    pass


class _OverBudget(RuntimeError):
    pass


class _Bucket:
    """A token bucket shared by every brief in the process: several briefs run at once
    (research._MAX_ENRICH_WORKERS) against one free-tier key. Thread-safe; a waiter sleeps
    outside the lock and wakes early when its brief is stopped."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tokens: Optional[float] = None
        self.stamp = time.monotonic()

    def take(self, capacity: float, rate: float, stop: threading.Event) -> None:
        while True:
            if stop.is_set():
                raise _Stopped("over the deadline")
            with self.lock:
                now = time.monotonic()
                if self.tokens is None:
                    self.tokens = capacity
                self.tokens = min(capacity, self.tokens + (now - self.stamp) * rate)
                self.stamp = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / rate if rate > 0 else 1.0
            stop.wait(min(wait, 1.0))


_BUCKET = _Bucket()
_SPY_LOCK = threading.Lock()
_spy_cache: dict[str, float] = {}


class _Vendor:
    def __init__(self, client: Any, cfg: dict, stop: threading.Event):
        self.client, self.stop = client, stop
        self.capacity = max(float(cfg["bucket_capacity"]), 1.0)
        self.rate = max(float(cfg["refill_per_s"]), 0.0)
        self.max_calls = max(int(cfg["max_calls"]), _MIN_CALLS)
        self.calls = 0

    def left(self) -> int:
        return self.max_calls - self.calls

    def get(self, path: str, params: dict) -> Any:
        if self.left() <= 0:
            raise _OverBudget("the call cap is spent")
        _BUCKET.take(self.capacity, self.rate, self.stop)
        self.calls += 1
        r = self.client.get(BASE + path, params=params)
        if r.status_code == 429:
            raise _RateLimited("HTTP 429")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    def metrics(self, ticker: str) -> Optional[dict]:
        return metrics_from(self.get("/stock/metric", {"symbol": ticker, "metric": "all"}), ticker)


def _too_small(kept: list[tuple[str, dict]], target_cap_m: Optional[float], ratio: float) -> bool:
    """The kept peers' median market cap is below `ratio` of the company's."""
    if not kept or not target_cap_m or target_cap_m <= 0:
        return False
    return statistics.median(m["marketCapitalization"] for _, m in kept) < ratio * target_cap_m


def collect(vendor: _Vendor, ticker: str, cfg: dict, floor_m: float, today: str) -> dict:
    """The vendor data the table needs: the company, the sub-industry peer list and at most
    `max_candidates` of its peers, then the industry list and at most `max_candidates` more when
    the sub-industry leaves too few usable peers or peers much smaller than the company, then the
    S&P 500 reference (once a day per process). All calls together stay within `max_calls`.
    A failed peer call drops that peer. Any failure in the industry step keeps what the
    sub-industry gave; any other failure or a 429 raises."""
    target = vendor.metrics(ticker)
    if target is None:
        return {"target": None}
    per_list = max(0, int(cfg["max_candidates"]))
    candidates: list[tuple[str, Optional[dict]]] = []

    def examine(tickers: list[str]) -> None:
        added = 0
        for t in tickers:
            # One call is held back for the S&P 500 reference.
            if added >= per_list or vendor.left() <= 1:
                return
            if any(t == c for c, _ in candidates):
                continue
            added += 1
            try:
                candidates.append((t, vendor.metrics(t)))
            except (_RateLimited, _Stopped, _OverBudget):
                raise
            except Exception:       # noqa: BLE001 — a failed peer is dropped, not fatal
                candidates.append((t, None))

    examine(peer_list(vendor.get("/stock/peers", {"symbol": ticker}), ticker))
    ok = usable(candidates, target, floor_m)
    cap = target.get("marketCapitalization")
    wider = None
    if len(ok) < int(cfg["min_peers"]):
        wider = WIDER_TOO_FEW
    elif _too_small(closest_in_size(ok, cap, int(cfg["max_peers"])), cap,
                    float(cfg["size_mismatch_ratio"])):
        wider = WIDER_SMALLER
    grouping, limited = SUB_INDUSTRY, False
    # The list call, one peer and the S&P 500 reference.
    if wider and vendor.left() >= 3:
        before = len(candidates)
        try:
            examine(peer_list(vendor.get("/stock/peers", {"symbol": ticker,
                                                          "grouping": "industry"}), ticker))
        except _Stopped:
            raise
        except Exception as e:      # noqa: BLE001 — the sub-industry result stands
            limited = isinstance(e, _RateLimited)
        if len(candidates) > before:
            grouping = wider
    return {"target": target, "candidates": candidates, "grouping": grouping,
            "spy": _spy(vendor, today, skip=limited)}


def _spy(vendor: _Vendor, today: str, skip: bool = False) -> Optional[float]:
    """SPY's 52-week price return, the S&P 500 reference on the same vendor basis as the
    stocks. Cached per UTC day; `skip` (the key is rate limited) or a failure other than a 429
    leaves it out."""
    with _SPY_LOCK:
        if today in _spy_cache:
            return _spy_cache[today]
    if skip:
        return None
    try:
        m = vendor.metrics("SPY")
    except (_RateLimited, _Stopped):
        raise
    except Exception:       # noqa: BLE001 — the line then says no S&P figure was available
        return None
    v = (m or {}).get("52WeekPriceReturnDaily")
    if v is not None:
        with _SPY_LOCK:
            _spy_cache.clear()
            _spy_cache[today] = v
    return v


def fetch_section(card: Any, config: Optional[dict], *, now: Optional[datetime] = None,
                  transport: Any = None) -> PeerTable:
    """The section for the brief. Never raises. The calls run in a daemon thread and the caller
    waits `timeout` (at most 45 s) of wall clock; past it the thread makes no further call."""
    from .filings import log_abstain

    now = now or datetime.now(timezone.utc)
    as_of = now.strftime("%Y-%m-%d %H:%M UTC")
    ticker = str(getattr(card, "ticker", "") or "").upper()
    try:
        cfg = config_block(config)
        key = os.environ.get("FINNHUB_API_KEY")
        if not key:
            return PeerTable(NOT_SHOWN, NO_KEY_TEXT, as_of, ticker)
        floor_m = market_cap_floor_m(config, cfg)
        deadline_s = min(max(float(cfg["timeout"]), 0.1), MAX_DEADLINE_S)
    except Exception as e:      # noqa: BLE001 — never-raises contract
        log_abstain("peer section failed", ticker or "?", e)
        return PeerTable(UNAVAILABLE, UNAVAILABLE_TEXT, as_of, ticker)

    stop, box = threading.Event(), {}

    def work() -> None:
        import httpx
        try:
            with httpx.Client(timeout=min(_REQUEST_TIMEOUT_S, deadline_s), transport=transport,
                              headers={"X-Finnhub-Token": key}) as client:
                box["raw"] = collect(_Vendor(client, cfg, stop), ticker, cfg, floor_m,
                                     now.date().isoformat())
        except Exception as e:      # noqa: BLE001 — carried to the caller below
            box["error"] = e

    worker = threading.Thread(target=work, name="peers-fetch", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        stop.set()
        log_abstain("peer fetch failed", ticker, TimeoutError("over the deadline"))
        return PeerTable(UNAVAILABLE, UNAVAILABLE_TEXT, as_of, ticker)
    if "error" in box:
        log_abstain("peer fetch failed", ticker, RuntimeError(str(box["error"]).replace(key, "***")))
        return PeerTable(UNAVAILABLE, UNAVAILABLE_TEXT, as_of, ticker)
    try:
        return build(ticker, getattr(card, "sic_bucket", None), box["raw"], cfg, floor_m, as_of)
    except Exception as e:      # noqa: BLE001 — never-raises contract
        log_abstain("peer section failed", ticker, e)
        return PeerTable(UNAVAILABLE, UNAVAILABLE_TEXT, as_of, ticker)


def from_record(rec: Any) -> Optional[PeerTable]:
    """The section from a brief's JSON record; None for a brief without one. Never raises."""
    if not isinstance(rec, dict) or not rec.get("status"):
        return None
    rows = []
    for r in rec.get("rows") or []:
        if isinstance(r, dict):
            try:
                n = int(r.get("n") or 0)
            except (TypeError, ValueError):
                n = 0
            rows.append(PeerRow(str(r.get("label") or ""), str(r.get("company") or ""),
                                str(r.get("median") or ""), n, str(r.get("rank") or "")))
    return PeerTable(
        status=str(rec.get("status")), text=str(rec.get("text") or ""),
        as_of=str(rec.get("as_of") or ""), ticker=str(rec.get("ticker") or ""),
        basis=str(rec.get("basis") or ""),
        peers=[str(p) for p in (rec.get("peers") or []) if isinstance(p, str)],
        rows=rows, masked=[str(m) for m in (rec.get("masked") or [])],
        relative=str(rec.get("relative") or ""), growth=str(rec.get("growth") or ""),
        in_prompt=rec.get("in_prompt") is True)


def heading(t: PeerTable) -> str:
    return HEADING_IN_PROMPT if t.in_prompt else HEADING


def context_line(t: Optional[PeerTable]) -> str:
    """The prompt's peer line, or "" unless the table is shown. PROMPT-ONLY: never a
    grounding segment, so a vendor figure cannot verify as a filing quote."""
    if t is None or t.status != SHOWN:
        return ""
    rows = "; ".join(f"{r.label} {r.company} vs peer median {r.median} (n={r.n}"
                     + (f", {r.rank}" if r.rank else "") + ")" for r in t.rows)
    return (f"Peer comparison (Finnhub vendor data as of {t.as_of}; context only, NOT filing "
            f"text — never quote it as filing evidence). Peers: {', '.join(t.peers)}. "
            f"{t.basis} {rows}. Ranks count {t.ticker} with the peers that report the measure; "
            "for a multiple, lower is cheaper on that measure; n/m is a multiple or ratio on "
            f"negative earnings, EBITDA or equity. Relative performance: {t.relative} Growth vs "
            f"the peer set: {t.growth}")


def note(t: PeerTable) -> str:
    return f"Finnhub, as of {t.as_of}." if t.as_of else "Finnhub."


def masked_note(t: PeerTable) -> str:
    return ("Not meaningful for this sector, so not shown: " + ", ".join(t.masked) + "."
            if t.masked else "")


def markdown_lines(t: Optional[PeerTable]) -> list[str]:
    """The brief's section, starting with a blank line; [] when the section is off."""
    if t is None or not t.status:
        return []
    lines = ["", f"## {heading(t)}", f"_{note(t)}_"]
    if t.status != SHOWN:
        return lines + [f"_{t.text}_"]
    lines += [t.basis, "", f"| Measure | {t.ticker} | Peer median (n) | {t.ticker} rank |",
              "|---|---|---|---|"]
    lines += [f"| {r.label} | {r.company} | {r.median} ({r.n}) | {r.rank or '—'} |"
              for r in t.rows]
    lines += ["", f"Peers: {', '.join(t.peers)}."]
    if t.masked:
        lines.append(masked_note(t))
    lines += [f"_{RANK_NOTE.format(ticker=t.ticker)}_", "",
              f"- **Relative performance:** {t.relative}",
              f"- **Growth vs the peer set:** {t.growth}"]
    return lines


def text_lines(t: Optional[PeerTable]) -> list[str]:
    """The chat message's lines; the table itself is in the report."""
    if t is None or not t.status:
        return []
    if t.status != SHOWN:
        reason = t.text or "Not shown."
        return ["   🧭 Peer comparison: " + reason[:1].lower() + reason[1:]]
    seen = "given to the model" if t.in_prompt else "not seen by the model"
    return [f"   🧭 Peer comparison (vendor data, {seen}) vs "
            f"{', '.join(t.peers)} — the table is in the report",
            f"      {t.relative}", f"      {t.growth}"]
