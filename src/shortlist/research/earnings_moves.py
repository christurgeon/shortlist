"""Realized post-announcement price moves, from 8-K Item 2.02 (research-only).

The anchor that makes an implied earnings move interpretable. "The market prices +/-8%"
means nothing alone; "+/-8% against a company that has moved 0.8% on its last six
prints" is a finding, and so is the reverse. Measured spread across four names:

    AAPL   -7.4  +3.2  +0.5  -0.4  -2.5  -3.7    |median| 3.2%
    INTC   -7.9 +23.6 -17.0  +0.3  -8.5  -6.7    |median| 8.5%
    MSFT  +15.5  -3.9 -10.0  -2.9  +3.9  +7.6    |median| 7.6%
    KO     +0.9  +0.7  +2.3  -0.6  -0.7  +0.3    |median| 0.7%

Prompt-only, never scored, never in the grounding haystack. Feeds
`research/options.py:context_line`. Design: docs/audits/2026-08-24-options-surface-design.md.

WHY 8-K ITEM 2.02 AND NOT THE VENDOR CALENDAR
----------------------------------------------
Finnhub's `stock/earnings` rows carry only the fiscal `period` (quarter-end), never the
print date, and the free-tier calendar holds no past entries at all
(`data/sources/finnhub.py:_earnings`). So the stack has no historical announcement date.
8-K Item 2.02 (Results of Operations and Financial Condition) IS the announcement, it is
free and authoritative, and it validated at +0 days against print dates recovered
independently from the snapshot store on AAPL, GOOGL and MSFT.

KNOWN IMPRECISION — THE SESSION AMBIGUITY
------------------------------------------
An 8-K 2.02 filed after the close reacts on the NEXT session; one filed pre-open reacts
the same day. This module spans the announcement close-to-close (last close on or before
the filing date, to the first close after it), which is correct for after-close filers —
most large caps — and shifts by one session for pre-open filers. The filing's ACCEPTANCE
TIMESTAMP disambiguates it and is not yet used; a pre-open filer's move is therefore
attributed to the session after the reaction. Treat single readings as approximate and
the median of six as sound.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Optional

from ..data.diskcache import read_json_cache, write_json_cache

# Item codes as filed, e.g. "2.02,9.01". Anchored on both sides so a substring search
# cannot fire: a bare `"2.02" in items` also matches 12.02 (Ability to Fund Operations,
# a different disclosure entirely) and 2.021, and a wrong announcement date silently
# shifts every realized move that follows from it.
_ITEM_202 = re.compile(r"(?<![\d.])2\.02(?![\d])")
_DEFAULT_QUARTERS = 6
# 8-K filings scanned back from newest. Six quarters of results sit well inside this on
# every filer measured; a large filer posts many non-2.02 8-Ks between prints.
_INDEX_SCAN = 40
# Shared with data/sources/yahoo.py — same directory, same key format, same request
# params, so the two hit the same day-cache entry rather than each paying for a fetch.
_YAHOO_CACHE_DIR = ".cache/yahoo"
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
_YAHOO_PARAMS = {"range": "5y", "interval": "1d"}


def is_results_8k(items: Optional[str]) -> bool:
    """True when an 8-K's item list includes Item 2.02 (Results of Operations)."""
    return bool(_ITEM_202.search(str(items or "")))


def realized_moves(closes: list, announcements: list,
                   quarters: int = _DEFAULT_QUARTERS) -> list:
    """Close-to-close moves spanning each announcement, newest first.

    `closes` is [(date, close)] ascending; `announcements` is a list of dates. Returns
    [(iso_date, pct)] and silently drops any announcement without a session on both
    sides — the newest print routinely post-dates the last available close. Pure."""
    if not closes or not announcements:
        return []
    dates = [d for d, _ in closes]
    px = dict(closes)
    out: list[tuple[str, float]] = []
    for announced in sorted(set(announcements), reverse=True):
        before = [d for d in dates if d <= announced]
        after = [d for d in dates if d > announced]
        if not before or not after:
            continue
        prior = px[before[-1]]
        if not prior:
            continue
        out.append((announced.isoformat(), round((px[after[0]] / prior - 1) * 100, 1)))
        if len(out) >= quarters:
            break
    return out


