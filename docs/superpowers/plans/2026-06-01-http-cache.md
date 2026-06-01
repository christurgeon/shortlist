# Persistent HTTP-response cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, TTL-driven SQLite cache at the FMP/Finnhub HTTP boundary (both the sync screener and async harness stacks) so repeated ad-hoc runs stop burning the free-tier daily quota.

**Architecture:** One leaf module `src/shortlist/cache.py` exposes `HttpCache` (SQLite, rollback journal, `check_same_thread=False` + a `threading.Lock`), a `NoOpCache`, pure helpers (`cache_key`, `_is_cacheable`, `ttl_for`), and a configured **process-global singleton** (`configure_default_cache`/`get_default_cache`/`reset_default_cache`). The cache **owns TTL resolution** (no call site passes a TTL). Each FMP/Finnhub `_get` wraps its existing body in a closure and delegates to `self._cache or get_default_cache()`. CLIs configure the global once at startup from `--no-cache`/`--refresh-cache` + `config["cache"]`.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`hashlib`/`json`/`time`/`threading`/`random`, `pytest`, `requests` (screener), `httpx`+`asyncio` (harness).

**Spec:** `docs/superpowers/specs/2026-06-01-http-cache-design.md`

---

## File Structure

- **New** `src/shortlist/cache.py` — the entire cache mechanism (one responsibility: cache HTTP JSON payloads by key+TTL). Self-contained; only stdlib + `env.redact_secrets`.
- **New** `tests/test_cache.py` — unit + integration tests.
- **New** `tests/conftest.py` — autouse fixture isolating the global cache per test.
- **Modify** `src/shortlist/providers/fmp.py`, `src/shortlist/providers/finnhub.py` — wrap `_get`, add `cache=None` ctor param.
- **Modify** `src/shortlist/data/sources.py` — wrap FMP + Finnhub `_get`, add `cache=None` ctor param.
- **Modify** `src/shortlist/screen.py`, `src/shortlist/data/cli.py` — add flags, call `configure_default_cache`.
- **Modify** `config.yaml` — add `cache:` block.
- **Docs** `docs/DATA_SOURCES.md`, `CLAUDE.md`, `HARNESS.md`, `README.md`.

> **Conventions:** private helpers are `_`-prefixed (see `_first`, `_pct`). Tests import
> privates directly (existing pattern in `tests/`). **Key API decision:** `get_or_fetch`
> and `aget_or_fetch` take **no `ttl` argument** — the cache resolves TTL itself via
> `ttl_for(provider, endpoint, self._ttls)`. TTL overrides come from `config.yaml` into
> `HttpCache(..., ttls=...)`. This keeps every call site clean.

---

### Task 1: Cache key + cacheability pure functions

**Files:**
- Create: `src/shortlist/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
import pytest
from shortlist.cache import cache_key, _is_cacheable


def test_cache_key_order_independent():
    a = cache_key("fmp", "ratios", {"symbol": "AAPL", "period": "annual", "limit": 5})
    b = cache_key("fmp", "ratios", {"limit": 5, "period": "annual", "symbol": "AAPL"})
    assert a == b


def test_cache_key_strips_secrets():
    with_key = cache_key("fmp", "quote", {"symbol": "AAPL", "apikey": "SECRET1"})
    rotated = cache_key("fmp", "quote", {"symbol": "AAPL", "apikey": "SECRET2"})
    no_key = cache_key("fmp", "quote", {"symbol": "AAPL"})
    assert with_key == rotated == no_key
    assert "SECRET1" not in with_key


def test_cache_key_distinguishes_provider_endpoint_symbol():
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("finnhub", "quote", {"symbol": "AAPL"})
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("fmp", "profile", {"symbol": "AAPL"})
    assert cache_key("fmp", "quote", {"symbol": "AAPL"}) != \
        cache_key("fmp", "quote", {"symbol": "MSFT"})


def test_cache_key_raises_on_non_json_param():
    with pytest.raises(TypeError):
        cache_key("fmp", "quote", {"symbol": object()})


@pytest.mark.parametrize("payload", [None, [], {}, "", {"error": "Not found"},
                                     {"Error": "x"}])
def test_not_cacheable(payload):
    assert _is_cacheable(payload) is False


@pytest.mark.parametrize("payload", [[{"c": 123}], {"c": 123}, {"data": []},
                                     {"errors": 0}])  # 'errors' != 'error' key
def test_cacheable(payload):
    assert _is_cacheable(payload) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.cache'`

