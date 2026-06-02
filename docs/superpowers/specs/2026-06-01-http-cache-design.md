# Persistent HTTP-response cache — design (v2)

**Date:** 2026-06-01
**Status:** approved (brainstorm) + adversarially reviewed; pending implementation
**Implements:** `docs/DATA_SOURCES.md` §6 ("Scale hardening — the caching layer")
**Cross-refs:** `ASSESSMENT_GAPS.md` §2.3, §4; `CLAUDE.md` ("Scale / rate limits")

> **v2 changelog** (after two adversarial reviews): the "never cache errors" rule is
> now a payload-level **cacheability predicate** (200-OK soft failures are the real
> failure mode, not non-2xx); journal mode switched from WAL to the **default rollback
> journal** (ad-hoc short-lived process; NFS-safe; no sidecar litter); connection opens
> `check_same_thread=False`; TTL buckets keyed by **(provider, path)**; integration is a
> **configured process-global singleton** rather than build-path constructor injection;
> distinct **`--no-cache` / `--refresh-cache`** flags (no overload of `--refresh`); test
> isolation via an autouse fixture. See "Adversarial-review resolutions" at the end.

## Problem

Free API tiers cap throughput. FMP's free plan allows ~250 calls/day and ~5/min;
the harness spends ~13 FMP calls/ticker, the screener ~8 — so a handful of names
exhausts the **daily** quota and a 5-ticker burst trips the **per-minute** throttle,
both surfacing as `429`s. Retry/backoff (already shipped) makes the failure honest
but cannot manufacture quota. The only thing that makes **repeated** runs cheap is
not re-fetching what we already pulled. The app runs **ad-hoc** (CLI, not a daemon),
so the cache must **persist on disk between invocations**.

## Scope

**In scope:** FMP and Finnhub, across **both** stacks — the four HTTP-JSON
chokepoints:

| Stack    | Provider/Source   | Method                            | Sync/Async |
|----------|-------------------|-----------------------------------|------------|
| screener | `FMPProvider`     | `providers/fmp.py:_get`           | sync (`requests`) |
| screener | `FinnhubProvider` | `providers/finnhub.py:_get`       | sync (`requests`) |
| harness  | `FMPSource`       | `data/sources.py:_get` (FMP)      | async (`httpx`) |
| harness  | `FinnhubSource`   | `data/sources.py:_get` (Finnhub)  | async (`httpx`) |

**Out of scope:** `YahooSource` (already day-cached), `FinraSource` (already
settlement-date-cached, one bulk fetch/run), EDGAR both stacks (routes through
`edgartools`, free, rate-limited not quota-capped). All untouched.

## Architecture

One new leaf module: `src/shortlist/cache.py`, exposing an `HttpCache` class backed
by **SQLite**.

Why SQLite over flat JSON files: atomic writes (single-statement `INSERT OR REPLACE`),
indexed TTL queries, and safe concurrent readers/writers without a daemon. Persistence
is inherent: the DB is a single file (`.cache/http.sqlite`, gitignored), so it survives
between ad-hoc CLI invocations.

### Journal mode (rollback, NOT WAL)

The process is **short-lived and ad-hoc**: open once, do point reads plus a handful of
writes, exit. It has no long-lived concurrent readers that need WAL's
reader-doesn't-block-writer property. WAL has real downsides here: it leaves `-wal`/`-shm`
sidecar files that only checkpoint cleanly on a graceful last-connection close (an
interactive `Ctrl-C` leaves them growing), and **WAL does not work on networked
filesystems** (NFS/SMB) — a real risk since the cache lives in the working dir. So we
use SQLite's **default rollback journal** plus a generous `busy_timeout`:

```python
conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
conn.execute("PRAGMA busy_timeout=5000")   # wait, don't instantly SQLITE_BUSY
conn.row_factory = sqlite3.Row
```

- **`check_same_thread=False`**: the harness runs `EdgarSource` under
  `asyncio.to_thread` (worker threads). EDGAR is out of cache scope, so the cache is
  in practice only touched from the main loop thread — but a lazily-created singleton
  could be born on any thread, and SQLite's default `check_same_thread=True` would then
  raise `ProgrammingError` from any other thread. `False` + the lock below is the robust
  pairing; the lock (not `check_same_thread`) is what serializes access.
