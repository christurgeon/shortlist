"""Persistent HTTP-response cache for the FMP/Finnhub data boundary.

Caches parsed JSON payloads on disk (SQLite) keyed by (provider, endpoint, params)
with a per-bucket TTL, so a warm re-run makes no upstream calls and spares free-tier
quotas. Dependency-light leaf: stdlib and env.redact_secrets only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .env import redact_secrets

_SECRET_PARAMS = {"apikey", "token", "api_key"}
_DEFAULT_PATH = ".cache/http.sqlite"


def cache_key(provider: str, endpoint: str, params: dict) -> str:
    """Stable SHA-256 key for (provider, endpoint, params).

    Secret params are stripped, so key rotation does not fragment the cache. Raises
    TypeError on a non-JSON-serializable param. SHA-256 is used because hash() is
    salted per process.

    The `v1:` prefix is a manual schema epoch. Bump it whenever a source's `_get`
    changes the SHAPE of what it stores, or warm caches serve payloads the new
    parser cannot read."""
    clean = {k: v for k, v in params.items() if k.lower() not in _SECRET_PARAMS}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    raw = f"v1:{provider}:{endpoint}:{canon}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_cacheable(payload: object) -> bool:
    """A 2xx body can be a soft failure (FMP gating returns 200 with [] or {}; Finnhub
    returns error-keyed bodies). Storing one would poison the cache for the whole TTL."""
    if payload is None or payload == [] or payload == {} or payload == "":
        return False
    if isinstance(payload, dict) and any(k.lower() == "error" for k in payload):
        return False
    return True


# (provider, endpoint-path) -> bucket. Keyed on the pair, not path alone, because
# "quote" is used by both FMP and Finnhub.
_BUCKET_BY_KEY = {
    ("fmp", "quote"): "quote",
    ("fmp", "stock-price-change"): "quote",
    ("finnhub", "quote"): "quote",
    ("fmp", "ratios-ttm"): "fundamentals",
    ("fmp", "ratios"): "fundamentals",
    ("fmp", "key-metrics-ttm"): "fundamentals",
    ("fmp", "key-metrics"): "fundamentals",
    ("finnhub", "stock/metric"): "fundamentals",
    ("fmp", "price-target-consensus"): "analyst",
    ("fmp", "grades-consensus"): "analyst",
    ("fmp", "insider-trading/search"): "analyst",
    ("finnhub", "stock/recommendation"): "analyst",
    ("finnhub", "stock/insider-sentiment"): "analyst",
    ("finnhub", "company-news"): "quote",   # 6h — news has a short half-life
    ("finnhub", "stock/earnings"): "fundamentals",   # 1d — past surprises change on a print
    ("finnhub", "calendar/earnings"): "quote",       # 6h — the scheduled date can be revised
    ("fmp", "income-statement"): "statements",
    ("fmp", "balance-sheet-statement"): "statements",
    ("fmp", "cash-flow-statement"): "statements",
    ("fmp", "profile"): "profile",
    ("finnhub", "stock/profile2"): "profile",
}

_DEFAULT_TTLS = {
    "quote": 21600,         # 6h  — quotes, price-change, live metric
    "fundamentals": 86400,  # 1d  — ratios, key-metrics
    "analyst": 86400,       # 1d  — grades, targets, recommendations, insider-sentiment
    "statements": 604800,   # 7d  — income/balance/cashflow (change only on a filing)
    "profile": 604800,      # 7d  — company profile
    "default": 86400,       # 1d  — any unmapped endpoint
}

_warned_unmapped: set = set()


def ttl_for(provider: str, endpoint: str, ttl_config: dict) -> float:
    """Resolve the TTL (seconds) for an endpoint; ttl_config overrides per bucket.
    An unmapped (provider, endpoint) falls to the 1d default and is logged once so a
    new endpoint surfaces rather than silently inheriting a wrong TTL."""
    bucket = _BUCKET_BY_KEY.get((provider, endpoint))
    if bucket is None:
        bucket = "default"
        if (provider, endpoint) not in _warned_unmapped:
            _warned_unmapped.add((provider, endpoint))
            print(f"cache: unmapped endpoint {provider}/{endpoint} -> default TTL",
                  file=sys.stderr)
    return ttl_config.get(bucket, _DEFAULT_TTLS[bucket])


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
"""