- [ ] **Step 3: Implement the helpers**

```python
# src/shortlist/cache.py
from __future__ import annotations

import hashlib
import json

_SECRET_PARAMS = {"apikey", "token", "api_key"}


def cache_key(provider: str, endpoint: str, params: dict) -> str:
    """Stable SHA-256 key for (provider, endpoint, params). Secrets are stripped by
    name so key rotation doesn't fragment the cache and no secret enters the store.
    Raises TypeError on a non-JSON-serializable param (fail loud — the call site is
    wrong) rather than papering over it with default=str."""
    clean = {k: v for k, v in params.items() if k.lower() not in _SECRET_PARAMS}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    raw = f"v1:{provider}:{endpoint}:{canon}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_cacheable(payload: object) -> bool:
    """A 2xx body can still be a soft failure (FMP gating / no-coverage returns 200
    with [] or {}; Finnhub returns error-keyed bodies). Don't store those — they'd
    poison the cache for the whole TTL. Genuine successes here are always non-empty."""
    if payload is None or payload == [] or payload == {} or payload == "":
        return False
    if isinstance(payload, dict) and any(k.lower() == "error" for k in payload):
        return False
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/cache.py tests/test_cache.py
git commit -m "feat(cache): stable key + cacheability predicate"
```

---

### Task 2: TTL bucket map keyed by (provider, path)

**Files:**
- Modify: `src/shortlist/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cache.py
from shortlist.cache import ttl_for, _BUCKET_BY_KEY


def test_ttl_for_buckets():
    cfg = {}  # no overrides -> hardcoded defaults
    assert ttl_for("fmp", "quote", cfg) == 21600
    assert ttl_for("finnhub", "quote", cfg) == 21600
    assert ttl_for("fmp", "income-statement", cfg) == 604800
    assert ttl_for("fmp", "ratios-ttm", cfg) == 86400
    assert ttl_for("fmp", "unknown-endpoint", cfg) == 86400  # default bucket


def test_ttl_for_respects_config_override():
    cfg = {"quote": 60, "statements": 120}
    assert ttl_for("fmp", "quote", cfg) == 60
    assert ttl_for("fmp", "income-statement", cfg) == 120


def test_bucket_map_covers_all_live_endpoints():
    """Every (provider, path) emitted by the four wrapped _get call sites must be in
    the bucket map, or it silently demotes to the 1d default."""
    harness = {
        ("fmp", "profile"), ("fmp", "quote"), ("fmp", "ratios-ttm"),
        ("fmp", "ratios"), ("fmp", "key-metrics-ttm"), ("fmp", "key-metrics"),
        ("fmp", "income-statement"), ("fmp", "balance-sheet-statement"),
        ("fmp", "cash-flow-statement"), ("fmp", "price-target-consensus"),
        ("fmp", "grades-consensus"), ("fmp", "insider-trading/search"),
        ("fmp", "stock-price-change"),
        ("finnhub", "stock/profile2"), ("finnhub", "quote"),
        ("finnhub", "stock/metric"), ("finnhub", "stock/recommendation"),
        ("finnhub", "stock/insider-sentiment"),
    }
    screener = {
        ("fmp", "quote"), ("fmp", "ratios-ttm"), ("fmp", "key-metrics-ttm"),
        ("fmp", "price-target-consensus"), ("fmp", "grades-consensus"),
        ("fmp", "stock-price-change"),
        ("finnhub", "quote"), ("finnhub", "stock/metric"),
        ("finnhub", "stock/recommendation"), ("finnhub", "stock/insider-sentiment"),
    }
    for pk in harness | screener:
        assert pk in _BUCKET_BY_KEY, f"{pk} missing from bucket map"
```

