"""SEC EDGAR full-text search (EFTS) primitives for 8-K discovery.

The 13D-pattern daily index carries NO item codes; EFTS returns them inline
(`_source.items`), so a full day of 8-Ks costs 3-6 requests instead of a header fetch
per filing. Shared-leaf pattern (data/finra.py): the URL, normalization, day-cache
contract, and windowing live here ONCE so the live EdgarEightKSignal, the negative-item
veto sweep, and the batch backfill walker agree on one definition. No scout imports.

Live-probed facts this module encodes (2026-07-07, twice — do not "fix" back):
- `forms=8-K` filters on `root_forms` and RETURNS 8-K/A rows -> `file_type` is preserved
  on every normalized row; the `file_type != "8-K"` drop is the AGGREGATORS' job (the
  cache always stores the complete, unfiltered day).
- EFTS lags: querying today's date returns `total: 0` -> the day cache is only FINAL
  once fetched >= EFTS_LAG_DAYS after the day; younger fetches are reused intra-day only.
- ES-style pagination window (`from+size <= 10k`) -> any range whose `total >= 9,900`
  is split recursively at the date midpoint (earnings-heavy months approach the cap).
- Browser-ish UA + Accept headers required; intermittent 500s -> bounded retry+backoff.
- SEC fair access: THROTTLE_S sleep before every request (~3 req/s).

The default (`q=None`) item-query path is what EdgarEightKSignal / the veto sweep / the 8-K
backfill ride, and its request params are FROZEN (a byte-identical regression test in
tests/test_data_efts.py pins them). An OPTIONAL exact-phrase `q` threads a `"..."` full-text
query through the same fetch discipline for the buyback originator (data/buyback.py), with
its OWN cache namespace (`.cache/efts_buyback/<phrase-hash>/`) — the shared retry/throttle/
split/finality machinery lives here ONCE; only the params gain a `q` key when a phrase is set.

**No production caller since 2026-08-11.** Its consumers (the 8-K and buyback originators) retired with the scout
(`docs/audits/2026-08-11-scout-retirement.md`), so nothing in `shortlist` imports this
on the `/screen` or `/deep` path. Same deal as `shortlist/edgar/`: CI pins the PARSE
shapes, but upstream shape drift is only caught by the live tests, which are
`pytest.mark.live` and skip by default.
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
# EFTS 500s arrive in BURSTS, not as isolated blips (live-diagnosed 2026-07-08: the veto's
# 30-day cold-start sweep failed twice on ~43-page crawls while every short smoke passed;
# an instrumented run absorbed two 500s mid-crawl). max_retries=2 rides out only ~3s of
# burst (waits 1+2s); 5 rides out ~23s (1+2+4+8+8) — the whole-range fetch is all-or-nothing
# per chunk, so one exhausted page throws away the other ~40 pages' work.
_MAX_RETRIES = 5
_RETRY_BASE_S = 1.0
_RETRY_MAX_S = 8.0

# EFTS rejects bot-shaped (UA-only) requests — a browser-ish UA + Accept set is required
# (live-probed). The operator identity rides in the UA tail for SEC fair-access contact.
# Accept-Encoding stays a subset of what httpx can decode (no br/zstd — the Yahoo gotcha).
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
    no ciks). `file_type` is preserved verbatim (8-K/A included — see module docstring);
    `display_names` are carried for the SPAC name check ONLY, NEVER as a ticker source."""
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
    """EFTS exact-phrase query value: the phrase wrapped in double quotes (live-probed —
    `q="share repurchase program"` matches the phrase, unquoted ANDs the terms)."""
    return f'"{phrase}"'


