# TECH-DEBT

Larger items surfaced by the 2026-06-14 fleet review that were **deliberately
deferred** rather than fixed inline — each either changes scoring/backtest numerics
(needs a backtest first), is a cross-cutting refactor with drift risk, or needs
coordination across modules. The ~30 safe, output-neutral fixes from the same review
were applied directly (see git history).

Format: **[area] title** — what / why deferred / suggested approach.

---

## Open next steps

### [backtest] Momentum Stage 0 — de-risk re-run before any Stage 1 build
The multi-horizon momentum "prize-bound" gate (`backtest/prize_bound.py`, shipped
2026-06-14; design/plan in the local `docs/superpowers/specs|plans/2026-06-14-multi-horizon-momentum-*`)
returned a **marginal PROCEED** on a 28-name large-cap subset: `mom_6m` is fully redundant
with the incumbent `rel_strength_6m` (τ 0.995, zero churn), `mom_12_1` moves only ~1 name in
the top-10 (full-basket τ **0.947**, just under the 0.95 inert prior), and the weight bound
(τ 0.80) confirms momentum at 0.08 has only ~2-top-10 of theoretical headroom.

**Next step (do this before committing to the Stage 1 measurement build):** re-run on the
**full 80-name** largecap basket once FMP's daily quota has reset (a quota-starved run
inflates momentum's effective weight and would overstate churn):

```
uv run python -m shortlist.backtest.prize_bound   # full largecap (80)
```

**Decision rule:** compare `mom_12_1`'s full-basket τ to today's 0.947 — holds or drops
(more churn) → the prize is real, write the Stage 1 plan; rises toward 1.0 (less churn) →
**stop**: momentum at 0.08 is a near-zero mover (EV/EBIT-style "measured, not shipped"), and
the only remaining lever is the value/momentum **weight split** (a separate question). Drop
`mom_6m` either way.

---

## Numerics-affecting (backtest before shipping)

> **INVESTIGATED 2026-06-15 — NO-OP, do not pursue as scoped.** The original item proposed
> populating `ret_1m`/`ret_3m`/`ret_12m` in `_normalize_yahoo`. Tracing the chain showed
> those fields are **dead**: set by FMP/mock but read by nothing (not the bridge, scorer,
> backtest, or reports), and `momentum_score` is already 100% Yahoo-sourced + gating-immune
> (`price_vs_200dma` + `rel_strength_6m`; `eps_revision` is permanently `None`). The real
> adjacent question — does a better multi-horizon momentum signal help — became the
> momentum Stage 0 prize-bound work (see "Open next steps" above).

### [data-harness] Finnhub `roiTTM` mapped to `roic`
`data/sources.py` `_normalize_finnhub` maps Finnhub's `roiTTM` (Return *on Investment*)
into the snapshot's `roic` (Return *on Invested Capital*) — different metrics. Only
surfaces on the FMP-gated fallback path. **PARTIALLY ADDRESSED 2026-06-26:** the
no-note half is fixed — the mapping now carries an inline comment marking it a
*deliberate* ROIC proxy on the gated path (so it's no longer an undocumented
approximation). The numerics half remains open: whether to keep the proxy or drop it
to `None` shifts quality/moat scores, so it stays deferred pending a quality/moat
backtest. **Approach (remaining):** backtest the quality/moat axes with the proxy on
vs. off; drop the mapping if it doesn't help.

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

> **INVESTIGATED 2026-06-26 — CANNOT FIRE, do not pursue.** The concern was that the
> `mean(rets) if rets else 0.0` at `backtest/metrics.py:129` imputes a fabricated `0.0`
> for an empty bucket. Tracing the guards shows the `else 0.0` branch is **unreachable**:
> `len(clean) >= 4` (early return otherwise) and the collapse loop exits with either
> `nb == 2` or `len(clean) // nb >= 2`, so `size = len(clean) / nb >= 2` in every case.
> Each bucket then spans `round((b+1)*size) - round(b*size) >= size - 1 >= 1` rows (and the
> last bucket runs to `len(clean)`), so `rets` is never empty. The branch is dead defensive
> code — left in place as a belt-and-suspenders guard rather than churned, since removing it
> changes no output. (Mirrors the `ret_1m` no-op finding above: investigated, not a real bug.)

## Cross-cutting refactors (drift risk; keep byte-identical)

> **RESOLVED 2026-06-14:** Extracted `_KeyedHttpSource` (data/sources.py) — env-key
> resolution, lazy httpx client, and the cache-delegating GET with optional Retry-After
> backoff now live once; `FMPSource`/`FinnhubSource` set `BASE`/`_AUTH_PARAM`/`_ENV_VAR`/
> `_PROVIDER`. Default `_max_retries = 0` (single attempt) keeps Finnhub's no-retry
> behavior byte-identical; FMP opts in. Suite unchanged at 1006 passing; demo output identical.

> **RESOLVED 2026-06-26 (disk-cache half):** Extracted `read_json_cache`/`write_json_cache`
> into the new `data/diskcache.py` leaf; the `try/exists/json.loads` + `mkdir/write_text` +
> `except: pass` idiom now lives once. Five data-layer sites delegate to it —
> `YahooSource._get_chart`, `FinraSource`, `GovContractsSource`, `LobbyingSource` (keeps its
> `_CACHE_V` version gate around the shared load), and `apewisdom.fetch_wsb_mentions`.
> Behavior-preserving (the helper's contract — present-falsy `[]`/`{}` is a hit, missing-or-
> corrupt is a `None` miss — is pinned by `tests/test_diskcache.py`; full suite 1286 passing,
> demo output identical). `macro.py` (TTL-gated single file) and `backtest/xbrl.py` (different
> layer + "treat as miss" semantics) intentionally keep their own shapes. The optional
> `BulkIndexSource` mixin (the shared load-once-then-O(1)-lookup shape of `FinraSource`/
> `WsbSource`) was **not** pursued — the two index structures differ enough that a mixin would
> add coupling without removing real duplication.

> **RESOLVED 2026-06-14:** Factored the `v not in (None, [], "")` convention into one
> `_is_present(v)` in `data/models.py`, reused by `_merge_flat`/`_merge_insider`/`_has_data`
> and `coverage()`/`missing()`. Behavior-identical (suite unchanged at 1006); the rule now
> lives in exactly one place.

> **RESOLVED 2026-06-26 — keep as by-design, do not pursue.** `backtest/cli.py`
> `_load_histories` (and `_load_companyfacts`) fetch SPY then each ticker serially. This is
> **pure latency, not correctness**, and serial requests are the *intended* politeness choice
> against Yahoo's edge WAF (see the "Yahoo screener WAF gotcha" in CLAUDE.md — a burst of
> concurrent requests risks the fingerprint block the cooldown machinery is built to avoid).
> The note itself scoped this "only if runtime bites"; backtest runs are offline-batch and
> not latency-sensitive, so the trade-off favors the current behavior. Revisit only if a
> full-universe backtest's wall-clock becomes a real bottleneck — then gather under a bounded
> `asyncio.Semaphore` (3–5) with SPY first to seed IP reputation.

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
