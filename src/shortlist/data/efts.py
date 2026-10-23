"""SEC EDGAR full-text search (EFTS) primitives for 8-K discovery.

EFTS returns 8-K item codes inline (`_source.items`), so a full day of 8-Ks costs a
few requests instead of a header fetch per filing. The URL, normalization, day-cache
contract and windowing live here once (shared-leaf pattern, as in data/finra.py).

Observed EFTS behavior this module encodes. Do not "fix" it back:
- `forms=8-K` filters on `root_forms` and returns 8-K/A rows. `file_type` is preserved
  on every row; dropping non-8-K rows is the aggregators' job. The cache stores the
  complete, unfiltered day.
- EFTS lags: today's date returns `total: 0`. A day cache is final only once fetched
  >= EFTS_LAG_DAYS after the day; younger fetches are reused intra-day only.
- The pagination window is `from+size <= 10k`, so any range with `total >= 9,900` is
  split recursively at the date midpoint.
- A browser-like UA + Accept set is required. 500s are intermittent: bounded retry.
- SEC fair access: THROTTLE_S sleep before every request (~3 req/s).

The default (`q=None`) request params are frozen; tests/test_data_efts.py pins them. An
optional exact-phrase `q` adds a `q` key and uses its own cache namespace
(`.cache/efts_buyback/<phrase-hash>/`).

No production caller: the scout consumers were retired
(`docs/audits/2026-08-11-scout-retirement.md`). CI pins the parse shapes; live tests
skip by default. Kept for the EFTS behavior above.
"""
from __future__ import annotations

import calendar as _cal
import hashlib
import time
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from ..env import redact_secrets
from .diskcache import read_json_cache, write_json_cache

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EFTS_LAG_DAYS = 2       # EFTS indexes with a lag: today's query returns total 0
THROTTLE_S = 0.35       # sleep before EVERY request (~3 req/s SEC fair access)
_PAGE = 100
_SPLIT_TOTAL = 9_900    # ES from+size window is 10k; split any range at/above this
# EFTS 500s arrive in bursts. 2 retries ride out only ~3s; 5 ride out ~23s (waits
# 1+2+4+8+8). A range fetch is all-or-nothing per chunk, so one exhausted page discards
# the chunk's other pages.
_MAX_RETRIES = 5
_RETRY_BASE_S = 1.0
_RETRY_MAX_S = 8.0

# EFTS rejects UA-only requests. The operator identity rides in the UA tail for SEC
# fair-access contact. Accept-Encoding must stay within what httpx decodes (no br/zstd).
def _efts_headers(identity: str) -> dict:
    return {
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       f"(KHTML, like Gecko) Chrome/124 Safari/537.36 {identity}"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Origin": "https://www.sec.gov",
        "Referer": "https://www.sec.gov/",
    }


def normalize_hit(hit) -> Optional[dict]:
    """One EFTS hit -> the normalized row every consumer reads. None on junk (no adsh /
    no ciks). `file_type` is preserved verbatim (8-K/A included). `display_names` are not
    a ticker source."""
    src = (hit or {}).get("_source") or {}
    adsh = src.get("adsh")
    ciks = src.get("ciks") or []
    if not adsh or not ciks:
        return None
    return {
        "adsh": str(adsh),
        "cik": str(ciks[0]),
        "items": [str(i) for i in (src.get("items") or [])],
        "file_date": str(src.get("file_date") or ""),
        "file_type": str(src.get("file_type") or ""),
        "sics": [str(s) for s in (src.get("sics") or [])],
        "display_names": [str(d) for d in (src.get("display_names") or [])],
    }


