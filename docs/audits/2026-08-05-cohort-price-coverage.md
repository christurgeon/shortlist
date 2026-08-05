# Cohort price-coverage audit — does better price history change any verdict? (2026-08-05)

**Purpose (plan Phase 0.1):** decide whether a paid one-month price backfill is justified,
by measuring whether committed cohort verdicts are limited by *price coverage* (fixable by
buying data) or by *attrition* (not fixable, and which the double-sort spread is claimed to
cancel).

**Status: PARTIAL.** Four of five cohorts answered. The fifth (`8k-neg`, the only one whose
*decision surface* sits below the floor) is **unmeasured** — the replay was abandoned to
protect production, see §5.

---

## 1. The stored verdicts cannot answer this question

`n_no_price_series` — the field that splits COVERAGE from ATTRITION — **is absent from every
committed verdict** (`scout/backfill/verdict-*.json`, `/opt/shortlist/scout/validate-latest.json`).
It postdates them; it was added in the 2026-08-04 evaluator-guards work precisely because
conflating the two is "how the 2026-08-03 12pp claim was published" (`validate.py`).

So the split cannot be recovered retroactively from artifacts. **It requires re-running the
evaluator**, which is what §3 does.

## 2. Decision-surface coverage across all committed cohorts

Per R-B5 the **`scored_gated`** cohort is the decision surface, not the raw firehose.
From the stored verdicts:

| cohort | raw frac | **scored_gated frac** | vs 0.90 floor |
|---|---|---|---|
| `edgar:activist_13d` | 0.70 | **0.94** | clears |
| `edgar:13d_stake_increase` | 0.72 | **0.94** | clears |
| `edgar:8k` | 0.71 | **0.94** | clears |
| `edgar:buyback_auth` | 0.89 | **0.96** | clears |
| `edgar:8k_negative` | 0.63 | **0.88** | **BELOW** |

**Four of five decision surfaces already clear the floor.** Only `8k-neg` is coverage-limited
where it matters — and `8k-neg` is the *veto* half, already shipped ON as the defensible
side, whose expected sign is negative (a KILL-shaped result CONFIRMS the veto).

The raw cohorts are far below the floor, but they are explicitly **not** the decision surface.
Buying data to raise them would improve a number nobody is allowed to decide on.

## 3. Buyback replay — the coverage/attrition split, measured

`shortlist-scout validate --backfill scout/backfill/buyback-2022-01-01-2025-12-31.jsonl
--as-of 2026-07-26 --json`

- **raw** (n=588): `measurable_fraction` 0.88. Of the 69 unmeasured, **54 had NO price series
  (COVERAGE) and 15 had a series but no return at the horizon (attrition)** — so ~78% of the
  raw shortfall is coverage.
- **scored_gated** (n=246): `measurable_fraction` **0.959**; double-sort `high_frac` 0.9706 /
  `low_frac` 0.9696 — **0.1pp apart**, `bucket_below_floor: false`. The buckets are measured
  alike, which *supports* the attrition-cancels assumption rather than merely asserting it.

**Conclusion:** the coverage shortfall is concentrated in the raw firehose. On the decision
surface it is immaterial.

## 4. ⚠ The committed buyback KILL does not reproduce — attribution NOT yet established

`docs/audits/2026-07-11-buyback-backfill-kill.md` records **−0.84%/mo, 90% CI entirely
negative → KILL**. The replay above gives:

> **−0.14%/mo, CI [−1.21%, +0.72%] — spans zero → INSUFFICIENT**, noted "point alpha
> −0.0014/mo <= 0 but CI spans zero — inconclusive".

**Do not yet call this a retraction.** The replay differs from the original in *three* ways
and they are not disentangled:

1. **Evaluator changes** since 2026-07-11 — #151's `monthly_rets` fix and the issuer-bootstrap
   change, which are exactly what forced the 2026-08-03 re-derivation of the two 13D claims.
2. **A different `--as-of`** (2026-07-26 vs ~2026-07-11): 15 extra days of maturity changes
   which events resolve at the K=3m horizon.
