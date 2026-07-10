"""SEC XBRL companyfacts I/O for the backtest: ticker->CIK resolution and a
disk-cached companyfacts fetch. Keyless; SEC fair-access requires a descriptive
User-Agent carrying a contact email (SEC_IDENTITY)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def build_cik_index(raw: dict) -> dict[str, str]:
    """{TICKER -> zero-padded 10-digit CIK} from SEC company_tickers.json."""
    idx = {}
    for row in raw.values():
        tk = str(row.get("ticker", "")).upper()
        cik = row.get("cik_str")
        if not tk or cik is None:
            continue
        try:
            idx[tk] = f"{int(cik):010d}"
        except (ValueError, TypeError):
            continue   # skip malformed rows, don't abort the whole index
    return idx


def cik_for(ticker: str, index: dict[str, str]) -> Optional[str]:
    """The zero-padded CIK for a ticker (case-insensitive), or None if unknown."""
    return index.get(ticker.upper())


def build_name_index(raw: dict) -> dict[str, str]:
    """{TICKER -> registrant title} from SEC company_tickers.json (rows carry
    `ticker` + `title`). Skips rows missing either field."""
    idx = {}
    for row in raw.values():
        tk = str(row.get("ticker", "")).upper()
        title = row.get("title")
        if not tk or not title:
            continue
        idx[tk] = str(title)
    return idx


def _facts_cache_path(cache_dir: str, cik: str, month: str) -> Path:
    return Path(cache_dir) / f"CIK{cik}-{month}.json"


# Marker persisted for a CIK whose companyfacts carry no us-gaap section (IFRS /
# 20-F foreign issuers). Caching the miss stops a full-universe backtest re-hitting
# SEC for the same never-resolving issuers every run; it's month-scoped like the
# positive cache, so a newcomer to us-gaap is picked up next month.
_NO_US_GAAP = {"_shortlist_no_us_gaap": True}


def _write_facts_cache(cp: Path, cache_dir: str, payload: dict) -> None:
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(payload))
    except Exception:
        pass  # cache write failure is non-fatal


def read_companyfacts_cache(cik: Optional[str], *, cache_dir: str,
                            month: str) -> Optional[dict]:
    """Disk-only read of a month-cached companyfacts payload (no network) — the
    lazy counterpart to fetch_companyfacts, for a memory-bounded backtest that
    loads one ticker's facts at a time. Returns None for the no-us-gaap marker,
    a missing/corrupt file, or a None cik."""
    if not cik:
        return None
    cp = _facts_cache_path(cache_dir, cik, month)
    try:
        if cp.exists():
            cached = json.loads(cp.read_text())
            if isinstance(cached, dict) and cached.get("_shortlist_no_us_gaap"):
                return None
            return cached
    except (ValueError, OSError):
        pass  # corrupt cache -> treat as miss
    return None


async def fetch_companyfacts(cik: str, client, *, cache_dir: str,
                             month: str) -> Optional[dict]:
    """companyfacts JSON for a zero-padded CIK, month-cached on disk. Returns
    None on empty payload (no us-gaap facts, e.g. IFRS 20-F issuers) — that miss
    is cached too (negative marker) so the next run within the month doesn't refetch.
    A true 404 raises from raise_for_status() and is handled by the caller."""
    cp = _facts_cache_path(cache_dir, cik, month)
    try:
        if cp.exists():
            cached = json.loads(cp.read_text())
            if isinstance(cached, dict) and cached.get("_shortlist_no_us_gaap"):
                return None                    # cached "no us-gaap" miss
            return cached
    except (ValueError, OSError):
        pass  # corrupt cache -> refetch
    resp = await client.get(_FACTS_URL.format(cik=cik))
    resp.raise_for_status()
    raw = resp.json()
    if not raw or "us-gaap" not in raw.get("facts", {}):
        _write_facts_cache(cp, cache_dir, _NO_US_GAAP)
        return None
    _write_facts_cache(cp, cache_dir, raw)
    return raw


async def fetch_cik_index(client, *, cache_dir: str, month: str) -> dict[str, str]:
    """SEC company_tickers.json -> {TICKER: CIK}, month-cached. Thin wrapper over
    fetch_company_tickers_raw (same cache file/keys) + the build_cik_index transform."""
    raw = await fetch_company_tickers_raw(client, cache_dir=cache_dir, month=month)
    return build_cik_index(raw)


async def fetch_company_tickers_raw(client, *, cache_dir: str, month: str) -> dict:
    """Raw SEC company_tickers.json, month-cached on disk. Callers build whichever
    index they need (build_cik_index / build_name_index) from the one payload."""
    cp = Path(cache_dir) / f"company_tickers-{month}.json"
    try:
        if cp.exists():
            return json.loads(cp.read_text())
    except (ValueError, OSError):
        pass
    resp = await client.get(_TICKERS_URL)
    resp.raise_for_status()
    raw = resp.json()
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(raw))
    except Exception:
        pass
    return raw