def _page(get: Callable, start: date, end: date, from_: int, *,
          throttle_s: float, max_retries: int, q: Optional[str] = None) -> Optional[dict]:
    """One throttled page with bounded retry-on-5xx. None = failed (never raises).

    `q=None` (the default) reproduces the FROZEN item-query params byte-for-byte (pinned by
    tests/test_data_efts.py); a non-None `q` adds ONLY a trailing exact-phrase `q` key."""
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
    default opens ONE httpx.Client for the whole (possibly split/paginated) range.

    `q` (optional exact phrase) narrows the query to full-text hits — the buyback path; the
    default `q=None` is the frozen item-query used by the 8-K originator/veto/backfill."""
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


# --- day cache (COMPLETE unfiltered rows; envelope carries the fetch date) ---

def _day_cache_path(cache_dir: str, day: date) -> Path:
    return Path(cache_dir) / f"{day.isoformat()}.json"


def _fresh(env: dict, day: date, today: date) -> bool:
    """FINAL once fetched >= EFTS_LAG_DAYS after the day; a younger fetch is reused
    intra-day only (originator + veto share one fetch per run), else it is a miss."""
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
    """One day's complete normalized rows, day-cached. None = fetch failed (cache
    untouched — a failure is never frozen)."""
    today = today or date.today()
    cached = _read_day_cache(day, cache_dir, today)
    if cached is not None:
        return cached
    rows = fetch_eightk_range(day, day, identity=identity, **fetch_kw)
    if rows is None:
        return None
    _write_day_cache(day, rows, cache_dir, today)   # complete + unfiltered, ALWAYS
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
    """Ranged fetch over [start, end], chunked by month, reusing the day cache: a chunk
    whose every day is cache-fresh makes ZERO requests; otherwise ONE ranged fetch (with
    the >=9,900 split inside fetch_eightk_range) re-fills every day cache in the chunk
    (empty days included, so weekends/holidays never force a refetch). None = any chunk
    failed (the caller treats the window as failed — resumable at its own layer).

    `q` (optional exact phrase) is threaded to the range fetch AND stored complete/unfiltered
    per-day. STRUCTURAL GUARD: whenever `q` is set, the day cache is redirected under a
    phrase-hash subdir (`_phrase_subdir`) of `cache_dir`, so phrase-FILTERED rows can NEVER
    pool into the shared item-query day cache (`.cache/efts`) regardless of the caller's
    `cache_dir` — the cache-completeness invariant is enforced by construction, not docstring.
    The `q=None` path is byte-identical (no redirect)."""
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


# --- exact-phrase full-text queries (the buyback originator; own cache namespace) ---

BUYBACK_CACHE_DIR = ".cache/efts_buyback"


def _phrase_subdir(cache_dir: str, phrase: str) -> str:
    """A phrase-hash subdirectory under the buyback cache namespace, so each phrase's
    complete/unfiltered day cache is isolated (keyed by (phrase-hash, day))."""
    h = hashlib.sha1(phrase.encode("utf-8")).hexdigest()[:16]
    return str(Path(cache_dir) / h)


def fetch_phrase_day(phrase: str, day: date, *, identity: str,
                     cache_dir: str = BUYBACK_CACHE_DIR, today: Optional[date] = None,
                     **fetch_kw) -> Optional[list[dict]]:
    """One day's COMPLETE normalized rows matching an exact phrase, day-cached under
    `<cache_dir>/<phrase-hash>/<day>.json` (same envelope + finality rule as the item-query
    day cache). None = fetch failed (cache untouched — a failure is never frozen).

    A single-day `fetch_eightk_window(day, day, q=phrase)` — so the phrase day-cache contract
    (phrase-hash subdir choice, envelope, finality rule) has exactly ONE implementation, the
    one `fetch_phrase_window` also rides. The live buyback signal calls this; the signature is
    stable."""
    return fetch_eightk_window(day, day, identity=identity, cache_dir=cache_dir,
                               today=today, q=phrase, **fetch_kw)


def fetch_phrase_window(phrases, start: date, end: date, *, identity: str,
                        cache_dir: str = BUYBACK_CACHE_DIR, today: Optional[date] = None,
                        **fetch_kw) -> Optional[list[dict]]:
    """Ranged fetch over [start, end] for EVERY phrase, each with its own phrase-hash day
    cache (fetch_eightk_window namespaces automatically whenever `q` is set); rows are tagged
    with the matched `phrase` and merged (accession dedup is the aggregator's job, never
    here). None = any phrase's window failed. Used by the 8-K-shaped buyback backfill (the
    fetch factory in scout/backfill.py)."""
    today = today or date.today()
    out: list[dict] = []
    for phrase in phrases:
        rows = fetch_eightk_window(start, end, identity=identity, cache_dir=cache_dir,
                                   today=today, q=phrase, **fetch_kw)
        if rows is None:
            return None
        out.extend({**r, "phrase": phrase} for r in rows)
    return out