class HttpCache:
    """Persistent SQLite cache for HTTP JSON payloads.

    Uses SQLite's default rollback journal, not WAL: the DB may sit on a networked
    filesystem, where WAL sidecar files do not work. The Lock, not
    `check_same_thread=False`, serializes access; the harness runs EdgarSource under
    `asyncio.to_thread`.

    The cache owns TTL resolution via `ttl_for()`; callers never pass a TTL.
    """

    def __init__(self, path: str, *, refresh: bool = False, ttls: dict | None = None):
        self._refresh = refresh
        self._ttls = ttls or {}
        self._lock = threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, key: str, now: float):
        # A DB fault or malformed stored row degrades to a miss, never a crash.
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT payload FROM cache WHERE key = ? AND expires_at > ?",
                    (key, now),
                ).fetchone()
            return json.loads(row["payload"]) if row else None
        except (sqlite3.Error, ValueError):
            return None

    def _write(self, key: str, payload: object, now: float, ttl: float) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO cache (key, payload, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (key, json.dumps(payload, separators=(",", ":")), now, now + ttl),
                )
                self._conn.commit()
        except sqlite3.Error:
            pass  # disk full / write failure is non-fatal — caller already has data

    def get_or_fetch(self, provider: str, endpoint: str, params: dict, fetcher):
        key = cache_key(provider, endpoint, params)
        now = time.time()
        if not self._refresh:
            hit = self._read(key, now)
            if hit is not None:
                return hit
        result = fetcher()  # raises on error -> never reach _write -> errors uncached
        if _is_cacheable(result):
            self._write(key, result, now, ttl_for(provider, endpoint, self._ttls))
        return result

    async def aget_or_fetch(self, provider: str, endpoint: str, params: dict, fetcher):
        """Async twin of get_or_fetch. SQLite calls are direct (not via to_thread)
        because a PK lookup is fast."""
        key = cache_key(provider, endpoint, params)
        now = time.time()
        if not self._refresh:
            hit = self._read(key, now)
            if hit is not None:
                return hit
        result = await fetcher()
        if _is_cacheable(result):
            self._write(key, result, now, ttl_for(provider, endpoint, self._ttls))
        return result

    def sweep(self) -> None:
        """Delete every expired row. Cheap via the expires_at index."""
        try:
            with self._lock:
                self._conn.execute("DELETE FROM cache WHERE expires_at <= ?",
                                   (time.time(),))
                self._conn.commit()
        except sqlite3.Error:
            pass

    def maybe_sweep(self, probability: float = 0.05) -> None:
        """Sweep with low probability per open, to bound file growth."""
        if random.random() < probability:
            self.sweep()


class NoOpCache:
    """Drop-in that never stores — always calls the fetcher. Used when caching is
    disabled (`--no-cache` / `cache.enabled: false`) or the DB can't be opened."""

    def get_or_fetch(self, provider, endpoint, params, fetcher):
        return fetcher()

    async def aget_or_fetch(self, provider, endpoint, params, fetcher):
        return await fetcher()

    def close(self):
        pass


_default_cache = None


def configure_default_cache(*, enabled: bool = True, refresh: bool = False,
                            path: str | None = None, ttls: dict | None = None) -> None:
    """Configure the process-global cache once at CLI startup. On any open failure,
    degrade to NoOpCache: a broken cache must never break a screen."""
    global _default_cache
    reset_default_cache()
    if not enabled:
        _default_cache = NoOpCache()
        return
    try:
        c = HttpCache(path or _DEFAULT_PATH, refresh=refresh, ttls=ttls)
        c.maybe_sweep()
        _default_cache = c
    except Exception as e:  # noqa: BLE001 — must never propagate to a screen
        print(f"cache: disabled (open failed: {redact_secrets(e)})", file=sys.stderr)
        _default_cache = NoOpCache()


def get_default_cache():
    """Return the configured global cache, lazily creating an on-by-default one if no
    CLI configured it (programmatic callers / the config-less harness CLI)."""
    global _default_cache
    if _default_cache is None:
        configure_default_cache()
    return _default_cache


def reset_default_cache() -> None:
    global _default_cache
    if _default_cache is not None:
        with contextlib.suppress(Exception):
            _default_cache.close()
    _default_cache = None