def _as_date(value: Any) -> Optional[datetime.date]:
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def announcement_dates(ticker: str, quarters: int = _DEFAULT_QUARTERS,
                       identity: Optional[str] = None) -> list:
    """8-K Item 2.02 filing dates for `ticker`, newest first. Raises only if
    SEC_IDENTITY is unset (like its filings.py siblings); the caller guards that."""
    from edgar import Company  # lazy: optional [edgar] extra

    from .filings import require_identity

    require_identity(identity)
    found: list[datetime.date] = []
    for filing in Company(ticker).get_filings(form="8-K").head(_INDEX_SCAN):
        if not is_results_8k(getattr(filing, "items", None)):
            continue
        filed = _as_date(getattr(filing, "filing_date", None))
        if filed is not None:
            found.append(filed)
        if len(found) >= quarters:
            break
    return found


def daily_closes(ticker: str, timeout: float = 20.0,
                 cache_dir: str = _YAHOO_CACHE_DIR) -> list:
    """Daily closes as [(date, close)] ascending.

    REUSES THE PRICE SOURCE'S DAY-CACHED PAYLOAD — same path, same request params — so a
    /deep costs ZERO extra Yahoo requests: `data/sources/yahoo.py` has already fetched
    and cached this exact chart earlier in the same run (it leads the harness merge for
    price fields). An earlier version issued its own uncached `range=2y` call and added
    **40-60 seconds** to every brief, because Yahoo answers the v8 chart slowly from a
    datacenter IP even while returning 200. Do not reintroduce a second fetch, and do
    not change `range`/`interval` here without changing them in the source too, or the
    two will write different payloads to the same cache key.

    Falls back to a live fetch (and populates the shared cache) when the file is absent,
    which is the `--provider`-narrowed or research-only path."""
    path = Path(cache_dir) / f"{ticker.upper()}-{datetime.date.today().isoformat()}.json"
    raw = read_json_cache(path)
    if raw is None:
        import httpx

        r = httpx.get(f"{_YAHOO_CHART}/{ticker.upper()}",
                      params=_YAHOO_PARAMS,
                      headers={"User-Agent": "Mozilla/5.0 (shortlist research)"},
                      timeout=timeout)
        r.raise_for_status()
        raw = r.json()
        write_json_cache(path, raw)
    return _closes_from_payload(raw)


def _closes_from_payload(raw: Any) -> list:
    """[(date, close)] ascending from a Yahoo chart envelope; [] when it carries no
    result (the 404 envelope the price source day-caches for a dead ticker)."""
    results = ((raw or {}).get("chart") or {}).get("result") or []
    if not results:
        return []
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote") or [{}]
    values = quote[0].get("close") or []
    return [(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date(), c)
            # strict=False: Yahoo has been seen to return a close array shorter than
            # its timestamp array on a partial session; truncating is correct, raising
            # would take out the whole clause.
            for t, c in zip(stamps, values, strict=False) if c is not None]


def fetch_moves(ticker: str, cfg: Optional[dict] = None,
                as_of: Optional[str] = None,
                today: Optional[datetime.date] = None) -> list:
    """Realized post-announcement moves for `ticker`, or []. Never raises — the clause
    simply abstains, but the reason reaches stderr so a systematic failure does not look
    like "this company has never reported".

    POINT-IN-TIME: the price series is fetched live and has no as-of control, so a past
    `as_of` abstains rather than splicing today's prices into a historical snapshot —
    the same guard as `options.fetch_surface`."""
    from .filings import log_abstain

    cfg = cfg or {}
    today = today or datetime.date.today()
    if as_of and as_of[:10] != today.isoformat():
        return []
    quarters = int(cfg.get("earnings_lookback_quarters", _DEFAULT_QUARTERS))
    try:
        announcements = announcement_dates(ticker, quarters)
        if not announcements:
            return []
        return realized_moves(daily_closes(ticker, float(cfg.get("timeout", 20))),
                              announcements, quarters)
    except Exception as e:      # noqa: BLE001 — never-raises contract
        log_abstain("realized earnings moves failed", ticker, e)
        return []
