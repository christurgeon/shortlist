"""Point-in-time CIK<->ticker resolver for the Phase-2 backfill (survivorship correction).

Forward (CIK->ticker, 13D): active CIK -> live company_tickers.json; delisted CIK -> nearest
Wayback snapshot <= the event date. Reverse (ticker->CIK, FINRA): archive-only, None for the
~82% of FINRA's OTC universe absent from company_tickers.json (reported as an abstention rate).
See docs/superpowers/specs/2026-07-01-...-design.md §8/§16/§17. Free/keyless; caches forever;
polite to archive.org (~1 req/s). Never raises to the caller.

SERIAL-ONLY (L1): the module-level request throttle is not thread-safe. The backfill coordinator
resolves serially; do not share one Symbology across threads without adding a lock.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import httpx

from .cik_tickers import build_cik_to_ticker
from ..env import redact_secrets

_CDX_URL = "http://web.archive.org/cdx/search/cdx"
_WB_RAW = "http://web.archive.org/web/{ts}id_/https://www.sec.gov/files/company_tickers.json"
_MIN_INTERVAL_S = 1.0                       # archive.org politeness ceiling (spec §17)
_last_request_ts: float = 0.0               # module-level throttle (monotonic seconds)


def _ts_to_date(ts: str) -> date:
    return datetime.strptime(ts[:8], "%Y%m%d").date()


def parse_cdx(rows: list[list[str]]) -> list[tuple[str, date]]:
    """CDX output=json rows -> sorted [(timestamp14, date)] for statuscode==200 only."""
    if not rows:
        return []
    header = rows[0]
    try:
        i_ts, i_st = header.index("timestamp"), header.index("statuscode")
    except ValueError:
        return []
    out: list[tuple[str, date]] = []
    for r in rows[1:]:
        if len(r) <= max(i_ts, i_st) or r[i_st] != "200":
            continue
        ts = r[i_ts]
        if len(ts) >= 8 and ts[:8].isdigit():
            try:
                out.append((ts, _ts_to_date(ts)))
            except ValueError:
                continue
    out.sort(key=lambda t: t[0])
    return out


def nearest_snapshot_before(snapshots: list[tuple[str, date]], target: date) -> Optional[str]:
    """Timestamp of the latest snapshot with date <= target (no look-ahead). None if none."""
    best: Optional[str] = None
    for ts, d in snapshots:                 # snapshots are sorted ascending
        if d <= target:
            best = ts
        else:
            break
    return best


def _throttle() -> None:
    global _last_request_ts
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def cdx_snapshots(*, cache_dir: str, client: Optional[httpx.Client] = None,
                  max_age_days: int = 7, today: Optional[date] = None) -> list[tuple[str, date]]:
    """Full CDX snapshot list (one unfiltered call), cached; refresh if older than max_age_days.
    Never raises -> [] on failure."""
    ref = today or date.today()
    cp = Path(cache_dir) / "cdx-company_tickers.json"
    try:
        if cp.exists():
            payload = json.loads(cp.read_text())
            fetched = date.fromisoformat(payload.get("fetched", "1970-01-01"))
            if (ref - fetched).days < max_age_days:
                return [(ts, date.fromisoformat(d)) for ts, d in payload["snapshots"]]
        owns = client is None
        client = client or httpx.Client(timeout=60.0, headers={"User-Agent": "shortlist symbology"})
        try:
            _throttle()
            # limit far above the real count (~660, spec §17) so a single call is complete;
            # M3 truncation guard: warn if we ever hit the cap (would mean silent pagination).
            _CAP = 100000
            resp = client.get(_CDX_URL, params={"url": "sec.gov/files/company_tickers.json",
                                                "output": "json", "limit": str(_CAP)},
                              follow_redirects=True)
            resp.raise_for_status()
            rows = resp.json()
        finally:
            if owns:
                client.close()
        snaps = parse_cdx(rows)
        if len(rows) - 1 >= _CAP:            # header + rows hit the cap -> possible truncation
            import warnings
            warnings.warn("symbology: CDX response hit the row cap — snapshot list may be "
                          "truncated (raise the limit)", stacklevel=2)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"fetched": ref.isoformat(),
                                  "snapshots": [[ts, d.isoformat()] for ts, d in snaps]}))
        return snaps
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the backfill
        import warnings
        warnings.warn(f"symbology: CDX fetch failed: {redact_secrets(str(exc))}", stacklevel=2)
        return []