def _total(payload: dict) -> int:
    try:
        return int(((payload.get("hits") or {}).get("total") or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0


def _hits(payload: dict) -> list:
    return (payload.get("hits") or {}).get("hits") or []


def _phrase_q(phrase: str) -> str:
    """EFTS exact-phrase query value: the phrase in double quotes. Unquoted terms are ANDed."""
    return f'"{phrase}"'


def _page(get: Callable, start: date, end: date, from_: int, *,
          throttle_s: float, max_retries: int, q: Optional[str] = None) -> Optional[dict]:
    """One throttled page with bounded retry-on-5xx. None = failed (never raises).

    `q=None` keeps the frozen item-query params (tests/test_data_efts.py); a non-None `q`
    only adds a trailing exact-phrase `q` key."""
    params = {"forms": "8-K", "dateRange": "custom",
              "startdt": start.isoformat(), "enddt": end.isoformat(),
              "from": from_, "size": _PAGE}
    if q is not None:
        params["q"] = _phrase_q(q)
    for attempt in range(max_retries + 1):
        if throttle_s > 0:
            time.sleep(throttle_s)
        status, payload = get(params)
        if status == 200 and payload is not None:
            return payload
        if not (500 <= status < 600) or attempt == max_retries:
            return None                    # non-5xx (or exhausted): give up, no retry-spam
        time.sleep(min(_RETRY_BASE_S * (2 ** attempt), _RETRY_MAX_S))
    return None


def _range(start: date, end: date, *, get: Callable, throttle_s: float,
           max_retries: int, q: Optional[str] = None) -> Optional[list[dict]]:
    first = _page(get, start, end, 0, throttle_s=throttle_s, max_retries=max_retries, q=q)
    if first is None:
        return None
    total = _total(first)
    if total >= _SPLIT_TOTAL and start < end:
        mid = start + timedelta(days=(end - start).days // 2)
        left = _range(start, mid, get=get, throttle_s=throttle_s, max_retries=max_retries, q=q)
        if left is None:
            return None
        right = _range(mid + timedelta(days=1), end, get=get,
                       throttle_s=throttle_s, max_retries=max_retries, q=q)
        if right is None:
            return None
        return left + right
    if total >= _SPLIT_TOTAL:
        warnings.warn(f"efts: single day {start} reports {total} hits — truncated at the "
                      "ES pagination window", stacklevel=2)
    rows = [r for r in (normalize_hit(h) for h in _hits(first)) if r]
    from_ = _PAGE
    while from_ < total and from_ + _PAGE <= 10_000:
        page = _page(get, start, end, from_, throttle_s=throttle_s,
                     max_retries=max_retries, q=q)
        if page is None:
            return None
        hs = _hits(page)
        if not hs:
            break
        rows.extend(r for r in (normalize_hit(h) for h in hs) if r)
        from_ += _PAGE
    return rows


def fetch_eightk_range(start: date, end: date, *, identity: str,
                       throttle_s: float = THROTTLE_S, max_retries: int = _MAX_RETRIES,
                       timeout: float = 30.0, q: Optional[str] = None,
                       _get=None) -> Optional[list[dict]]:
    """All normalized 8-K-rooted rows filed in [start, end]. None = fetch failed (warned,
    redacted); [] = none. `_get(params) -> (status, payload|None)` is the test seam; the
    default opens one httpx.Client for the whole range. `q` is an optional exact phrase."""
    close = None
    get = _get
    if get is None:
        import httpx
        client = httpx.Client(timeout=timeout, headers=_efts_headers(identity))
        close = client.close

        def get(params, _c=client):
            resp = _c.get(EFTS_URL, params=params)
            if resp.status_code != 200:
                return resp.status_code, None
            try:
                return 200, resp.json()
            except ValueError:
                return 200, None
    try:
        return _range(start, end, get=get, throttle_s=throttle_s,
                      max_retries=max_retries, q=q)
    except Exception as exc:  # noqa: BLE001 — the leaf never raises to a live scan
        warnings.warn(f"efts: range fetch failed for {start}:{end}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None
    finally:
        if close is not None:
            close()


# --- day cache (complete, unfiltered rows) ---

def _day_cache_path(cache_dir: str, day: date) -> Path:
    return Path(cache_dir) / f"{day.isoformat()}.json"


def _fresh(env: dict, day: date, today: date) -> bool:
    """Final once fetched >= EFTS_LAG_DAYS after the day; a younger fetch is reused
    intra-day only, else it is a miss."""
    try:
        fetched = date.fromisoformat(str(env.get("fetched_on")))
    except (TypeError, ValueError):
        return False
    return day <= fetched - timedelta(days=EFTS_LAG_DAYS) or fetched == today


def _read_day_cache(day: date, cache_dir: str, today: date) -> Optional[list[dict]]:
    env = read_json_cache(_day_cache_path(cache_dir, day))
    if isinstance(env, dict) and isinstance(env.get("rows"), list) \
            and _fresh(env, day, today):
        return env["rows"]
    return None


def _write_day_cache(day: date, rows: list[dict], cache_dir: str, today: date) -> None:
    write_json_cache(_day_cache_path(cache_dir, day),
                     {"fetched_on": today.isoformat(), "rows": rows})


def fetch_eightk_day(day: date, *, identity: str, cache_dir: str = ".cache/efts",
                     today: Optional[date] = None, **fetch_kw) -> Optional[list[dict]]:
    """One day's complete normalized rows, day-cached. None = fetch failed (the cache is
    untouched)."""
    today = today or date.today()
    cached = _read_day_cache(day, cache_dir, today)
    if cached is not None:
        return cached
    rows = fetch_eightk_range(day, day, identity=identity, **fetch_kw)
    if rows is None:
        return None
    _write_day_cache(day, rows, cache_dir, today)
    return rows


def _month_spans(start: date, end: date) -> list[tuple[date, date]]:
    spans, d = [], start
    while d <= end:
        last = date(d.year, d.month, _cal.monthrange(d.year, d.month)[1])
        spans.append((d, min(last, end)))
        d = last + timedelta(days=1)
    return spans


def fetch_eightk_window(start: date, end: date, *, identity: str,
                        cache_dir: str = ".cache/efts",
                        today: Optional[date] = None, q: Optional[str] = None,
                        **fetch_kw) -> Optional[list[dict]]:
    """Ranged fetch over [start, end], chunked by month, reusing the day cache. A chunk
    whose days are all cache-fresh makes no request; otherwise one ranged fetch re-fills
    every day cache in the chunk, empty days included. None = any chunk failed.

    When `q` is set, the day cache moves under a phrase-hash subdir (`_phrase_subdir`),
    so phrase-filtered rows never pool into the shared item-query day cache."""
    today = today or date.today()
    if q is not None:
        cache_dir = _phrase_subdir(cache_dir, q)
    out: list[dict] = []
    for c_start, c_end in _month_spans(start, end):
        days = [c_start + timedelta(days=i) for i in range((c_end - c_start).days + 1)]
        cached = {d: _read_day_cache(d, cache_dir, today) for d in days}
        if all(v is not None for v in cached.values()):
            for d in days:
                out.extend(cached[d])
            continue
        rows = fetch_eightk_range(c_start, c_end, identity=identity, q=q, **fetch_kw)
        if rows is None:
            return None
        by_day: dict[str, list[dict]] = {d.isoformat(): [] for d in days}
        for r in rows:
            by_day.setdefault(r.get("file_date") or "", []).append(r)
        for d in days:
            _write_day_cache(d, by_day.get(d.isoformat(), []), cache_dir, today)
        out.extend(rows)
    return out


# --- exact-phrase full-text queries ---

BUYBACK_CACHE_DIR = ".cache/efts_buyback"


def _phrase_subdir(cache_dir: str, phrase: str) -> str:
    """A phrase-hash subdirectory of `cache_dir`, isolating each phrase's day cache."""
    h = hashlib.sha1(phrase.encode("utf-8")).hexdigest()[:16]
    return str(Path(cache_dir) / h)


def fetch_phrase_day(phrase: str, day: date, *, identity: str,
                     cache_dir: str = BUYBACK_CACHE_DIR, today: Optional[date] = None,
                     **fetch_kw) -> Optional[list[dict]]:
    """One day's complete normalized rows matching an exact phrase, day-cached under
    `<cache_dir>/<phrase-hash>/<day>.json`. None = fetch failed. A single-day
    `fetch_eightk_window(day, day, q=phrase)`."""
    return fetch_eightk_window(day, day, identity=identity, cache_dir=cache_dir,
                               today=today, q=phrase, **fetch_kw)


def fetch_phrase_window(phrases, start: date, end: date, *, identity: str,
                        cache_dir: str = BUYBACK_CACHE_DIR, today: Optional[date] = None,
                        **fetch_kw) -> Optional[list[dict]]:
    """Ranged fetch over [start, end] for every phrase. Rows are tagged with the matched
    `phrase` and merged; accession dedup is the aggregator's job. None = any phrase's
    window failed."""
    today = today or date.today()
    out: list[dict] = []
    for phrase in phrases:
        rows = fetch_eightk_window(start, end, identity=identity, cache_dir=cache_dir,
                                   today=today, q=phrase, **fetch_kw)
        if rows is None:
            return None
        out.extend({**r, "phrase": phrase} for r in rows)
    return out
