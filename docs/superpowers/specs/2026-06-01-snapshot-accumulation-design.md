# Point-in-time snapshot accumulation — design spec

**Date:** 2026-06-01
**Status:** approved (autonomous review)
**Enables:** the backtest **snapshot-replay** + **weight-fitting** paths
(`shortlist.backtest`, gap `ASSESSMENT_GAPS.md` §2.1 Phase 2), which are built and
**guarded** until ≥ `MIN_SNAPSHOT_DATES` (24) organically-captured daily snapshots
exist. Today the store is empty and nothing captures it, so those paths can never
activate. This builds the capture mechanism.
**Branch / worktree:** `feat/snapshot-accumulation` (`/home/chris/shortlist-accumulate`)
**Hard constraint:** **the daily job is delivered DORMANT — not enabled, not
scheduled.** This ships the mechanism + a sample (disabled) timer; turning it on is
a separate, explicit user action.

---

## 1. Goal & non-goals

**Goal:** a robust, idempotent, observable mechanism to capture point-in-time
`TickerSnapshot`s into the on-disk store (`store.save`, `<root>/<TICKER>/<DAY>.json`)
once per day, so the snapshot-replay backtest accumulates the real history it needs.

**Non-goals (explicit):**
- **Not enabling** any schedule (no installed cron/systemd timer). Sample unit
  shipped disabled.
- **No backfill.** Snapshots are stamped with `as_of = utcnow` at capture; the tool
  must never write a snapshot dated to a past day. Backfilled fundamentals would be
  restated-not-as-reported data and silently reintroduce look-ahead into the
  backtest — forbidden by design.
- Not a portfolio/trading job; not the autonomous-scout feature (separate). This is
  purely data accumulation feeding the backtest.

## 2. Point-in-time integrity (the load-bearing invariant)

The snapshot-replay backtest is valid **only** for organically-accumulated daily
captures (see `SnapshotSignalSource` docstring). Therefore:

- The tool captures **only the current UTC day**. There is no `--date` override for
  *writing*; `as_of` always comes from `utcnow_iso()` via the normal collect path.
- The idempotency key is **today's** date (`as_of[:10]`). Re-running on the same day
  is safe (overwrites today with fresh same-day data); it can never touch another
  day's file.
- The run-log records the wall-clock capture time, so any out-of-band tampering is
  auditable.

## 3. Architecture

A new leaf module `src/shortlist/data/accumulate.py` (pure orchestration over the
existing `collect` + `store`), a CLI `shortlist-accumulate`, a bundled default
watchlist, and a **disabled** deploy sample.

| Unit | Responsibility |
|---|---|
| `data/accumulate.py` | `accumulate()` (idempotent capture), `captured_days()` / `is_captured()` (store query helpers the store lacks), `store_status()` (how close to the 24-date threshold), run-log append. |
| `data/store.py` (modify) | Make `save()` **atomic** (write `*.tmp` then `os.replace`) so an interrupted daily run can't leave a truncated JSON. Add `captured_days(ticker, root)` query helper. |
| CLI `shortlist-accumulate` → `data/accumulate:main` | `run` (capture today) and `status` (progress toward the backtest threshold) subcommands. |
| `data/accumulate_watchlist.txt` | ~12 liquid large-caps known to work on FMP's free tier (avoids the documented per-symbol 402-gated names). Default universe. |
| `deploy/shortlist-accumulate.{service,timer}` + `deploy/README.md` | **Disabled** systemd sample + enable instructions. Shipped, never installed. |

### `accumulate()` flow (per-ticker isolation, API-frugal)

```
accumulate(tickers, sources, root, *, force=False, max_tickers=None, today=None) -> AccumulationRun
  day = today or utcnow().date()        # always "now"; no past-day writes
  for ticker in tickers[:max_tickers]:
      if not force and is_captured(ticker, root, day):
          skipped += 1; continue        # idempotent — and BEFORE any API call
      try:
          snap = collect([ticker], sources)[0]   # per-ticker → one bad name can't abort the run
          save(snap, root)                        # atomic
          captured.append((ticker, snap.coverage()))
      except Exception as e:
          failed.append((ticker, redact_secrets(e)))   # never leak a keyed URL
  append run-log; return AccumulationRun(day, attempted, captured, skipped, failed, mean_coverage)
```

- **Existence check precedes collect** so re-runs cost zero API calls — important
  under FMP's 250/day cap.
- **Per-ticker collect** isolates failures (collector aborts a *batch* only if a
  Source raises; sources normally don't, but a daily job must survive the
  unexpected) and bounds spend.
- **`max_tickers`** guards the FMP free-tier cliff (default cap **15** ⇒ ≤195
  FMP calls < 250/day with margin). Documented; overridable for paid tiers.