- **`threading.Lock`** guards every read/write of the single per-process connection. On
  the async harness path all tasks share one event-loop thread, so the lock is
  uncontended there (the critical section is synchronous and non-awaiting); it is
  genuinely load-bearing only if a future caller touches the cache from a worker thread.
  Documented as such so nobody "optimizes it away."
- Default `synchronous=FULL` is kept (write volume is tiny relative to the dominating
  HTTP fetches; corruption-safety for free). A corrupt DB is still survivable — see
  Failure modes.

### Two entry points, one store

- `get_or_fetch(provider, endpoint, params, fetcher)` — **sync**, screener providers.
- `aget_or_fetch(provider, endpoint, params, async_fetcher)` — **async**, harness sources.

Both share one sync SQLite `_get`/`_put`. The async variant calls SQLite **directly,
not via `asyncio.to_thread`**: a primary-key lookup is tens of microseconds, and the
only operation that blocks the loop — the HTTP fetch on a miss — is already async.

### Schema

```sql
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,   -- sha256 hex of canonical (provider+endpoint+params)
    payload    TEXT NOT NULL,      -- JSON text of the parsed response, verbatim
    created_at REAL NOT NULL,      -- epoch seconds (time.time())
    expires_at REAL NOT NULL       -- epoch seconds
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
```

`WITHOUT ROWID`: always looked up by the string PK. Point reads filter `expires_at` in
the predicate, so an expired entry is simply not returned and is overwritten on the next
fetch — no delete-on-read. The index serves the probabilistic sweep.

## The wrap

Each `_get` keeps its existing body inside a closure and delegates to the cache,
resolving the cache lazily so a `None` instance falls back to the configured global:

```python
# screener FMPProvider._get  (providers/fmp.py)
def _get(self, path, **params):
    params["apikey"] = self.key
    def fetch():
        ...  # existing 429-retry loop, raise_for_status(), return r.json()
    cache = self._cache or get_default_cache()
    return cache.get_or_fetch("fmp", path, params, fetch)
```

```python
# harness FMPSource._get  (data/sources.py)
async def _get(self, path, **params):
    params["apikey"] = self.key
    async def fetch():
        r = await self._client.get(f"{self.BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()
    cache = self._cache or get_default_cache()
    return await cache.aget_or_fetch("fmp", path, params, fetch)
```

`get_default_cache()` **never returns `None`** — when caching is disabled it returns a
`NoOpCache` whose `get_or_fetch`/`aget_or_fetch` just call the fetcher. So the call site
is uniform and the `__new__`-based provider unit tests (which never set `self._cache`)
still work: `self._cache` is `None` → falls to the configured global (a `NoOpCache`
under the test fixture).

## Cacheability — the real "never cache errors" rule

`raise_for_status()` only catches non-2xx. The actual failure mode is a **200-OK with a
degraded body**: FMP free-tier gating and no-coverage symbols return an empty list `[]`
or `{}`; Finnhub returns `{}`-ish or error-keyed bodies. The entire downstream layer is
written to tolerate these (`_first(...)→None`, `isinstance(x, list) and x`, `.get("metric",
{})`). They never raise, so a naive cache would store a transient empty and serve it for
the full TTL — strictly worse than re-fetching.

So `_put` (and thus `get_or_fetch`) applies a **cacheability predicate** before writing:

```python
def _is_cacheable(payload) -> bool:
    if payload is None or payload == [] or payload == {} or payload == "":
        return False
    if isinstance(payload, dict) and any(
        k.lower() == "error" for k in payload):
        return False
    return True
```

A non-cacheable payload is returned to the caller (correct degraded data) but **not
stored**, so the next run re-fetches — no quota saved on empties, but no poisoning, and
no regression versus today. This also resolves the double-fetch concern (two concurrent
misses where one returns empty): the empty is never written. A genuinely-successful
response is always non-empty here, so this never skips a real hit.

**"Hit indistinguishable from live"**: the cache stores and returns the parsed JSON
object the source would have produced — no `_cached` marker, no different type. The
`coverage()` layer cannot tell a hit from a live fetch.

## Cache key & secret hygiene

