"""CIK -> ticker resolver, common-stock preferred. Reads SEC company_tickers.json.

Verified (2026-06-28): the file lists the common stock FIRST per CIK (PECE before
PECEU/PECER/PECEW; GOOGL before GOOG*; BAC before BAC-PB), and first-occurrence alone
is correct for 1,472/1,473 multi-ticker CIKs. So first-occurrence is authoritative and
the suffix backstop is sibling-relative ONLY — a blanket 'de-prioritize W/U/R/-P' rule
would mis-bind ~54 liquid issuers to a foreign-OTC (*F) or preferred sibling.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from pathlib import Path

import httpx

from .sec_throttle import sec_throttle

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# unit/warrant/right suffixes whose base, IF also a ticker of the same CIK, is the common
_UNIT_SUFFIX = re.compile(r"^(?P<base>[A-Z]{2,})(?:U|W|R|WS)$")
_PREF_SUFFIX = re.compile(r"^(?P<base>[A-Z]+)-P[A-Z]?$")

_MAX_ATTEMPTS = 3            # one transient 429 must not bail every EDGAR originator
_RETRY_BACKOFF_S = 1.0       # linear: 1s, 2s
_MAX_STALE_DAYS = 7          # beyond this a cached index can mis-resolve renamed symbols

# Per-process resolver memo. signals.py loads the index at 5 independent call sites; without
# this they can DISAGREE inside one session (an early site fetches fresh, a later one falls
# back to an older index mid-outage). Only non-empty results are stored — memoising {} would
# pin the whole run to the failure this module exists to survive.
_memo: dict[tuple[str, str, str], dict[str, str]] = {}
_memo_lock = threading.Lock()


def _norm_cik(cik: str | int) -> str:
    return f"{int(cik):010d}"


def build_cik_to_ticker(raw: dict) -> dict[str, str]:
    """{10-digit CIK -> best ticker}. First occurrence wins; a unit/warrant/right or
    hyphenated preferred that appears FIRST is replaced by its base ONLY when the base is
    also a ticker of the same CIK (sibling-relative).

    A malformed row (bad/absent cik_str or ticker shape) is skipped INDIVIDUALLY, keeping
    every good row — one bad entry among ~12k must not discard the whole index (a loud
    per-scan {} would silently zero buyback/8-K/13D). A wholly-unusable payload (e.g. a
    list-shaped body with no `.values()`) still raises to the caller's never-raises wrap."""
    by_cik: dict[str, list[str]] = {}
    for row in raw.values():
        try:
            cik = _norm_cik(row["cik_str"])
            tkr = str(row["ticker"]).upper()
        except (TypeError, ValueError, KeyError, AttributeError):
            continue                    # skip a single malformed row, keep the good ones
        if not tkr:
            continue
        by_cik.setdefault(cik, []).append(tkr)
    out: dict[str, str] = {}
    for cik, tickers in by_cik.items():
        chosen = tickers[0]                       # first-occurrence authoritative
        members = set(tickers)
        m = _UNIT_SUFFIX.match(chosen) or _PREF_SUFFIX.match(chosen)
        if m and m.group("base") in members:      # sibling-relative backstop only
            chosen = m.group("base")
        out[cik] = chosen
    return out


def resolve_ticker(cik: str | int, index: dict[str, str]) -> str | None:
    try:
        return index.get(_norm_cik(cik))
    except (TypeError, ValueError):
        return None


def reset_resolver_cache() -> None:
    """Drop the per-process resolver memo. For tests and long-lived processes (the bot)."""
    with _memo_lock:
        _memo.clear()


