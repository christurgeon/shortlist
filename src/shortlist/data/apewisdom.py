"""Keyless WallStreetBets mention data via ApeWisdom (https://apewisdom.io/api/).

A dependency-light leaf shared by the harness WsbSource (async, via to_thread) and
the scout WsbHypeSignal (sync). One bulk GET of the wallstreetbets filter (page 1 =
top tickers by volume), normalized + disk-cached by fetch date. NEVER raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..env import redact_secrets
from .diskcache import read_json_cache, write_json_cache

WSB_URL = "https://apewisdom.io/api/v1.0/filter/wallstreetbets"
_DEFAULT_CACHE_DIR = ".cache/apewisdom"


@dataclass
class WsbMention:
    """One ApeWisdom WSB row, normalized. `ticker` is the upcased symbol (dots kept,
    e.g. "BRK.B") used for downstream screening; the index keys it via norm_symbol().
    `mention_delta_pct`/`rising` are derived HERE for the scout consumer; the bridge
    re-derives the parallel StockMetrics fields from raw facts (ShortInterest pattern)
    — keep the two derivations in lockstep if you edit either."""
    ticker: str
    mentions: Optional[int] = None
    mentions_24h_ago: Optional[int] = None
    upvotes: Optional[int] = None
    rank: Optional[int] = None
    rank_24h_ago: Optional[int] = None
    as_of: Optional[str] = None              # ISO date this data was fetched (staleness anchor)
    mention_delta_pct: Optional[float] = None  # (mentions - prev)/prev; None if prev 0/None
    rising: Optional[bool] = None              # mentions > mentions_24h_ago


def norm_symbol(sym: str) -> str:
    """Collapse separators so BRK.B / BRK-B / BRKB all match one key (matches FINRA)."""
    return (sym or "").upper().replace("-", "").replace(".", "")


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def parse_wsb(payload: Any, as_of: str) -> dict[str, WsbMention]:
    """Pure: ApeWisdom JSON -> {norm_symbol: WsbMention}. Never raises (a malformed
    payload — non-dict, or `results` not a list — yields {})."""
    rows = (payload or {}).get("results") if isinstance(payload, dict) else None
    rows = rows if isinstance(rows, list) else []
    out: dict[str, WsbMention] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        tk = (r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        mentions = _int(r.get("mentions"))
        prev = _int(r.get("mentions_24h_ago"))
        delta = (mentions - prev) / prev if (mentions is not None and prev) else None
        rising = (mentions > prev) if (mentions is not None and prev is not None) else None
        wm = WsbMention(
            ticker=tk, mentions=mentions, mentions_24h_ago=prev,
            upvotes=_int(r.get("upvotes")), rank=_int(r.get("rank")),
            rank_24h_ago=_int(r.get("rank_24h_ago")), as_of=as_of,
            mention_delta_pct=delta, rising=rising,
        )
        key = norm_symbol(tk)
        prior = out.get(key)
        if prior is None or (wm.mentions or 0) >= (prior.mentions or 0):
            out[key] = wm
    return out


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def fetch_wsb_mentions(
    cache_dir: str = _DEFAULT_CACHE_DIR, timeout: float = 20.0
) -> tuple[dict[str, WsbMention], Optional[str]]:
    """Bulk-fetch (or read today's cache) and return ({norm_symbol: WsbMention}, error).
    NEVER raises — on failure returns ({}, redacted_error)."""
    as_of = _today_iso()
    cp = Path(cache_dir) / f"{as_of}.json"
    cached = read_json_cache(cp)
    if cached is not None:
        try:
            return parse_wsb(cached.get("payload"), cached.get("as_of", as_of)), None
        except Exception:
            pass  # corrupt cache payload -> refetch
    try:
        import httpx  # lazy: only needed for live runs
        r = httpx.get(WSB_URL, timeout=timeout, headers={"Accept": "application/json"})
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        return {}, redact_secrets(str(e))
    write_json_cache(cp, {"as_of": as_of, "payload": payload})
    return parse_wsb(payload, as_of), None