> **Implementer note:** confirm the path strings against the live call sites before
> trusting the sets above:
> `grep -nE '"(profile|quote|ratios|ratios-ttm|key-metrics|key-metrics-ttm|income-statement|balance-sheet-statement|cash-flow-statement|price-target-consensus|grades-consensus|insider-trading/search|stock-price-change|stock/profile2|stock/metric|stock/recommendation|stock/insider-sentiment)"' src/shortlist/providers/fmp.py src/shortlist/providers/finnhub.py src/shortlist/data/sources.py`.
> They were verified against the spec; if one differs, fix the set AND the map together.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py -k ttl_for -q`
Expected: FAIL — `ImportError: cannot import name 'ttl_for'`

- [ ] **Step 3: Implement the bucket map + ttl_for**

```python
# add to src/shortlist/cache.py
import sys

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
    ("fmp", "income-statement"): "statements",
    ("fmp", "balance-sheet-statement"): "statements",
    ("fmp", "cash-flow-statement"): "statements",
    ("fmp", "profile"): "profile",
    ("finnhub", "stock/profile2"): "profile",
}

_DEFAULT_TTLS = {
    "quote": 21600, "fundamentals": 86400, "analyst": 86400,
    "statements": 604800, "profile": 604800, "default": 86400,
}

_warned_unmapped: set = set()


def ttl_for(provider: str, endpoint: str, ttl_config: dict) -> float:
    """Resolve the TTL (seconds) for an endpoint; ttl_config overrides per bucket."""
    bucket = _BUCKET_BY_KEY.get((provider, endpoint))
    if bucket is None:
        bucket = "default"
        if (provider, endpoint) not in _warned_unmapped:
            _warned_unmapped.add((provider, endpoint))
            print(f"cache: unmapped endpoint {provider}/{endpoint} -> default TTL",
                  file=sys.stderr)
    return ttl_config.get(bucket, _DEFAULT_TTLS[bucket])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/cache.py tests/test_cache.py
git commit -m "feat(cache): (provider,path) TTL bucket map + coverage test"
```

---

### Task 3: HttpCache — store, predicate-gated writes, persistence, TTL ownership

**Files:**
- Modify: `src/shortlist/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cache.py
from shortlist.cache import HttpCache


def test_caches_within_ttl(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))  # default quote TTL = 6h
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": 1}]

    r1 = cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    r2 = cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert r1 == r2 == [{"c": 1}]
    assert calls["n"] == 1
    cache.close()


def test_ttl_expiry_refetches(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"), ttls={"quote": -1})  # already expired
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": calls["n"]}]

    cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    cache.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert calls["n"] == 2
    cache.close()


def test_empty_payload_not_stored(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return []  # soft failure

    cache.get_or_fetch("fmp", "grades-consensus", {"symbol": "X"}, fetch)
    cache.get_or_fetch("fmp", "grades-consensus", {"symbol": "X"}, fetch)
    assert calls["n"] == 2  # empty never cached -> re-fetched
    cache.close()


def test_errors_never_cached(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cache.get_or_fetch("fmp", "quote", {"symbol": "X"}, fetch)
    assert calls["n"] == 2
    cache.close()


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "c.sqlite")
    c1 = HttpCache(path)
    c1.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, lambda: [{"c": 7}])
    c1.close()  # model an ad-hoc process exiting

    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"c": 999}]

    c2 = HttpCache(path)  # a fresh "process"
    r = c2.get_or_fetch("fmp", "quote", {"symbol": "AAPL"}, fetch)
    assert r == [{"c": 7}] and calls["n"] == 0  # served from persisted DB
    c2.close()


