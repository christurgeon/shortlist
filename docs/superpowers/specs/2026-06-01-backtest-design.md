# Backtest harness — design spec

**Date:** 2026-06-01
**Status:** approved (autonomous review)
**Closes:** `docs/ASSESSMENT_GAPS.md` §2.1 — "No validation that the score predicts anything."
**Branch / worktree:** `backtest` (`/home/chris/shortlist-backtest`)

This spec was produced autonomously: design decisions were resolved by subagent
exploration of the codebase, an empirical probe of the Yahoo data feed, a
literature review of factor-validation methodology, and an adversarial
architecture critique — not by hand-assertion. Where a number is a default, it
cites why.

---

## 1. Problem & goal

Every weight and band in `config.yaml` is **asserted, never measured**. There is no
evidence the composite score (or any sub-score) predicts forward returns. This
harness converts `config.yaml` from taste to evidence by reporting, for any
signal we can compute point-in-time:

- **Rank Information Coefficient (IC)** — Spearman rank correlation between the
  signal at time *T* and the realized forward return over *T → T+h*.
- **Quantile forward-return spread** — top-minus-bottom bucket, with monotonicity.

The deliverable must be **robust** (no look-ahead, honest about bias and breadth)
and **extensible** (new point-in-time signals — XBRL fundamentals, sector-relative
scores — slot in without rework).

### What can actually be validated *today* (the honest scope)

Three facts, established empirically, bound v1:

1. **No stored snapshot history exists.** `data/store.py` accumulates point-in-time
   `TickerSnapshot`s as the harness runs daily, but the store is empty today. A
   snapshot-replay backtest therefore has nothing to validate against *now*.
2. **Price history is available and point-in-time by construction.** Yahoo's keyless
   chart feed yields full daily adjusted closes (AAPL ~11.4k points back to 1980,
   SPY ~8.4k back to 1993). Momentum signals can be *reconstructed* at any historical
   date from prices alone, with no look-ahead.
3. **Dependencies are stdlib-only** (no numpy/scipy/pandas). All statistics are
   implemented in pure Python (`statistics` + `math`). This is a hard constraint, not
   a preference.

So v1's **real, validatable result is the momentum axis**, run over a real
large-cap universe (price-only, keyless). The composite, the fundamental
sub-scores (quality/moat/growth/value/insider), and weight-fitting depend on
accumulated point-in-time fundamentals that do not exist yet — those paths are
built as **tested, guarded, ready** modules but produce no headline number until
their data lands. This matches `ASSESSMENT_GAPS.md` §2.1 exactly: *"Start with a
price/momentum IC (data already on hand) and expand as XBRL history lands."*

Anti-corner-cutting note: deferring weight-fitting *results* is intellectual
honesty (you cannot fit six weights from one observable axis), **not** dropping
scope. The fitting *mechanism* is built and unit-tested against synthetic data
with known structure; it is gated from emitting a result until it has real
multi-axis history. Extensibility is in the architecture; the guard is in the
output.

---

## 2. Architecture

A new package `src/shortlist/backtest/`, with a **signal-agnostic core**. The unit
of currency is an `Observation`:

```python
@dataclass(frozen=True)
class Observation:
    as_of: date          # first date this value was PUBLICLY KNOWABLE (see §6)
    ticker: str
    signals: dict[str, float]   # {signal_name: value}; every value a 0–100 sub-score
```

Two contracts make the core extensible and the IC apples-to-apples:

- **`as_of` = first publicly-knowable date.** For price-reconstructed signals this
  is the price date. For future XBRL fundamentals it is the **filing acceptance
  date** (not the fiscal-period end), because the number is not knowable until
  filed. The engine's look-ahead join keys off `as_of`; baking in "as_of = price
  date" would silently leak look-ahead when XBRL lands, so the semantics are
  documented in the protocol and enforced by the source.
- **Every signal value is a scored sub-score on the 0–100 scale**, produced by the
  *real* `scoring` functions — never raw legs, never a reimplementation (see §3).
  This keeps IC comparable across heterogeneous sources.

### Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `prices.py` | Async Yahoo **dated** daily-close fetcher; `PriceHistory` with nearest-trading-day lookup and forward-return. | `httpx`, existing Yahoo cache conventions |
| `metrics.py` | Pure stdlib stats: tie-averaged `rank`, `spearman_ic`, `aggregate_ic`, `quantile_spread`. | `statistics`, `math` |
| `signals.py` | `SignalSource` protocol + `MomentumSignalSource` (real today) + `SnapshotSignalSource` (ready, guarded). | `prices`, `data.sources`, `data.bridge`, `scoring` |
| `engine.py` | Build non-overlapping observation grid; join forward returns; compute time-series + cross-sectional IC and quantile spreads; assemble `BacktestReport`. | `prices`, `metrics`, `signals` |
| `fit.py` | Walk-forward composite-weight fitting with shrinkage toward prior; **guarded** (refuses below breadth/period floors). | `metrics`, `scoring` |
| `report.py` | Render `BacktestReport` as a rich table / JSON / CSV. | `rich` |
| `universe.py` + `universe_largecap.txt` | Bundled default universe (~80 curated current large-caps) for cross-sectional breadth, plus ad-hoc `--tickers`. | — |

The three universe figures are related, not contradictory: the bundled list is
**~80** so that after dropping names with < ~200 trading days of pre-*T* history,
the surviving per-date cross-section still clears the **~30-name** reporting gate
(§5) and the **~50-name** acceptance bar (§8).
| CLI: `shortlist-backtest` → `backtest.cli:main` | Orchestrate; flags for universe, horizon, sampling, buckets, source, output. | all |

### A pre-req seam in `data/sources.py` (Task 0)

The momentum path **must** reuse production leg math. `_normalize_yahoo(ticker,
closes, spy_closes) -> TickerSnapshot` (sources.py:482) already builds a `Price`
from a close list via `_yh_sma`/`_yh_ret_over`/etc., all of which read trailing
windows (`xs[-n:]`, `xs[-1]`). Passing `closes[:idx_T+1]` therefore yields a
correct point-in-time `Price` with zero new math. We add one public re-export so
the backtest does not reach into a privately-named function:

```python
# sources.py — public seam, delegates to the existing implementation
def snapshot_from_closes(ticker: str, closes: list[float],
                         spy_closes: list[float]) -> TickerSnapshot:
    return _normalize_yahoo(ticker, closes, spy_closes)
```

No behavior change; existing tests stay green.

---

## 3. The momentum path — reusing real scoring (the load-bearing decision)

`scoring.momentum_score` (scoring.py:56) computes nothing itself — it consumes
pre-computed `price_vs_200dma`, `rel_strength_6m`, `eps_revision`. The leg math
lives in `_normalize_yahoo` (harness) and, divergently, in `providers/fmp.py`
(screener). Reimplementing it in the backtest would validate *that
reimplementation*, not the product. So `MomentumSignalSource.observe(ticker, T)`:

1. From the ticker's full dated close history and SPY's, slice to closes with
   `date ≤ T` (strict look-ahead boundary).
2. `snapshot_from_closes(ticker, closes_≤T, spy_≤T)` → `TickerSnapshot` with a
   real `Price` (identical to production: `ma200` over last 200, `rel_strength_6m`
   = stock 126-day return − SPY 126-day return).
3. `bridge.snapshot_to_metrics(snap)` → `StockMetrics` (the real bridge).
4. `scoring.momentum_score(metrics, thresholds)` → the production momentum sub-score
   at *T*. The `eps_revision` leg is `None` historically and redistributes via the
   existing `_avg` None-handling — exactly as in a live thin-data run.
5. Emit `Observation(as_of=T, ticker, {"momentum": score})`.

This requires ≥ ~200 trading days of history *before T* (for `ma200`); names with
less are **dropped, never zeroed**, at that date.

The same mechanism generalizes: `SnapshotSignalSource.observe` loads a stored
`TickerSnapshot` at `as_of`, runs the identical `snapshot_to_metrics → score`
chain, and emits **all** sub-scores + composite. It is fully implemented and
unit-tested with synthetic snapshots, but the CLI **guards** it: it refuses to
run against the store until ≥ `MIN_SNAPSHOT_DATES` distinct dates exist (default
**24**, matching the ≥~24-period trust floor in §5), printing why, so no one
reports a "fitted weight from 8 names." It is also documented as
valid **only** for organically-accumulated daily captures — never backfilled or
restated data (that would reintroduce look-ahead).

---

## 4. Forward returns & the price layer (`prices.py`)

- **Source:** Yahoo chart with `period1=0&period2=<now>&interval=1d`. **Do not use
  `range=max`** — it silently degrades to quarterly bars (167 points). For a bounded
  run `range=10y&interval=1d` (~2514 daily points) is the named-param fallback. The
  fetch as-of is **pinned into the report** for reproducibility (Yahoo re-adjusts
  old `adjclose` over time; a cached pull is the stable reference).