```python
def cache_key(provider, endpoint, params):
    clean = {k: v for k, v in params.items()
             if k.lower() not in {"apikey", "token", "api_key"}}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"))  # raises on non-JSON
    raw = f"v1:{provider}:{endpoint}:{canon}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

- **Secrets stripped by name** (the names `env.py:redact_secrets` knows) — correctness
  (key-rotation invariance; same data regardless of which key paid) and hygiene (no
  secret in a persisted artifact; the stored *payload* is the response body, never the URL).
- **`sort_keys=True`** → order-independent; **`separators`** strips whitespace.
- **No `default=str`** — params here are all JSON-native; if a non-serializable value
  ever appears, `json.dumps` raising loudly is correct (the call site is wrong). `symbol`
  is already inside `params`, so it is not passed separately (removes a redundant
  failure surface).
- **SHA-256, not `hash()`** — `hash()` is `PYTHONHASHSEED`-salted, unstable across
  invocations, which would break a persistent cache. The `v1:` prefix allows wholesale
  invalidation; **any change to a `_get` body or its normalizer shape must bump
  `v1→v2`** (the cache has no automatic parser-version invalidation — documented footgun).
- **Note:** Finnhub `insider-sentiment` passes date-stamped `from`/`to` params
  (`today - 183d`), so its key changes each calendar day — it effectively never hits
  across a midnight boundary. Acceptable; noted so it isn't surprising.

## TTL by data half-life

The **(provider, endpoint-path) → bucket** mapping is hardcoded in `cache.py`
(endpoint paths are code-coupled). Keying on `(provider, path)` — not path alone —
avoids the `quote` collision between FMP and Finnhub. Bucket **durations** are
config-driven.

| Bucket         | (provider, path) | Default TTL |
|----------------|------------------|-------------|
| `quote`        | (fmp,`quote`), (fmp,`stock-price-change`), (finnhub,`quote`) | 6h (21600s) |
| `fundamentals` | (fmp,`ratios-ttm`), (fmp,`ratios`), (fmp,`key-metrics-ttm`), (fmp,`key-metrics`), (finnhub,`stock/metric`) | 1d (86400s) |
| `analyst`      | (fmp,`price-target-consensus`), (fmp,`grades-consensus`), (fmp,`insider-trading/search`), (finnhub,`stock/recommendation`), (finnhub,`stock/insider-sentiment`) | 1d (86400s) |
| `statements`   | (fmp,`income-statement`), (fmp,`balance-sheet-statement`), (fmp,`cash-flow-statement`) | 7d (604800s) |
| `profile`      | (fmp,`profile`), (finnhub,`stock/profile2`) | 7d (604800s) |
| `default`      | any unmapped (provider, path) | 1d (86400s) |

> Endpoint strings verified against source (`fmp.py`, `finnhub.py`, `sources.py`) as of
> this spec. A unit test asserts every path emitted by the four `_get` call sites — i.e.
> every value in `FMPSource.fetch`'s `sections` dict, `FinnhubSource.fetch`'s `calls`
> dict, and the screener call sites — is present in the bucket map, so a typo can't
> silently demote `statements` (7d) to `default` (1d).

Config block (added to `config.yaml`):

```yaml
cache:
  enabled: true
  path: .cache/http.sqlite
  ttl:
    quote: 21600
    fundamentals: 86400
    analyst: 86400
    statements: 604800
    profile: 604800
    default: 86400