def test_refresh_mode_bypasses_read_but_writes(tmp_path):
    path = str(tmp_path / "c.sqlite")
    seed = HttpCache(path)
    seed.get_or_fetch("fmp", "quote", {"symbol": "A"}, lambda: [{"v": 1}])
    seed.close()

    cache = HttpCache(path, refresh=True)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"v": 2}]

    assert cache.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch) == [{"v": 2}]
    assert calls["n"] == 1  # bypassed the stale read
    cache.close()

    after = HttpCache(path)  # refresh wrote it through
    assert after.get_or_fetch("fmp", "quote", {"symbol": "A"},
                              lambda: [{"v": 3}]) == [{"v": 2}]
    after.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py -k "caches_within or ttl_expiry or empty_payload or errors_never or persists or refresh_mode" -q`
Expected: FAIL — `ImportError: cannot import name 'HttpCache'`

- [ ] **Step 3: Implement HttpCache**

```python
# add to src/shortlist/cache.py
import sqlite3
import threading
import time
from pathlib import Path

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
    """Persistent SQLite cache for HTTP JSON payloads. Rollback journal (the default)
    — the process is short-lived/ad-hoc and may sit on NFS, so WAL's sidecar files and
    network-FS limits are a poor fit. check_same_thread=False + a Lock makes the single
    connection safe even if a future caller touches it from a worker thread (the Lock,
    not check_same_thread, is what serializes). The cache OWNS TTL resolution via
    ttl_for(); callers never pass a TTL."""

    def __init__(self, path: str, *, refresh: bool = False, ttls: dict | None = None):
        self._refresh = refresh
        self._ttls = ttls or {}
        self._lock = threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, key: str, now: float):
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM cache WHERE key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

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

    def sweep(self) -> None:
        try:
            with self._lock:
                self._conn.execute("DELETE FROM cache WHERE expires_at <= ?",
                                   (time.time(),))
                self._conn.commit()
        except sqlite3.Error:
            pass

    def maybe_sweep(self, probability: float = 0.05) -> None:
        import random
        if random.random() < probability:
            self.sweep()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/cache.py tests/test_cache.py
git commit -m "feat(cache): HttpCache store with TTL ownership, persistence, refresh"
```

---

### Task 4: Async entry point (`aget_or_fetch`)

**Files:**
- Modify: `src/shortlist/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cache.py
import asyncio


def test_aget_or_fetch_caches_and_runs_concurrently(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"))
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0)
        return [{"c": 1}]

    async def scenario():
        r1 = await cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
        r2, r3 = await asyncio.gather(
            cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch),
            cache.aget_or_fetch("fmp", "quote", {"symbol": "A"}, fetch),
        )
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(scenario())
    assert r1 == r2 == r3 == [{"c": 1}]
    assert calls["n"] == 1
    cache.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py::test_aget_or_fetch_caches_and_runs_concurrently -q`
Expected: FAIL — `AttributeError: 'HttpCache' object has no attribute 'aget_or_fetch'`

- [ ] **Step 3: Implement the async variant**

```python
# add to HttpCache in src/shortlist/cache.py
    async def aget_or_fetch(self, provider: str, endpoint: str, params: dict, fetcher):
        """Async twin of get_or_fetch. SQLite calls are made directly (not via
        asyncio.to_thread): a PK lookup is tens of microseconds; only the awaited fetch
        on a miss genuinely blocks, and it's already async."""
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/cache.py tests/test_cache.py
git commit -m "feat(cache): async aget_or_fetch"
```

---

### Task 5: NoOpCache, global singleton, corrupt-DB fallback

**Files:**
- Modify: `src/shortlist/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cache.py
from shortlist.cache import (NoOpCache, configure_default_cache,
                             get_default_cache, reset_default_cache)


def test_noopcache_always_fetches():
    c = NoOpCache()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [1]

    c.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
    c.get_or_fetch("fmp", "quote", {"symbol": "A"}, fetch)
    assert calls["n"] == 2


def test_configure_disabled_returns_noop():
    configure_default_cache(enabled=False)
    assert isinstance(get_default_cache(), NoOpCache)
    reset_default_cache()


def test_configure_enabled_returns_httpcache(tmp_path):
    configure_default_cache(enabled=True, path=str(tmp_path / "c.sqlite"))
    assert isinstance(get_default_cache(), HttpCache)
    reset_default_cache()


def test_get_default_cache_unconfigured_is_enabled(tmp_path, monkeypatch):
    reset_default_cache()
    monkeypatch.chdir(tmp_path)  # default .cache/ lands in tmp
    assert isinstance(get_default_cache(), HttpCache)
    reset_default_cache()