- **`PriceHistory`** holds parallel `dates: list[date]` and `closes: list[float]`
  (from `chart.result[0].timestamp` and `…adjclose`). **Paired filtering is
  mandatory:** `zip(timestamp, adjclose)` and drop a pair only when its close is
  non-numeric — never filter the two lists independently. The existing
  `_closes_from_chart` (sources.py:473) drops nulls *positionally* and discards
  timestamps, which would silently desynchronize dates from closes and make every
  forward-return join wrong; `prices.py` therefore uses its own paired parser, not
  `_closes_from_chart`. (Empirically zero nulls were observed on the full daily
  pull, but Yahoo can return nulls on halts/bad ticker-days, so alignment must not
  depend on that.) An acceptance test feeds a synthetic chart with an embedded
  `None` close and asserts `dates`/`closes` stay aligned.
- **Caching:** day-cached on disk in Yahoo's cache *directory* but under a
  **distinct filename** (e.g. `{SYMBOL}-fullhist-{today}.json`) and using
  `prices.py`'s own fetch params (`period1=0` / `range=10y`) — it must **not** reuse
  `YahooSource._cache_path` or it would silently be satisfied by the 2y harness
  cache. SPY is fetched **once** and shared across the universe.
- **`price_on(target: date)`** returns the close at the nearest trading day within a
  **±5-trading-day tolerance**, else `None`.
- **`forward_return(T, horizon_months)`**: target = `T + horizon_months` **calendar
  months**, resolved to nearest trading day (tolerance above). Horizon is calendar,
  not "63 trading days," to stay correct across holidays. Returns `None` (observation
  dropped) when the target is past series end or tolerance is unmet — the last
  `horizon` of every series has no forward return and is **dropped, not imputed**.
- **Adjusted close is total-return-adjusted** (splits + dividends). Forward returns
  are therefore total returns; the signal legs ride the *same* adjustment, so signal
  and target are consistent. The report states this.
- **Excess returns:** because the momentum signal is itself benchmark-relative
  (`rel_strength_6m` = stock − SPY), the **default forward return is excess over
  SPY** over the identical window. Raw and excess are both computed; excess is the
  headline so IC isn't dominated by market beta. (Configurable via `--return raw|excess`.)

---

## 5. Metrics (`metrics.py`, pure stdlib)

- `rank(xs)` — fractional ranks with **average tie handling** (required or Spearman
  is wrong on ties).
- `spearman_ic(signal, fwd)` — Pearson correlation of the two rank vectors; `None`
  if < 3 non-null pairs.
- `aggregate_ic(per_period_ics)` → `{mean, std, icir, t_stat, hit_rate, n}` where
  `icir = mean/std`, `t_stat = icir·√n` (a one-sample t-test that mean IC > 0), and
  `hit_rate` = fraction of periods with IC > 0.
- `quantile_spread(pairs, n_buckets)` → per-bucket **equal-weighted** mean forward
  return, top-minus-bottom spread, and a monotonicity flag. Cap-weighting is noted
  as blocked on fundamentals (no `market_cap` on the price-only path).

**Two IC modes, and which is load-bearing:**
- **Time-series (single-name) IC** — for each name, Spearman between its signal at
  *T* and its own forward return, across the grid. Works with even one name × many
  periods; this is the **primary** metric for small watchlists.
- **Cross-sectional IC** — at each grid date, Spearman across the universe; then
  aggregated over dates. The factor test. **Gated**: not reported (loud caveat)
  when the per-date cross-section is below ~30 names; buckets auto-collapse
  quintiles → terciles for small universes.

Methodology defaults (grounded in literature — see §8 sources): horizon **3 months**
default (1m/6m/12m secondary to show decay); **quintiles** (terciles when small);
**non-overlapping sampling** (sample every `horizon` months) as the overlap fix so
the `t = mean/std·√n` t-stat is valid without Newey-West; report **IC hit-rate**
alongside. Interpretation guide printed with results: monthly IC ~0.02–0.05 is a
real signal, >0.10 strong; trust requires ≥ ~30 names/period and ≥ ~24 periods —
below that, results are labeled **exploratory**.

---

## 6. Look-ahead, survivorship, and honesty rails

- **Look-ahead boundary (asserted in code):** signal uses only data with `date ≤ T`;
  forward-return window is entirely `> T`. A unit test catches the off-by-one
  (close *at* T+h vs *after*).
- **`as_of` semantics** (§2) documented in the `SignalSource` protocol so XBRL lands
  without leaking.
- **Survivorship:** the bundled universe is *currently-listed* large-caps; delisted
  names are absent, so realized spreads are an **upper bound**. The report frames the
  headline as **relative signal validation** (does the score rank-order forward
  returns within this universe?), which is exactly what a screener needs, not a
  tradeable PnL. Caveat printed every run.