def _newest_cached_payload(cache_dir: str, today: date, max_stale_days: int) -> dict:
    """Newest `company_tickers-<day>.json` on disk within `max_stale_days` of `today`, or {}.

    The 2026-08-04 outage: a transient SEC 429 abstained every resolver-backed originator
    for a whole session while a valid 24h-old index sat unread here. The map drifts by a
    handful of rows a day, so a recent copy beats abstaining — but past the ceiling it can
    mis-resolve renamed/delisted symbols, and abstaining is right again."""
    best_day, best_payload = None, {}
    try:
        entries = list(Path(cache_dir).glob("company_tickers-*.json"))
    except OSError:
        return {}
    for path in entries:
        try:
            cached = date.fromisoformat(path.stem[len("company_tickers-"):])
            age = (today - cached).days
            if age < 0 or age > max_stale_days:
                continue
            if best_day is None or cached > best_day:
                payload = json.loads(path.read_text())
                if payload:
                    best_day, best_payload = cached, payload
        except (ValueError, OSError, json.JSONDecodeError):
            continue                     # an unparseable/undated sibling never blocks the rest
    return best_payload


def load_raw_company_tickers(identity: str, *, cache_dir: str = ".cache/sec_tickers",
                             _today: date | None = None,
                             _client: httpx.Client | None = None,
                             _sleep=None, _throttle=None) -> dict:
    """Day-cached raw company_tickers.json payload. SEC blocks UA-less GETs, so a
    contact-email User-Agent is mandatory. Never raises: returns {} on any failure. The
    raw payload carries the issuer `title` the CUSIP name-fallback resolver needs, which
    the CIK->ticker index throws away.

    A transient fetch failure is RETRIED (`_MAX_ATTEMPTS`, linear backoff) and then falls
    back to the newest recent cached index rather than abstaining — see
    `docs/audits/2026-08-05-discovery-funnel-audit.md` §3."""
    today = _today or date.today()
    sleep = _sleep or time.sleep
    cp = Path(cache_dir) / f"company_tickers-{today.isoformat()}.json"
    try:
        if cp.exists():
            return json.loads(cp.read_text())
    except (OSError, json.JSONDecodeError):
        pass                             # a corrupt same-day file falls through to the fetch
    client = _client or httpx.Client(timeout=30.0, headers={"User-Agent": identity})
    throttle = _throttle if _throttle is not None else sec_throttle()
    try:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                throttle("cik_tickers")   # www.sec.gov — draws on the shared budget
                resp = client.get(_TICKERS_URL)
                resp.raise_for_status()
                raw = resp.json()
                if not raw:
                    raise ValueError("empty company_tickers payload")
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(raw))
                return raw
            except Exception:  # noqa: BLE001 — retry transient SEC failures (429/5xx/timeout)
                if attempt < _MAX_ATTEMPTS - 1:
                    sleep(_RETRY_BACKOFF_S * (attempt + 1))
    finally:
        if _client is None:
            client.close()
    # Every attempt failed: a recent cached index beats abstaining on every row.
    return _newest_cached_payload(cache_dir, today, _MAX_STALE_DAYS)


def load_cik_to_ticker(identity: str, *, cache_dir: str = ".cache/sec_tickers",
                       _today: date | None = None, _client: httpx.Client | None = None,
                       _sleep=None, _throttle=None) -> dict[str, str]:
    """Day-cached company_tickers.json -> resolver index. SEC blocks UA-less GETs, so a
    contact-email User-Agent is mandatory. Never raises: returns {} on any failure.

    Memoised per (identity, cache_dir, day) so every call site in one run shares one index
    — see `_memo`. Call `reset_resolver_cache()` to drop it."""
    key = (identity, cache_dir, (_today or date.today()).isoformat())
    with _memo_lock:
        hit = _memo.get(key)
    if hit is not None:
        return hit
    raw = load_raw_company_tickers(identity, cache_dir=cache_dir, _today=_today,
                                   _client=_client, _sleep=_sleep, _throttle=_throttle)
    if not raw:
        return {}
    try:
        index = build_cik_to_ticker(raw)
    except Exception:  # noqa: BLE001 — a truthy-but-malformed payload (null/non-int cik_str,
        return {}      # list-shaped body) must degrade to {}, never crash the daily run.
    if index:
        with _memo_lock:
            _memo[key] = index
    return index