def test_corrupt_db_falls_back_to_noop(tmp_path):
    bad = tmp_path / "c.sqlite"
    bad.write_text("not a sqlite database")
    configure_default_cache(enabled=True, path=str(bad))
    assert isinstance(get_default_cache(), NoOpCache)
    reset_default_cache()


def test_sweep_removes_expired(tmp_path):
    cache = HttpCache(str(tmp_path / "c.sqlite"), ttls={"quote": -1, "profile": 1000})
    cache.get_or_fetch("fmp", "quote", {"symbol": "A"}, lambda: [1])     # expired
    cache.get_or_fetch("fmp", "profile", {"symbol": "B"}, lambda: [2])   # live
    cache.sweep()
    with cache._lock:
        n = cache._conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    assert n == 1
    cache.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py -k "noop or configure or corrupt or sweep or unconfigured" -q`
Expected: FAIL — `ImportError: cannot import name 'NoOpCache'`

> **Note:** `test_corrupt_db_falls_back_to_noop` must reliably trigger an open failure.
> Opening a non-SQLite file succeeds lazily; the failure surfaces on `executescript`.
> Since `__init__` runs `executescript`, the corrupt file raises there and
> `configure_default_cache`'s try/except catches it. Good.

- [ ] **Step 3: Implement NoOpCache + singleton + fallback**

```python
# add to src/shortlist/cache.py
from .env import redact_secrets

_DEFAULT_PATH = ".cache/http.sqlite"


class NoOpCache:
    """Drop-in that never stores — always calls the fetcher. Used when caching is
    disabled or the DB can't be opened."""

    def get_or_fetch(self, provider, endpoint, params, fetcher):
        return fetcher()

    async def aget_or_fetch(self, provider, endpoint, params, fetcher):
        return await fetcher()

    def close(self):
        pass


_default_cache = None


def configure_default_cache(*, enabled: bool = True, refresh: bool = False,
                            path: str | None = None, ttls: dict | None = None) -> None:
    """Configure the process-global cache once at CLI startup. On any open failure
    (corrupt/unreadable DB, NFS without locking), degrade to NoOpCache — caching is an
    optimization, never a hard dependency."""
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
        try:
            _default_cache.close()
        except Exception:  # noqa: BLE001
            pass
    _default_cache = None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cache.py -q`
Expected: PASS (entire file)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/cache.py tests/test_cache.py
git commit -m "feat(cache): NoOpCache, global singleton, corrupt-DB fallback"
```

---

### Task 6: Test isolation fixture (conftest)

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the fixture**

```python
# tests/conftest.py
import pytest

from shortlist import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolate_http_cache():
    """Keep the on-by-default global cache out of the real repo-root .cache/ during
    tests and prevent cached rows leaking between tests (which would break call-count
    assertions). Tests that want a real cache build their own HttpCache(tmp_path)."""
    cache_mod.reset_default_cache()
    cache_mod.configure_default_cache(enabled=False)
    yield
    cache_mod.reset_default_cache()
```

- [ ] **Step 2: Verify suite passes and creates no repo-root `.cache/`**