- **Gross, not net:** the report states it computes gross signal IC, not
  net-of-cost portfolio returns — a 0.04 IC is not a tradeable edge by itself.
- **Determinism:** given a fixed price cache, all joins/lookups/buckets are
  deterministic; the price-fetch as-of is pinned into `BacktestReport`.

---

## 7. CLI & output

`shortlist-backtest` (new console script in `pyproject.toml`):

```
--tickers GEV,LMT,...     ad-hoc universe (time-series IC focus)
--universe largecap       bundled ~80-name large-cap list (cross-sectional; default)
--horizon 3               forward-return horizon in months (default 3)
--horizons 1,3,6,12       multiple horizons in one run
--buckets 5               quantile buckets (auto-collapses if universe small)
--return excess|raw       default excess (over SPY)
--source momentum|snapshot   default momentum; snapshot is guarded (§3)
--start / --end           restrict the observation grid
--json / --csv PATH       machine-readable output (table by default)
```

`BacktestReport` (printed + JSON): per-signal × per-horizon IC block (mean, std,
ICIR, t-stat, hit-rate, n_periods, n_obs, breadth), quantile spreads with
monotonicity, the universe + price-fetch as-of, and the honesty caveats (§6). The
table is the default; JSON/CSV keep stdout clean for piping.

---

## 8. Testing & acceptance

**Pure-stats tests (highest value — acceptance criteria):**
- `spearman_ic`: perfectly monotone pairs → +1.0; reversed → −1.0; seeded noise →
  ≈0; **tied ranks handled** (average ties) → matches a hand-computed value.
- `aggregate_ic`: known IC list → correct mean/std/icir/t_stat/hit_rate.
- `quantile_spread`: constructed buckets → correct spread + monotonicity flag.

**Price layer:**
- `price_on` nearest-day within/outside tolerance; `forward_return` calendar-month
  resolution, excess-vs-raw, right-edge drop; **look-ahead off-by-one** test.
- Yahoo parsing from a synthetic chart payload (monkeypatched fetch, like
  `tests/test_yahoo_source.py`); no network in tests.

**Momentum path (proves real-code reuse, not reimplementation):**
- A synthetic dated close series where the production `_normalize_yahoo` →
  `momentum_score` value at *T* is hand-derivable; assert the source emits exactly
  that. Confirms the backtest rides production scoring.

**Engine / fit:**
- Engine end-to-end on synthetic prices with a **planted** signal→return relationship
  → recovers the expected IC sign/magnitude; non-overlapping grid spacing correct;
  thin names dropped not zeroed.
- `fit.py` recovers known weights from synthetic multi-axis data; **guard** refuses
  below breadth/period floors (test the refusal).

**Live smoke (run once during verification, not in CI):**
- Real `shortlist-backtest --universe largecap --horizons 1,3,6,12` produces a
  momentum IC report; numbers are sanity-checked (momentum should show a positive
  but modest IC) and pasted into the PR description as the data-driven result.

**Acceptance:** (1) momentum IC + quantile spread computed on a real ≥~50-name
large-cap universe with no look-ahead and no reimplemented scoring; (2) all stats
unit-tested against known-answer fixtures including ties; (3) snapshot + fit paths
implemented, tested, and guarded — ready for fundamentals history; (4) `Observation`
core absorbs a future XBRL source by adding one `SignalSource`, no engine change;
(5) full `uv run pytest` green; (6) honesty rails (look-ahead assert, survivorship
caveat, exploratory labeling) present in output.

---

## 9. Explicitly deferred (Phase 2, blocked on data — not corners cut)

- **Weight-fitting *results*** — needs accumulated point-in-time multi-axis history.
  Mechanism built/tested/guarded now.
- **Snapshot-replay *results*** — needs the daily store to accumulate organically.
  Source built/tested/guarded now.
- **Point-in-time fundamentals (EDGAR XBRL)** — a future `XBRLSignalSource` emitting
  sub-scores at filing-acceptance `as_of`. The `Observation` core is designed to
  absorb it without rework (§2, §6).
- **Cap-weighted buckets / transaction-cost PnL** — blocked on fundamentals; equal-
  weight gross IC is the v1 convention.

## 10. House rules honored

Route any error string that may contain a request URL through
`env.py:redact_secrets()` (the Yahoo fetcher embeds no key, but the pattern is kept
for any source error surfaced). Keep coverage honest: a missing input lowers
coverage / drops the observation / redistributes weight — never a silent zero.
