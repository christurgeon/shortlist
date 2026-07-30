# Buyback originator (`edgar:buyback_auth`) — backfill KILL verdict

> **⚠️ SUPERSEDED 2026-07-26 — the KILL is RETRACTED; the verdict is now INSUFFICIENT.**
>
> This document's verdict rested on "alpha 90% CI entirely negative
> (−0.018, −0.0000459)". That CI was invalid. `validate.py` built it by resampling MONTHS of
> an already-flattened calendar-time portfolio — `calendar_time_portfolio` replaces each
> event's whole K-month path with a constant monthly rate, so cross-sectional dispersion was
> averaged away before the bootstrap ran. Re-derived on 2026-07-26 with an **event-level**
> bootstrap (`validate.py:event_bootstrap_alpha`), the scored_gated CI is
> **[−1.72%, +0.13%] — it straddles zero.** The secondary `alpha <= 0` KILL trigger, which
> caught it next, was removed the same day by operator decision (a bare negative point
> estimate is not disproof).
>
> **Current verdict: INSUFFICIENT** — "not shown to work", NOT "shown not to work". The signal
> **stays `enabled: false`**: nothing here argues for turning it on, only that it was never
> disproved. Re-enabling still requires a fresh pre-registered cohort.
>
> Everything below is preserved unaltered as the record of what was run and concluded at the
> time. Full analysis: `docs/audits/2026-07-26-funnel-composition-audit.md` §3a.

**Date:** 2026-07-11 · **Signal:** `edgar:buyback_auth` (scout `edgar_buyback`) ·
**Verdict: KILL** (stays `enabled: false`). Committed evidence of record, mirroring the
`edgar_8k` precedent (`docs/audits/2026-07-08-eightk-composition-audit.md`), because the
raw run artifacts (`scout/backfill/*.jsonl`, `scout/backfill/verdict-*.json`) live under
the gitignored root `/scout/` and are not recoverable from a fresh clone or the
`/opt/shortlist` deploy.

## What was run

```bash
uv run shortlist-scout backfill --signal buyback --start 2022-01-01 --end 2025-12-31
uv run shortlist-scout validate --backfill scout/backfill/buyback-2022-01-01-2025-12-31.jsonl --json
```

Pre-registration: `src/shortlist/scout/preregister/edgar_buyback_auth.yaml`
(`as_of`/`verdict_as_of` 2026-07-09, committed before the run — see the tamper-check note
below). Expected sign POSITIVE (Ikenberry-Lakonishok-Vermaelen 1995; Peyer-Vermaelen 2009),
K=3m, window 2022–2025, FF3 / equal-weight, `min_measurable_frac` 0.90,
`min_independent_blocks` 8.

## Result

588 events assembled; 524 measurable (non-measurable: 52 no_price_series, 11
no_entry_price, 1 unresolved_ticker).

| cohort | verdict | FF3 alpha (monthly) | 90% CI | IR | blocks | measurable frac |
|---|---|---|---|---|---|---|
| **scored/gated** (decision-relevant) | **KILL** | **−0.84%** | **[−1.80%, −0.005%] (entirely negative)** | −1.02 | 16 | 0.96 |
| raw (confirmatory only) | INSUFFICIENT | −0.83% | [−1.66%, −0.02%] | −1.21 | 16 | 0.89 (< 0.90 floor) |

No delisting-sensitivity flip in either cohort. Both cohorts tagged SYNTHETIC
(rank/KILL-only, M1 — survivorship precludes a PROMOTE from backfill).

## Interpretation

The academic buyback-authorization drift did **not** survive this funnel's universe/horizon
(K=3m, 2022–2025) — the scored/gated cohort's FF3 alpha is negative with a 90% CI entirely
below zero. Same outcome as `edgar_8k`. The originator **stays disabled**; do not enable
without a fresh pre-registered cohort. This is the DEFENSIBLE-prior "measure-first" bar
doing its job: the literature justified building the signal, the cohort killed it.

## Audit-trail note (prereg tamper check)

The prereg file was `git mv`'d from `edgar_buyback.yaml` to `edgar_buyback_auth.yaml` on
2026-07-11 to fix a slug-derivation bug. `verify_untampered` reads the file's newest commit
time at its current path, so it now reports 2026-07-11 (the rename), not the 2026-07-09
registration. Live validation is unaffected (rename date ≤ run date). The genuine
registration date remains provable via `git log --follow --format=%cI --
src/shortlist/scout/preregister/edgar_buyback_auth.yaml | tail -1` (→ 2026-07-09T04:38:50)
and the content-pinned `as_of` field. A content-based tamper check is a tracked follow-up
(TODO.md).