Run: `rm -rf .cache && uv run pytest tests/test_cache.py -q && (ls .cache >/dev/null 2>&1 && echo LEAKED || echo clean)`
Expected: PASS then `clean`. (Cache tests use `tmp_path` DBs; the autouse fixture
disables the global. Tests that call `configure_default_cache(enabled=True, path=tmp...)`
override the fixture for their body and reset at the end.)

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(cache): autouse fixture isolating the global cache"
```

---

### Task 7: Wire the screener providers (sync)

**Files:**
- Modify: `src/shortlist/providers/fmp.py` (imports; `__init__` 32-51; `_get` 53-65)
- Modify: `src/shortlist/providers/finnhub.py` (imports; `__init__` 22-26; `_get` 28-32)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing wired test**

```python
# add to tests/test_cache.py
def test_fmp_provider_get_uses_cache(tmp_path):
    from shortlist.providers.fmp import FMPProvider

    real = HttpCache(str(tmp_path / "c.sqlite"))
    p = FMPProvider.__new__(FMPProvider)  # bypass __init__/key requirement
    p.key = "test"
    p.timeout = 15
    p.max_retries = 2
    p._cache = real

    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"price": 1}]

    class FakeSession:
        def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    p._session = FakeSession()

    assert p._get("quote", symbol="AAPL") == [{"price": 1}]
    assert p._get("quote", symbol="AAPL") == [{"price": 1}]
    assert calls["n"] == 1  # second call cached
    real.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py::test_fmp_provider_get_uses_cache -q`
Expected: FAIL — `AttributeError: ... '_cache'` or `calls["n"] == 2`

- [ ] **Step 3: Wire FMPProvider**

Add the import near the top of `src/shortlist/providers/fmp.py` (after the existing
relative imports):

```python
from ..cache import get_default_cache
```

Add `cache=None` to `__init__` and store it:

```python
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 15,
        *,
        fetch_insider: bool = False,
        max_retries: int = 2,
        cache=None,
    ):
        ...  # unchanged body
        self.max_retries = max_retries
        self._cache = cache
        self._session = requests.Session()
        self._spy_6m: Optional[float] = None
```

Wrap `_get`, keeping the retry body inside the closure:

```python
    def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key
        url = f"{BASE}/{path}"

        def fetch():
            for attempt in range(self.max_retries + 1):
                r = self._session.get(url, params=params, timeout=self.timeout)
                if r.status_code != 429 or attempt == self.max_retries:
                    r.raise_for_status()
                    return r.json()
                time.sleep(_retry_after_seconds(r, attempt))
            raise AssertionError("unreachable")

        cache = self._cache or get_default_cache()
        return cache.get_or_fetch("fmp", path, params, fetch)
