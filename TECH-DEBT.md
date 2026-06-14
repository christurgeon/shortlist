# TECH-DEBT

Larger items surfaced by the 2026-06-14 fleet review that were **deliberately
deferred** rather than fixed inline — each either changes scoring/backtest numerics
(needs a backtest first), is a cross-cutting refactor with drift risk, or needs
coordination across modules. The ~30 safe, output-neutral fixes from the same review
were applied directly (see git history).

Format: **[area] title** — what / why deferred / suggested approach.

---

## Numerics-affecting (backtest before shipping)

### [data-harness] Yahoo source never populates `ret_1m` / `ret_3m` / `ret_12m`
`_normalize_yahoo` (`data/sources.py` ~L897) only sets `ret_6m`, `rel_strength_6m`,
`realized_vol`, `max_drawdown`, `ma200`. Because Yahoo **leads** the price merge, the
1m/3m/12m momentum legs fall through to FMP — and go `null` whenever FMP gates the
symbol (402) even though Yahoo holds the full 5y daily series. The `_yh_ret_over`
helper already exists. **Why deferred:** adding non-None values on a merge-priority-
leading source shifts momentum/composite numerics for gated names. **Approach:** add
`ret_1m=_yh_ret_over(closes,21)`, `ret_3m=…(63)`, `ret_12m=…(252)`; validate with
`shortlist-backtest` momentum rank IC before/after.

### [data-harness] Finnhub `roiTTM` mapped to `roic`
`data/sources.py` ~L257 maps Finnhub's `roiTTM` (Return *on Investment*) into the
snapshot's `roic` (Return *on Invested Capital*) — different metrics. Only surfaces on
the FMP-gated fallback path, and unlike the other documented approximations it carries
no note. **Why deferred:** changing/removing a fundamentals input shifts quality/moat
scores; low confidence on intent. **Approach:** confirm Finnhub field semantics; either
add an inline comment that it's a deliberate ROIC proxy on the gated path, or drop the
mapping so `roic` stays `None`. Backtest the quality/moat axes either way.

### [providers] FMP insider treats every non-`P` code as a sale
`providers/_fmp_insider.py` `is_buy()` returns True only for `P`-prefixed codes, and
`net_value()` does `net += v if is_buy else -v` — so awards (`A`), option exercises
(`M`), gifts (`G`), tax-withholding (`F`), conversions (`C`) all count as **sells**,
both subtracting from `net_value_6m` and (in the sources loop) inflating `sell_count`.
The sibling `_form4.py:classify_code` correctly three-ways these into buy/sell/other and
skips `other`. The two insider paths therefore disagree, biasing the FMP insider
sub-score bearish (a routine option exercise reads as selling). **Why deferred:** changes
insider scoring numerics; the FMP insider endpoint is paid/402-gated so it rarely fires
on the free tier, and the current behavior is documented as intentional. **Approach:**
share a single `classify_code` between `_fmp_insider` and `_form4` so both skip non-trade
codes; backtest the insider axis.

### [backtest] `quantile_spread` substitutes `0.0` for an empty bucket
`backtest/metrics.py` ~L126: after the average-occupancy collapse, an individual edge
bucket can still be empty for small/uneven n, and `mean(rets) if rets else 0.0` feeds a
fabricated `0.0` return straight into `spread = bucket_means[-1] - bucket_means[0]` and
the monotonic check — a silent numeric substitution that violates the module's
"drop, never impute" discipline. Low confidence it fires in practice (n≥4, nb≥2).
**Approach:** treat an empty bucket as abstain — re-collapse `nb` further or return
`None` rather than computing the spread against a 0.0.

## Cross-cutting refactors (drift risk; keep byte-identical)

### [data-harness] Shared keyed-HTTP base for FMPSource / FinnhubSource
`FMPSource._get` (~L70) and `FinnhubSource._get` (~L217) are near-identical (build params
with key/token → inner `fetch()` GET → `raise_for_status` → `.json()` →
`cache.aget_or_fetch`), differing only in the auth param name (`apikey` vs `token`) and
the cache provider tag; the two `__init__` bodies likewise. The redact + cacheability
invariants must currently be kept in sync by hand. **Approach:** extract a small base
parameterized by `base_url`, `auth_param_name`, `provider_tag`. Pure refactor — pin with
a byte-identical-output test across both sources.

### [data-harness] Shared disk-cache + bulk-index scaffolding for Finra / Wsb
`FinraSource` and `WsbSource` share the load-once-then-O(1)-lookup shape, and the
try/exists/`json.loads` + `mkdir`/`write_text` + `except: pass` disk-cache idiom is
triplicated across `FinraSource._read_cache/_write_cache`, `YahooSource._get_chart`, and
`apewisdom.fetch_wsb_mentions`. **Approach:** extract `read_json_cache`/`write_json_cache`
helpers and optionally a `BulkIndexSource` mixin. Touches three live caching paths — needs
behavior-preserving test coverage.

### [data-harness] One named present-ness predicate for the merge/coverage machinery
`_merge_flat`, `_has_data`, `_merge_insider`, and `coverage()` in `data/models.py` all
inline `v not in (None, [], "")` to mean "present", conflating a legitimately-empty `""`
with "absent". Fine for the current Optional fields, but the convention is duplicated in
four places with no single named home. **Approach:** factor into one `_is_present(v)`
reused everywhere. Cross-cutting over merge/coverage that scoring depends on — verify no
behavior change.

### [backtest] `_load_histories` fetches SPY + every ticker serially
`backtest/cli.py` ~L103 awaits SPY then each ticker one at a time; `_load_companyfacts`
is the same shape. Pure latency, not correctness — and serial requests are a defensible
politeness choice against Yahoo's edge WAF (see CLAUDE.md). **Approach (only if runtime
bites):** gather under a bounded `asyncio.Semaphore` (3–5), keeping SPY first to seed IP
reputation.

## Coordination / behavior-shaping

> **RESOLVED 2026-06-14:** `FMPSource._get` now implements the documented Retry-After-
> aware 429/5xx backoff, wired to the existing `fmp.max_retries` config knob (threaded via
> `build_sources`). 402 gating is not retried. Pinned by `tests/test_fmp_retry.py`; CLAUDE.md's
> claim is now accurate. `FinnhubSource` was deliberately left out (60/min is comfortable).

> **RESOLVED 2026-06-14:** `_salvage_json` now uses a string/escape-aware brace-depth
> scanner returning the first *balanced* `{...}` object (was first-`{` .. last-`}`), so
> trailing prose containing a `}` no longer breaks parsing. Pinned by `tests/research/test_assess.py`.

> **RESOLVED 2026-06-14:** `assess()` now accumulates `cost_usd` across the reparse retry,
> so a name that needed a second call records the full spend. Pinned by `tests/research/test_assess.py`.

## Test-only hardening (cheap, low risk — do opportunistically)

> **RESOLVED 2026-06-14:** `fetch_companyfacts` now persists a `_shortlist_no_us_gaap`
> negative marker for IFRS/20-F issuers (month-scoped like the positive cache) and
> short-circuits on it, so a full-universe backtest no longer re-hits SEC for the same
> never-resolving foreign issuers each run. Pinned by `tests/test_xbrl_fetch.py`.