```

An unmapped `(provider, path)` falls to `default` and is logged once to stderr.

## Integration — configured process-global singleton (not build-path injection)

The two stacks have **two different build mechanisms** (the screener's `_construct` in
`providers/__init__.py` hardcodes per-provider kwargs and has no `finnhub` branch; the
harness's `build_sources` dispatches via `inspect.signature` and only passes `config`).
Three programmatic callers reach the harness without threading config (`data/cli.py`'s
`collect`, `accumulate.py`'s `cf`, and — via `run_harness` — scout). Threading a cache
object through all of that would touch six+ call sites and four constructors.

Instead, `cache.py` owns a **process-global default**:

```python
def configure_default_cache(*, enabled=True, refresh=False, path=None, ttls=None): ...
def get_default_cache() -> CacheLike:   # HttpCache | NoOpCache; never None
def reset_default_cache() -> None:      # test teardown
```

- Each CLI `main()` calls `configure_default_cache(...)` **once at startup**, deriving
  `enabled`/`refresh` from `--no-cache`/`--refresh-cache` and `path`/`ttls` from
  `config.get("cache", {})` (defaults applied when the block is absent — see below).
- Providers/sources gain an optional `cache: CacheLike | None = None` constructor param
  (default `None`), used only for explicit test injection. At fetch time they resolve
  `self._cache or get_default_cache()`. **No change to `_construct` or `build_sources`
  dispatch is required** — the global handles the default, so every entrypoint (incl.
  the config-less harness CLI, accumulate, scout) gets caching for free.
- `--no-cache` → `enabled=False` → `get_default_cache()` returns `NoOpCache`.
- `--refresh-cache` → `refresh=True` → real cache, but reads are bypassed (fetch + write
  always), repopulating. The existing `--refresh` flag (regenerate research briefs)
  is **left untouched** — no overload.

### Config defaults (existing installs)

No central config loader exists; each entrypoint does a bare `yaml.safe_load`. An old
`config.yaml` without a `cache:` block must still get a **working** cache, so all reads
default in code:
`config.get("cache", {}).get("enabled", True)`, `.get("path", ".cache/http.sqlite")`,
and per-bucket `ttl.get(bucket, <hardcoded default>)`. (The deployed
`/opt/oracle/python/config.yaml` lacks the block; defaults cover it.)

## Lifecycle & failure modes

- **`close()`** on `HttpCache` (and a context-manager `__enter__/__exit__`) closes the
  connection; `reset_default_cache()` closes and clears the singleton. CLIs don't need
  an explicit close (process exit suffices with rollback journal — no checkpoint debt),
  but tests use it to model separate processes faithfully.
- **Corrupt/unreadable DB**: `configure_default_cache`/open wraps construction in
  try/except; on failure it logs once (redacted via `env.redact_secrets`) and installs
  a `NoOpCache`. Caching is an optimization, never a hard dependency — a broken cache
  never breaks a screen. Mirrors the Yahoo/FINRA "cache failure non-fatal" precedent.
- **Disk-full / write failure on `_put`**: caught and ignored (the fetch already
  succeeded; caller has live data).
- **Probabilistic sweep**: instead of a guaranteed `DELETE` on every open (a write on
  the hot path of a read-mostly process), sweep expired rows with low probability per
  open (`random.random() < 0.05`) and expose `sweep()` for tests to call deterministically.
  The keyspace is naturally bounded (tickers × ~18 endpoints, overwritten in place), so
  unbounded growth requires many distinct tickers over time; occasional sweep suffices.

## Testing (staff bar)

**`tests/test_cache.py` — unit (own `tmp_path` DB, never the global):**
- key order-independence; secret stripping (`apikey`/`token` absent; rotating key → same key)
- non-JSON param → `cache_key` raises (no silent `default=str`)
- TTL expiry → miss
- **cacheability predicate**: empty `[]`/`{}`/`None`/`""` and `{"error":...}` are NOT
  stored (next call re-fetches); non-empty IS stored
- never-cache-on-raise (fetcher raises → no row; next call fetches again)
- hit payload == miss payload (indistinguishable)
- **persistence across two `HttpCache` instances opened on the same path with a
  `close()` between** — models separate ad-hoc CLI runs (the core requirement)
- corrupt DB file → `NoOpCache`, fetcher still called
- `sweep()` removes expired rows, keeps live rows
- bucket-map coverage: every `(provider, path)` emitted by the four `_get` call sites is mapped

**Call-counting integration:**
- fake fetcher counts invocations: 2nd `get_or_fetch` within TTL → **0** new fetches
  (the §6 acceptance criterion); `refresh=True` → forces 1; `NoOpCache` → 2.
- `aget_or_fetch` exercised under `asyncio` with concurrent tasks on one loop.

**Wired tests:**
- `FMPProvider` with mocked `requests.Session`: two `fetch(ticker)` calls collapse to one
  HTTP call per endpoint.
- `FMPSource` with mocked `httpx`: same on the async path.

**Suite isolation (`tests/conftest.py`):** an **autouse** fixture calls
`configure_default_cache(enabled=False)` (or points it at `tmp_path`) before each test
and `reset_default_cache()` after — so the suite never creates/pollutes the repo-root
`.cache/http.sqlite`, and the singleton never leaks cached rows between tests (which
would break call-count assertions). Existing `__new__`-based provider tests are updated
only as needed (the `self._cache or get_default_cache()` fallback means most need no change).

## Acceptance criteria (from DATA_SOURCES.md §6)

1. Re-running the same basket within TTL makes **zero** upstream calls for cached
   buckets (asserted via the call-counting fake).
2. A cold run still respects rate limits (misses paced by existing retry/backoff); a
   warm re-run completes without `429`s.
3. `--refresh-cache` repopulates; TTL expiry triggers a single re-fetch; errors and
   empty/soft-failure payloads are never cached.

## Explicitly rejected (YAGNI / over-engineering)

- Cross-process single-flight / leasing — double-fetch on miss is acceptable and the
  cacheability predicate prevents persisting the worse of two results.
- `asyncio.to_thread` around SQLite calls — sub-ms ops; thread hop is net-negative.
- WAL journal mode — wrong fit for a short-lived, possibly-NFS, ad-hoc process.
- A separate `shortlist-cache` maintenance CLI — probabilistic sweep suffices.
- Caching EDGAR via edgartools storage — free/uncapped; separate mechanism.
- Per-endpoint configurable cacheability predicates — the universal empty/error rule
  covers every known soft-failure mode.
- A `_cached: true` marker — violates indistinguishability.

## Files touched

- **New:** `src/shortlist/cache.py`, `tests/test_cache.py`, `tests/conftest.py` (or
  extend an existing one)
- **Edit (wrap `_get`, add optional `cache=None`):** `providers/fmp.py`,
  `providers/finnhub.py`, `data/sources.py` (FMP + Finnhub sources)
- **Edit (flags + `configure_default_cache` at startup):** `screen.py` (the `shortlist`
  CLI), `data/cli.py` (the `shortlist-harness` CLI — add `--no-cache`/`--refresh-cache`),
  and `scout`/`accumulate`/`backtest` CLIs as needed for flag parity (at minimum they
  inherit the on-by-default global)
- **Edit:** `config.yaml` (`cache:` block); confirm `.gitignore` ignores `.cache/`
- **Edit (existing tests):** `tests/test_fmp_provider.py`, `tests/test_finnhub_provider.py`
  only if the `_get` fallback doesn't already cover them
- **Docs:** `docs/DATA_SOURCES.md` §6 (mark built; document flags/config/TTLs),
  `CLAUDE.md` (caching now exists; the daily-quota guidance), `HARNESS.md` (flags),
  `README.md` (flags if user-facing)

## Adversarial-review resolutions

| # | Finding | Resolution |
|---|---------|-----------|
| B1 | "never cache errors" false for 200-OK empties/error bodies | cacheability predicate in `_put` |
| B2 | `check_same_thread` + EDGAR `to_thread` | open `check_same_thread=False`, lock load-bearing |
| M3 | TTL keyed by path alone (`quote` collision) | key bucket map on `(provider, path)` |
| M4 | exact endpoint strings | verified; bucket-map coverage test added |
| M5 | redundant `symbol` arg; `default=str` hazard; date-stamped params | drop arg; remove `default=str`; note insider-sentiment |
| M6 | WAL wrong for ad-hoc; no close; NFS | rollback journal; `close()`; documented |
| M7 | singleton lifecycle/leak | `close()` + `reset_default_cache()`; persistence test opens/closes |
| m8 | sweep on every open | probabilistic sweep |
| m9 | double-fetch persists worse result | subsumed by B1 predicate |
| m10 | parser-change staleness | documented `v1→v2` bump discipline |
| I-B1/2/3 | build-path injection across two registries + config-less callers | configured process-global singleton; no build dispatch change |
| I-M4 | `--refresh` overload | distinct `--no-cache`/`--refresh-cache` |
| I-M5 | config defaults for old installs | in-code `.get(..., default)` |
| I-M6/7 | test pollution / `__new__` fixtures | autouse isolation fixture; `_get` fallback |
| I-m8/9 | `FMPSource` naming; lock purpose | corrected; documented |