```

- [ ] **Step 3b: Wire FinnhubProvider** (`src/shortlist/providers/finnhub.py`)

```python
from ..cache import get_default_cache

    def __init__(self, api_key: Optional[str] = None, timeout: int = 15, *, cache=None):
        self.key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        self.timeout = timeout
        self._cache = cache

    def _get(self, path: str, **params: Any) -> Any:
        params["token"] = self.key

        def fetch():
            r = requests.get(f"{BASE}/{path}", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()

        cache = self._cache or get_default_cache()
        return cache.get_or_fetch("finnhub", path, params, fetch)
```

- [ ] **Step 4: Run to verify pass (and existing provider tests still green)**

Run: `uv run pytest tests/test_cache.py tests/test_fmp_provider.py tests/test_finnhub_provider.py -q`
Expected: PASS. If a `__new__`-based fixture errors on `_cache`, set `p._cache = None`
in that test's helper — the `self._cache or get_default_cache()` fallback then uses the
test-disabled global (a `NoOpCache`), so behavior is unchanged for those tests.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/providers/fmp.py src/shortlist/providers/finnhub.py tests/test_cache.py
git commit -m "feat(cache): wire screener FMP/Finnhub providers"
```

---

### Task 8: Wire the harness sources (async)

**Files:**
- Modify: `src/shortlist/data/sources.py` (import; FMPSource `__init__` ~43-48 & `_get` ~53-57; FinnhubSource `__init__` ~179-185 & `_get` ~189-193)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing wired test**

```python
# add to tests/test_cache.py
def test_fmp_source_get_uses_cache(tmp_path):
    from shortlist.data.sources import FMPSource

    real = HttpCache(str(tmp_path / "c.sqlite"))
    s = FMPSource.__new__(FMPSource)
    s.key = "test"
    s._cache = real
    s.BASE = "https://example.invalid/stable"

    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"x": 1}]

    class FakeClient:
        async def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    s._client = FakeClient()

    async def scenario():
        return await s._get("quote", symbol="AAPL"), await s._get("quote", symbol="AAPL")

    a, b = asyncio.run(scenario())
    assert a == b == [{"x": 1}]
    assert calls["n"] == 1
    real.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py::test_fmp_source_get_uses_cache -q`
Expected: FAIL — `AttributeError: ... '_cache'` or `calls["n"] == 2`

- [ ] **Step 3: Wire FMPSource and FinnhubSource**

Add near the existing imports in `src/shortlist/data/sources.py`:

```python
from ..cache import get_default_cache
```

FMPSource:

```python
    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *, cache=None):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise RuntimeError("FMP_API_KEY not set")
        import httpx
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = cache

    async def _get(self, path: str, **params: Any) -> Any:
        params["apikey"] = self.key

        async def fetch():
            r = await self._client.get(f"{self.BASE}/{path}", params=params)
            r.raise_for_status()
            return r.json()

        cache = self._cache or get_default_cache()
        return await cache.aget_or_fetch("fmp", path, params, fetch)
```

FinnhubSource:

```python
    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *, cache=None):
        self.key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.key:
            raise RuntimeError("FINNHUB_API_KEY not set")
        import httpx
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = cache

    async def _get(self, path: str, **params: Any) -> Any:
        params["token"] = self.key

        async def fetch():
            r = await self._client.get(f"{self.BASE}/{path}", params=params)
            r.raise_for_status()
            return r.json()

        cache = self._cache or get_default_cache()
        return await cache.aget_or_fetch("finnhub", path, params, fetch)
```

- [ ] **Step 4: Run to verify pass (and harness tests still green)**

Run: `uv run pytest tests/test_cache.py tests/test_harness.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_cache.py
git commit -m "feat(cache): wire harness FMP/Finnhub sources (async)"
```

---

### Task 9: CLI flags + configure_default_cache at startup

**Files:**
- Modify: `src/shortlist/screen.py` (`build_arg_parser` ~148-164; `main` after config load ~172)
- Modify: `src/shortlist/data/cli.py` (`main` ~12-27)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cache.py
def test_screen_cli_no_cache_flag_disables(monkeypatch, tmp_path):
    import shortlist.screen as screen
    captured = {}

    def fake_run(tickers, providers, config):
        captured["t"] = type(get_default_cache()).__name__
        return []

    monkeypatch.setattr(screen, "run", fake_run)
    monkeypatch.setattr(screen, "_print_coverage_notes", lambda c: None)
    monkeypatch.setattr(screen, "_print_table", lambda c: None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("providers: [mock]\n")
    screen.main(["--tickers", "AAPL", "--config", str(cfg), "--no-cache"])
    assert captured["t"] == "NoOpCache"
    reset_default_cache()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cache.py::test_screen_cli_no_cache_flag_disables -q`
Expected: FAIL — `unrecognized arguments: --no-cache`

- [ ] **Step 3: Add flags + configure call to `screen.py`**

In `build_arg_parser`, after the `--refresh` argument:

```python
    ap.add_argument("--no-cache", action="store_true",
                    help="disable the on-disk HTTP cache for this run")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="bypass cached HTTP responses and repopulate them")
```

In `main`, immediately after `config = yaml.safe_load(Path(args.config).read_text())`:

```python
    from .cache import configure_default_cache
    cache_cfg = config.get("cache", {})
    configure_default_cache(
        enabled=(not args.no_cache) and cache_cfg.get("enabled", True),
        refresh=args.refresh_cache,
        path=cache_cfg.get("path"),
        ttls=cache_cfg.get("ttl"),
    )
```

- [ ] **Step 3b: Add flags + configure call to `data/cli.py`**

After the existing `ap.add_argument("--print", ...)` line:

```python
    ap.add_argument("--no-cache", action="store_true",
                    help="disable the on-disk HTTP cache for this run")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="bypass cached HTTP responses and repopulate them")
```

After `load_env()`:

```python
    from ..cache import configure_default_cache
    configure_default_cache(enabled=not args.no_cache, refresh=args.refresh_cache)
```

> The harness CLI loads no `config.yaml`, so it uses hardcoded TTL defaults — correct;
> the spec's on-by-default behavior holds without a config block.

- [ ] **Step 4: Run to verify pass + full suite**

Run: `uv run pytest -q`
Expected: PASS (whole suite)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/screen.py src/shortlist/data/cli.py tests/test_cache.py
git commit -m "feat(cache): --no-cache/--refresh-cache flags; configure at startup"
```

---

