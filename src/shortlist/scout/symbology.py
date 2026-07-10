"""Point-in-time CIK<->ticker resolver for the Phase-2 backfill (survivorship correction).

Forward (CIK->ticker, 13D): active CIK -> live company_tickers.json; delisted CIK -> nearest
Wayback snapshot <= the event date. Reverse (ticker->CIK, FINRA): archive-only, None for the
~82% of FINRA's OTC universe absent from company_tickers.json (reported as an abstention rate).
See docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md §8/§16/§17. Free/keyless; caches forever;
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

from ..env import redact_secrets
from .cik_tickers import _PREF_SUFFIX, _UNIT_SUFFIX, _norm_cik, build_cik_to_ticker, load_cik_to_ticker

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


def _raw_snapshot(timestamp: str, *, cache_dir: str, client: Optional[httpx.Client]) -> Optional[dict]:
    """Fetch (or read from the forever cache) one snapshot's raw company_tickers.json.
    None (never raises) if unavailable and no client to fetch with."""
    cp = Path(cache_dir) / "wayback_tickers" / f"{timestamp}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except (ValueError, OSError):
            pass
    if client is None:
        return None
    url = _WB_RAW.format(ts=timestamp)
    for attempt in range(3):
        try:
            _throttle()
            resp = client.get(url, follow_redirects=True,
                              headers={"Accept-Encoding": "gzip"})
            if resp.status_code == 200:
                raw = resp.json()
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(raw))
                return raw
        except Exception:  # noqa: BLE001
            pass
        if attempt < 2:                     # no dead sleep after the final failed attempt
            time.sleep(2 ** attempt)        # fixed backoff 1s/2s (no random)
    import warnings  # L2: a give-up is NOT silent (distinguish from "no snapshot")
    warnings.warn(f"symbology: snapshot fetch failed after retries for {timestamp}", stacklevel=2)
    return None


def snapshot_map(timestamp: str, *, cache_dir: str, client: Optional[httpx.Client] = None) -> dict[str, str]:
    raw = _raw_snapshot(timestamp, cache_dir=cache_dir, client=client)
    if not raw:
        return {}
    try:
        return build_cik_to_ticker(raw)
    except Exception:  # noqa: BLE001
        return {}


def snapshot_reverse(timestamp: str, *, cache_dir: str, client: Optional[httpx.Client] = None) -> dict[str, int]:
    raw = _raw_snapshot(timestamp, cache_dir=cache_dir, client=client)
    if not raw:
        return {}
    out: dict[str, int] = {}
    try:
        rows = raw.values()                 # guard: a non-dict truthy JSON must not raise
    except AttributeError:
        return {}
    for row in rows:
        try:
            tk = row.get("ticker")
            if not tk:                      # skip missing/None/empty tickers (no "NONE" keys)
                continue
            out[str(tk).upper()] = int(row["cik_str"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out


# Curated overrides for known bad historical resolutions (delisted multi-class names where the
# ≲2019 first-occurrence convention misfires). Extend as spot-checks find them. {cik10: ticker}.
_OVERRIDES: dict[str, str] = {}


def snapshot_multi(timestamp: str, *, cache_dir: str, client: Optional[httpx.Client] = None) -> set[str]:
    """Set of 10-digit CIKs with >1 ticker row in this snapshot (multi-share-class / units /
    preferred) — flags where the first-occurrence convention could misfire (C2). Never raises."""
    raw = _raw_snapshot(timestamp, cache_dir=cache_dir, client=client)
    if not raw:
        return set()
    counts: dict[str, int] = {}
    for row in raw.values():
        try:
            c = _norm_cik(row["cik_str"])
        except (KeyError, TypeError, ValueError):
            continue
        counts[c] = counts.get(c, 0) + 1
    return {c for c, n in counts.items() if n > 1}


class Symbology:
    """Point-in-time CIK<->ticker resolver. Loads the live map + CDX list once; memoizes
    per-snapshot maps. Reuse one instance across a backfill run."""

    def __init__(self, identity: str, *, cache_dir: str, client: Optional[httpx.Client] = None,
                 today: Optional[date] = None) -> None:
        self._cache_dir = cache_dir
        # C1 FIX: OWN a client when none is passed, so the snapshot-BLOB fetch path (delisted +
        # reverse resolution) actually works in the default `Symbology(identity, cache_dir=...)`
        # construction — `_raw_snapshot` returns None when client is None, which would silently
        # make every delisted/reverse resolution None. UA=identity satisfies both SEC + archive.org.
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=60.0, headers={"User-Agent": identity})
        self._live = load_cik_to_ticker(identity, cache_dir=str(Path(cache_dir) / "sec_tickers"),
                                        _today=today, _client=self._client)
        self._snapshots = cdx_snapshots(cache_dir=cache_dir, client=self._client, today=today)
        self._snap_cache: dict[str, dict[str, str]] = {}
        self._rev_cache: dict[str, dict[str, int]] = {}
        self._multi_cache: dict[str, set[str]] = {}
        self._overrides = dict(_OVERRIDES)
        self.disagreements: list[tuple[str, str, str]] = []
        self.low_confidence: list[tuple[str, str]] = []   # (cik10, ticker) delisted multi-class (C2)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Symbology":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _snapshot_map_for(self, as_of: date) -> dict[str, str]:
        ts = nearest_snapshot_before(self._snapshots, as_of)
        if ts is None:
            return {}
        if ts not in self._snap_cache:
            self._snap_cache[ts] = snapshot_map(ts, cache_dir=self._cache_dir, client=self._client)
        return self._snap_cache[ts]

    def _snapshot_multi_for(self, as_of: date) -> set[str]:
        ts = nearest_snapshot_before(self._snapshots, as_of)
        if ts is None:
            return set()
        if ts not in self._multi_cache:
            self._multi_cache[ts] = snapshot_multi(ts, cache_dir=self._cache_dir, client=self._client)
        return self._multi_cache[ts]

    def resolve_ticker(self, cik, as_of: date) -> Optional[str]:
        try:                                          # M2: never raise on a malformed CIK
            cik10 = _norm_cik(cik)
        except (TypeError, ValueError):
            return None
        if cik10 in self._overrides:
            return self._overrides[cik10]
        live_tkr = self._live.get(cik10)
        as_of_tkr = self._snapshot_map_for(as_of).get(cik10)
        if live_tkr is not None:                     # active issuer -> live wins (sidesteps §17 bug)
            if as_of_tkr is not None and as_of_tkr != live_tkr:
                self.disagreements.append((cik10, live_tkr, as_of_tkr))
            return live_tkr
        # delisted: archive-only (may be None). The ≲2019 convention bug can ONLY bite here (no
        # live cross-check possible), so flag low-confidence when the archived ticker looks like a
        # unit/warrant/preferred sibling OR the CIK had >1 ticker in that snapshot (C2). The
        # operator/coordinator spot-checks these + seeds `_OVERRIDES` before a verdict trusts them.
        if as_of_tkr is not None and (
                _UNIT_SUFFIX.match(as_of_tkr) or _PREF_SUFFIX.match(as_of_tkr)
                or cik10 in self._snapshot_multi_for(as_of)):
            self.low_confidence.append((cik10, as_of_tkr))
        return as_of_tkr

    def _reverse_map_for(self, as_of: date) -> dict[str, int]:
        ts = nearest_snapshot_before(self._snapshots, as_of)
        if ts is None:
            return {}
        if ts not in self._rev_cache:
            self._rev_cache[ts] = snapshot_reverse(ts, cache_dir=self._cache_dir, client=self._client)
        return self._rev_cache[ts]

    def resolve_cik(self, ticker: str, as_of: date) -> Optional[int]:
        # str() guards a truthy non-str ticker (honors the module's never-raises contract,
        # symmetric with the forward path's _norm_cik try/except).
        return self._reverse_map_for(as_of).get(str(ticker or "").upper())

    def resolve_ciks(self, tickers: list[str], as_of: date) -> tuple[dict[str, int], float]:
        resolved: dict[str, int] = {}
        for t in tickers:
            c = self.resolve_cik(t, as_of)
            if c is not None:
                resolved[str(t or "").upper()] = c
        rate = 1.0 - (len(resolved) / len(tickers)) if tickers else 0.0
        if rate > 0:                          # only warn on a real abstention, not at 0%
            import warnings
            warnings.warn(f"symbology: reverse abstention {rate:.1%} "
                          f"({len(tickers) - len(resolved)}/{len(tickers)} tickers unresolved)",
                          stacklevel=2)
        return resolved, rate
