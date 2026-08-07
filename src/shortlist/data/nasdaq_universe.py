"""Listed-universe market caps and last-sale prices, keyless, in three requests.

The `_form4.py` / `finra.py` shared-leaf pattern: a **pure** parser plus a thin day-cached
fetcher, so every consumer is testable offline.

`api.nasdaq.com/api/screener/stocks` returns the full NASDAQ / NYSE / AMEX common-stock
universe — ~7,200 symbols, ~5,800 with a usable `marketCap` — one request per exchange.

**Three things to know before touching this.**

1. **It is undocumented**, the same fragility class as the Yahoo screener this repo
   retired. That is why every failure path here returns `{}` rather than raising: an empty
   universe makes the investability floor abstain on everything, which is the byte-identical
   pre-feature funnel. The floor degrades to inert; it never blocks a run.
2. **It is NOT on sec.gov**, so it draws nothing from the shared `sec_throttle()` budget.
   Do not route it there — that budget exists to keep this box under SEC's fair-access
   ceiling, and padding it with unrelated hosts would misreport the thing it measures.
3. **It excludes ETFs and funds**, which is load-bearing beyond sizing: a symbol's absence
   here is weak evidence it is not listed common stock. Absence is still treated as
   *abstain* (never a drop) because it also captures OTC names, recent listings and plain
   API gaps.

Rows with an unparseable `marketCap` are skipped **individually** (mirroring
`secframes.parse_frame`), never failing the whole payload.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from .diskcache import read_json_cache, write_json_cache

_URL = ("https://api.nasdaq.com/api/screener/stocks"
        "?tableonly=true&limit=25000&offset=0&exchange={exchange}")
_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")

# A browser-shaped header set. The Yahoo-WAF lesson (CLAUDE.md): bot-shaped requests get an
# HTML rejection while a full header set returns JSON. Accept-Encoding stays httpx-decodable
# (no br/zstd without the dependency, or .json() fails).
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def _money(raw) -> Optional[float]:
    """`"$312.18"` / `"5,312,384,000,000"` / `""` / None -> float or None. Never raises."""
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    if not s or s in {"N/A", "--"}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def parse_universe(payload) -> dict:
    """Screener JSON -> `{UPPER symbol: (market_cap, last_sale)}`. **Pure, never raises.**

    Either value may be `None` — a symbol with a price but no cap is still worth carrying,
    because the floor's two legs abstain independently.
    """
    try:
        rows = (payload or {}).get("data", {}).get("table", {}).get("rows") or []
    except AttributeError:
        return {}
    out: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue                                  # skip one bad row, not the payload
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out[sym] = (_money(row.get("marketCap")), _money(row.get("lastsale")))
    return out


def fetch_universe(*, cache_dir: str = ".cache/nasdaq_universe", timeout: float = 30.0,
                   today: Optional[date] = None, _http_json=None) -> dict:
    """`{UPPER symbol: (market_cap, last_sale)}` for the listed universe, day-cached.

    Caches the **merged, parsed** map (not raw payloads) because the three exchange calls
    are only ever used together. Any failure — network, WAF, malformed JSON — degrades to
    `{}`, and a partial result is kept: two exchanges are better than none, and the floor
    abstains on whatever is missing.
    """
    day = (today or date.today()).isoformat()
    path = Path(cache_dir) / f"{day}.json"
    cached = read_json_cache(path)
    if cached is not None:
        return {k: tuple(v) for k, v in cached.items()}

    get = _http_json or _http_get_json
    merged: dict = {}
    for exchange in _EXCHANGES:
        try:
            merged.update(parse_universe(get(_URL.format(exchange=exchange), timeout)))
        except Exception:  # noqa: BLE001 — an undocumented endpoint; degrade, never abort
            continue
    if merged:
        write_json_cache(path, {k: list(v) for k, v in merged.items()})
    return merged


def _http_get_json(url: str, timeout: float):
    import httpx  # lazy: only needed for live runs
    with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return json.loads(r.text)


def adv_shares_from_finra(rows) -> dict:
    """`{UPPER symbol: average daily share volume}` from FINRA consolidated short-interest
    rows — the dataset the harness already fetches and disk-caches, so this costs **zero**
    additional requests.

    `averageDailyVolumeQuantity` is present on ~86% of rows and covers 93% of the tickers
    this funnel has ever surfaced. The dataset is **semi-monthly**, so a value can be up to
    ~4 weeks old; that is acceptable for a liquidity floor (ADV is slow-moving) and is why
    the floor is a floor rather than a ranking input. Non-positive and unparseable values
    are omitted so the floor abstains rather than reading them as "illiquid".
    """
    out: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbolCode") or "").strip().upper()
        if not sym:
            continue
        try:
            adv = float(row.get("averageDailyVolumeQuantity") or 0)
        except (TypeError, ValueError):
            continue
        if adv > 0:
            out[sym] = adv
    return out