### Task 10: config.yaml cache block

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Add the block** (near the other top-level keys, e.g. after `harness_sources`)

```yaml
cache:
  enabled: true
  path: .cache/http.sqlite
  ttl:
    quote: 21600         # 6h  — quotes, price-change, live metric
    fundamentals: 86400  # 1d  — ratios, key-metrics
    analyst: 86400       # 1d  — grades, targets, recommendations, insider-sentiment
    statements: 604800   # 7d  — income/balance/cashflow (change only on a filing)
    profile: 604800      # 7d  — company profile
    default: 86400       # 1d  — any unmapped endpoint
```

- [ ] **Step 2: Verify config parses and demo run is unaffected**

Run: `uv run shortlist --demo --json >/dev/null && echo OK`
Expected: `OK` (demo uses the mock source — no live HTTP).

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "feat(cache): config block with per-bucket TTLs"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/DATA_SOURCES.md` (§6), `CLAUDE.md`, `HARNESS.md`, `README.md`

- [ ] **Step 1: `docs/DATA_SOURCES.md` §6** — change status from "FUTURE WORK / not
  started" to "**shipped 2026-06**". Replace the "Design sketch" bullets with an "As
  built" summary: module `src/shortlist/cache.py`; SQLite rollback-journal store at
  `.cache/http.sqlite`; `(provider,path)` TTL buckets configurable under
  `config.yaml: cache.ttl`; on by default; `--no-cache`/`--refresh-cache`; payload-level
  cacheability predicate (empties/errors never stored); EDGAR/Yahoo/FINRA out of scope.
  Mark the Acceptance criteria met, pointing to `tests/test_cache.py`.

- [ ] **Step 2: `CLAUDE.md`** — in "Scale / rate limits (the honest catch)", change the
  "caching … specced as a future work stream" sentence to note caching now exists
  (on-by-default SQLite HTTP cache for FMP/Finnhub on both stacks; `--no-cache` /
  `--refresh-cache`; TTLs in `config.yaml:cache`). Add a short "Caching" subsection
  pointing to `cache.py` and the spec, noting the `v1→v2` key-prefix bump discipline
  when a `_get`/normalizer shape changes.

- [ ] **Step 3: `HARNESS.md`** — document the two new flags on `shortlist-harness`, the
  on-by-default behavior, that Yahoo/FINRA self-cache and EDGAR is uncached.

- [ ] **Step 4: `README.md`** — add `--no-cache`/`--refresh-cache` to the screener flag
  list if one exists; one sentence that repeated runs are now cheap.

- [ ] **Step 5: Commit**

```bash
git add docs/DATA_SOURCES.md CLAUDE.md HARNESS.md README.md
git commit -m "docs(cache): mark caching layer shipped; document flags/config"
```

---

### Task 12: Full verification + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `rm -rf .cache && uv run pytest -q && (ls .cache >/dev/null 2>&1 && echo LEAK || echo clean)`
Expected: all green, then `clean` (no repo-root `.cache/` from the suite).

- [ ] **Step 2: Live cold→warm smoke (needs FMP/FINNHUB keys in `.env`)**

```bash
rm -rf .cache
uv run shortlist --engine harness --tickers AAPL --json >/tmp/a.json   # cold
test -f .cache/http.sqlite && echo "cache created"
uv run shortlist --engine harness --tickers AAPL --json >/tmp/b.json   # warm
diff <(jq -S . /tmp/a.json) <(jq -S . /tmp/b.json) && echo "warm == cold"
sqlite3 .cache/http.sqlite "select count(*) from cache;"               # > 0
sqlite3 .cache/http.sqlite "select payload from cache;" | grep -i apikey \
  && echo LEAK || echo "no secret in store"
```

Expected: cold creates the DB; warm is byte-identical and faster; row count > 0; no
secret in the store.

- [ ] **Step 3: Flag behavior**

```bash
uv run shortlist --engine harness --tickers AAPL --no-cache --json >/dev/null
uv run shortlist --engine harness --tickers AAPL --refresh-cache --json >/dev/null
echo "flags OK"
```

- [ ] **Step 4: requesting-code-review**, then open the PR.
```