3. **A different price-cache day**, hence a different measurable set.

The 2026-08-03 re-derivation covered 13D and 13D/A and confirmed 8-K; **it did not cover
buyback**. So this cohort plausibly carries the same un-re-derived defect. Attributing it is
Phase 0.2 work — a like-for-like replay pinned to the original as-of.

**Live impact: none.** `edgar_buyback` is disabled either way; INSUFFICIENT does not promote.
The defect, if confirmed, is in the *documented verdict*, not in production behaviour.

## 5. Why `8k-neg` is unmeasured — and a self-inflicted incident

Replaying `8k-neg` needs **~2,884 Yahoo fetches** (4,078 unique tickers, 1,194 cached). A
bounded 60-ticker probe at ~1 req/s was substituted to estimate the split cheaply.

**It was WAF-blocked on the first request** (`HTTP 429 text/html`) and aborted immediately.

**The IP is currently blocked on the Yahoo *chart* endpoint** — not just the screener. The
likely cause is cumulative: an earlier hand-probe of the screener this session, plus the ~44
fetches the buyback replay in §3 issued. **Production depends on this endpoint** (the picks
scoreboard and the harness price merge), so the 22:30 run may degrade if the block persists.
No further Yahoo requests were made after the abort.

### The lesson is broader than "don't probe the screener"

The standing caution was about the *screener*. This shows that **any cohort replay is a
Yahoo-load event**, because `validate` fetches full history per uncached ticker. Two
consequences:

- **The evidence base and production share one WAF-protected free dependency.** Re-deriving
  evidence can degrade the live run. That is a structural conflict, not bad luck.
- **Delisted tickers are re-fetched on every run, forever.** `backtest/prices.py:fetch_history`
  calls `resp.raise_for_status()` *before* the caching block, so a **404 raises and is never
  cached** — while the code's own comment ("day-cache it, or every re-run re-fetches dead
  tickers and baits the WAF") only covers the *empty-but-200* case. Delisted names are exactly
  what a survivorship-corrected cohort is full of, so this is a systematic WAF-baiting bug.

## 6. Recommendation on the paid backfill

**The coverage argument does NOT justify it.** Four of five decision surfaces clear the floor;
the fifth is a veto already enabled on a defensible prior.

**A different argument might.** §5 shows the evidence base is operationally coupled to a
WAF-protected endpoint that production also needs, and that replays are therefore
self-limiting and not safely repeatable. Paid price history would decouple evidence
re-derivation from production's data path — and would additionally supply **delisted**
history, which Yahoo answers with a 404 and which is precisely the survivorship population the
cohorts need.

That is a **robustness/reproducibility** case, not a coverage case, and it is stronger. It
should be decided on those terms.

## 7. Open items

- ~~Fix the 404-not-cached bug in `backtest/prices.py`~~ — **DONE (2026-08-05).** A 404 now
  synthesizes the empty chart envelope and takes the same day-cache path as an empty-but-200
  body, so a delisted ticker is fetched **once per day instead of once per run**. Everything
  else (429 WAF, 5xx, timeouts) still propagates **uncached** — pinned by
  `test_fetch_history_never_caches_a_429_waf_block`, because caching a block as "empty" would
  fabricate a day of no-data for every ticker fetched during it. That guard is the load-bearing
  half of the fix.
- Like-for-like buyback replay pinned to the original as-of, to attribute §4.
- `8k-neg` split still unmeasured — cheaper now that dead tickers cache, but still ~2,884
  first-time fetches. Run off-hours, never colliding with the 22:30 session.

## 8. Decision taken (2026-08-05)

**Paid price history: APPROVED**, on the §6 reproducibility argument — *not* on coverage.
The purchase is justified by (a) decoupling evidence re-derivation from the WAF-protected
Yahoo endpoint that production also depends on, and (b) supplying **delisted** history, which
Yahoo answers with a 404 and which is the survivorship population these cohorts are built from.
Integration is deferred until the data is actually in hand; nothing here presumes it.
