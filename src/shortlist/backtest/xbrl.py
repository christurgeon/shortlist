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


def _facts_cache_path(cache_dir: str, cik: str, month: str) -> Path:
    return Path(cache_dir) / f"CIK{cik}-{month}.json"


async def fetch_companyfacts(cik: str, client, *, cache_dir: str,
                             month: str) -> Optional[dict]:
    """companyfacts JSON for a zero-padded CIK, month-cached on disk. Returns
    None on empty payload (no us-gaap facts, e.g. IFRS 20-F issuers). A true 404
    raises from raise_for_status() and is handled by the caller."""
    cp = _facts_cache_path(cache_dir, cik, month)
    try:
        if cp.exists():
            return json.loads(cp.read_text())
    except (ValueError, OSError):
        pass  # corrupt cache -> refetch
    resp = await client.get(_FACTS_URL.format(cik=cik))
    resp.raise_for_status()
    raw = resp.json()
    if not raw or "us-gaap" not in raw.get("facts", {}):
        return None
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(raw))
    except Exception:
        pass  # cache write failure is non-fatal
    return raw


async def fetch_cik_index(client, *, cache_dir: str, month: str) -> dict[str, str]:
    """SEC company_tickers.json -> {TICKER: CIK}, month-cached."""
    cp = Path(cache_dir) / f"company_tickers-{month}.json"
    try:
        if cp.exists():
            return build_cik_index(json.loads(cp.read_text()))
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
    return build_cik_index(raw)