- **Errors routed through `env.redact_secrets`** before logging (house rule).

### Observability — run-log + status

- `accumulate()` appends one JSON line per run to `<root>/_runs.jsonl`:
  `{ts, day, attempted, captured, skipped, failed, mean_coverage}`. Append-only,
  never read back by the capture path (avoids coupling), purely for monitoring.
- `store_status(root, tickers, min_dates=24)` reports distinct capture dates,
  per-ticker date counts, and **whether the backtest's `MIN_SNAPSHOT_DATES`
  threshold is met** — the direct answer to "can the snapshot backtest run yet?"
  Surfaced by `shortlist-accumulate status`.

## 4. Universe & sources (free-tier honesty)

The harness makes ~13 FMP calls/ticker; FMP free = 250/day ≈ **19 tickers/day**.
Finnhub (60/min) and Yahoo (keyless) are comfortable. So:

- **Default:** the bundled ~12-name watchlist with the existing `fmp,finnhub`
  source chain — real fundamentals, within free limits. Time-series snapshot IC
  becomes possible as dates accrue; cross-sectional stays breadth-gated (the
  backtest already enforces this).
- **Scaling (documented, not default):** a larger universe needs FMP's paid Starter
  tier (~$14–20/mo) or the caching layer, *or* dropping FMP to run
  `finnhub,edgar,yahoo` on more names (value axis goes null — coverage stays
  honest). The CLI exposes `--tickers`/`--watchlist`/`--sources`/`--max-tickers` so
  the operator chooses; the spec does not pretend free-tier scales to a universe.

## 5. CLI

```
shortlist-accumulate run     [--watchlist default | --tickers CSV] [--sources fmp,finnhub]
                             [--root DIR] [--max-tickers 15] [--force]
shortlist-accumulate status  [--root DIR] [--watchlist default | --tickers CSV] [--min-dates 24]
```

`run` prints a per-ticker coverage line + a summary (captured/skipped/failed, mean
coverage). `status` prints distinct dates, per-ticker counts, and the
threshold-met verdict. Neither installs nor enables anything.

## 6. Dormant scheduler artifact

`deploy/shortlist-accumulate.service` (oneshot, runs `shortlist-accumulate run`
from the repo so `.env` is found) and `.timer` (daily, after US close, e.g.
`OnCalendar=*-*-* 22:30:00 UTC`, `Persistent=true`). `deploy/README.md` documents
enabling (`systemctl --user enable --now shortlist-accumulate.timer`) and the
free-tier caveat. **Files only — the work explicitly does not run these.** A banner
in the README and in `shortlist-accumulate run --help` states it is disabled by
default.

## 7. Testing & acceptance

**Unit (offline, `mock`/tmp dirs — no network):**
- **Idempotency:** second same-day `run` skips all (captured count 0, skipped = N),
  and makes **no** new collect calls (assert via a spy/monkeypatched collect).
- **No-backfill:** `is_captured`/`accumulate` only ever key off today; a snapshot's
  saved filename equals `as_of[:10]`; there is no code path to write a past day.
- **Partial-failure isolation:** a collect that raises for one ticker leaves the
  others captured and records the failure (with a **redacted** message — feed a
  fake error containing `?apikey=SECRET` and assert it's scrubbed).
- **max_tickers** cap respected.
- **Atomic save:** `store.save` writes via tmp+replace; a simulated crash between
  tmp-write and replace leaves the prior file intact (no truncated JSON).
- **`captured_days` / `store_status`:** correct distinct-date counts and
  threshold-met verdict (e.g. 24 dates → met).
- **CLI:** arg parsing defaults; `status` on an empty store reports 0 dates / not met.

**Live smoke (one-off, throwaway tmp dir — does NOT enable scheduling):**
- `shortlist-accumulate run --tickers AAPL,MSFT --sources finnhub,yahoo --root /tmp/accum_smoke`
  captures two dated snapshots; a second run skips both. Confirms the real path
  works end-to-end without standing anything up.

**Acceptance:** (1) idempotent daily capture with per-ticker isolation and atomic
writes; (2) point-in-time integrity (today-only, redacted errors); (3) `status`
reports progress toward the backtest's 24-date threshold; (4) the scheduler sample
is present but **disabled/uninstalled**, with the constraint stated in docs and
`--help`; (5) full `uv run pytest` green; (6) a live smoke proves the path without
enabling a schedule.

## 8. House rules

`env.redact_secrets()` wraps every error string the tool logs (collect/HTTP errors
embed keyed URLs). Coverage stays honest — a thin/failed capture is recorded as
such (low coverage, error logged), never silently dropped or fabricated.
