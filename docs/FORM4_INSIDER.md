# Form 4 opportunistic-insider originator — design (2026-07-26)

**Status:** design approved, not implemented. Supersedes the current
`EdgarForm4Signal` (`scout/signals.py` + `scout/edgar_index.py:cluster_buys_from_records`).

**Doc location note:** design specs conventionally live in `docs/superpowers/specs/`, which
is **gitignored** — the mechanism by which two enablement artifacts have already evaporated
(see CLAUDE.md "Measure-first" and `docs/audits/2026-07-12-accruals-leg-disable.md`). This
one is tracked instead, following the `docs/POSITION_MONITOR.md` precedent, because it is the
reference for a multi-session build.

---

## 1. Why

`edgar_form4` is **enabled at weight 1.5 — the joint-highest of any originator — and has
never been measured**: no `preregister/edgar_form4.yaml`, no `_BACKFILL_SPECS` row, no audit.
Three *lower*-weighted originators were disabled by measurement while this one was never
asked to justify itself.

Its implementation is a bare count heuristic with three concrete defects, all directly
observed (`docs/audits/2026-07-26-funnel-composition-audit.md` §4, §5):

1. **No dollar floor whatsoever.** `min_buyers=2` and nothing else. Real ledger emissions
   read *"2 insiders bought $5k"*. Measured against SEC bulk data for 2025Q1, the median
   open-market insider purchase is **$23,700** — the signal is firing on the bottom quartile
   of insider buying by construction.
2. **It reads about half of each day.** `scout.edgar_index_daily_cap: 400` caps documents
   fetched per session against a measured **median 838 Form 4 filings/day (p90 1,498, max
   3,496)**, truncated in index order rather than sampled — a bias we cannot characterise.
3. **No routine/opportunistic split, no role weighting, no 10b5-1 exclusion, no size band.**

Measured headroom: **median 13 issuers/day** carry a ≥$100k open-market purchase (p90 29),
against roughly **2/day** emitted today.

## 2. Evidence base, stated honestly

**Supporting:** Cohen, Malloy & Pomorski, *Decoding Inside Information* (JF 2012) — over half
of all insider trades are predictable "routine" trades with **essentially zero** abnormal
return; stripping them leaves an opportunistic set carrying the predictive power, worth
**82 bps/month** value-weighted. Also Lakonishok & Lee (2001), Jeng-Metrick-Zeckhauser (2003).

**Three caveats that belong in the design, not a footnote:**

- **The effect is probably much decayed.** Published 2012 on data through ~2009.
  McLean & Pontiff (2016) find anomalies decay ~58% post-publication, and this one is
  thoroughly public — 2iQ, VerityData/InsiderScore, Smart Insider and TipRanks all sell this
  exact classification commercially. Expect a fraction of the headline number.
- **The headline is not our strategy.** CMP's 82bps is a **value-weighted long-short over the
  full CRSP universe**. We are long-only, ~10 names/day, no shorting. The published magnitude
  does not transfer.
- **This is NOT an information edge.** Form 4 is public two business days after the trade and
  every vendor parses it within seconds. We are not early. The legitimate claim is **drift
  capture** — post-filing insider drift runs for months, so selectivity matters and latency
  does not. Frame it that way everywhere it is surfaced.

## 3. Scope

**v1 ships:**
- SEC DERA bulk ingest → a per-insider trade-month history index
- routine / opportunistic / unclassified classification
- dollar floor, role weighting, 10b5-1 exclusion
- full daily Form 4 coverage via direct XML parsing
- `preregister/edgar_form4.yaml`, committed **before** any measurement run

**v1 defers:** the backfill cohort run and any verdict. See §9 — this is a dependency, not a
footnote.

**Out of scope:** changing `scoring.score()` (untouched — this is discovery plumbing);
re-enabling any currently-disabled signal; the `providers/_form4.py` per-ticker enrichment
path, which is a different consumer and stays as-is.

## 4. Architecture

Follows the established pure-leaf pattern (`stake.py` 110 lines, `buyback.py` 105,
`thirteenf.py` 293): math and parsing in testable leaves, I/O and orchestration in the signal.

| module | role | I/O? |
|---|---|---|
| `scout/dera.py` (new) | fetch + cache quarterly DERA ZIPs; build the insider trade-month index | yes |
| `scout/insider.py` (new) | **pure** — Form 4 XML → records, `classify_tier`, `strength`, emission assembly | no |
| `scout/signals.py` | `EdgarForm4Signal` rewritten to compose the two | yes |
| `scout/preregister/edgar_form4.yaml` (new) | pre-registered measurement parameters | — |

`scout/edgar_index.py` keeps `fetch_recent_records` for the daily-index walk-back (the
"index not published until ~02:00 UTC → use last published session" fallback is reused
verbatim); `cluster_buys_from_records` is retired.

## 5. Data contract — one definition, both paths

The single most important structural decision. `dera.py` (history) and `insider.py` (live)
**both produce the same record**:

```python
InsiderTxn = {
    "owner_cik": str,     # stable person ID -- the key the whole classification rests on
    "ticker": str,
    "date": date,         # transaction date, not filing date
    "code": str,          # "P" == open-market purchase
    "shares": float,
    "price": float,
    "plan_10b5_1": bool,
    "roles": frozenset,   # {"officer", "director", "tenpercent"}
    "title": str | None,  # free text, e.g. "CFO"
}
```

Live reads the **raw Form 4 XML tags**; history reads the **raw DERA TSV columns**
(`RPTOWNERCIK`, `TRANS_CODE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE`, `AFF10B5ONE`,
`RPTOWNER_RELATIONSHIP`, `ISSUERTRADINGSYMBOL`). Both are **raw** fields, deliberately not
edgartools' normalized view — that normalization layer is exactly what drifted between
versions and silently broke the accruals leg
(`docs/audits/2026-07-12-accruals-leg-disable.md`).

**Guard:** a test parses one real filing through *both* paths and asserts identical
`InsiderTxn` records. This is the concrete defence against live/history definitional drift.

## 6. Classification

CMP-2012: an insider is **routine** if they traded in the **same calendar month for 3
consecutive years**; **opportunistic** if they have ≥3 years of history but no such pattern;
**unclassified** if history is insufficient.

```
routine       -> DROP        (CMP: ~zero abnormal return)
opportunistic -> strength x 1.0
unclassified  -> strength x 0.6
```

The tier is recorded on every emission so a future cohort can score the tiers separately.

**The index must be built from ALL transaction types, not just purchases.** An insider who
sells every March under a standing arrangement is *routine* — that is precisely the noise the
filter exists to strip. Indexing only buys would misclassify most routine traders as
opportunistic and the filter would do nothing.

## 7. Emission and strength

**Unit of emission is the ISSUER, not the trade** (the funnel consumes tickers). Within one
session, all qualifying transactions for an issuer collapse to a single `Emission`.

**A transaction qualifies when:** `code == "P"` ∧ `not plan_10b5_1` ∧ role ∈ {officer,
director} ∧ tier != routine ∧ **that transaction's own value ≥ `min_value`**. The floor is
per-transaction, deliberately *not* an aggregate across insiders — otherwise five routine-
sized $20k trades would clear a $100k bar and reintroduce exactly the noise the floor exists
to remove. An issuer emits if it has **at least one** qualifying transaction.

Strength scales with, all **unfitted priors** (say so in the config comment):
- number of distinct qualifying buyers in the same issuer (the cluster bonus — retains the
  old signal's shape as a *bonus*, not a gate)
- dollar size, and size relative to market cap where available (materiality)
- role weight (CFO-type titles above CEO-type above other — Wang-Shin-Francis 2012)
- tier multiplier from §6

Expected ~6–8 emissions/day. With `wsb_hype` now off, raw funnel flow is ~5–9/day, so this
roughly restores flow while changing its composition from mega-cap chatter to material
insider buying.

## 8. Config

```yaml
scout:
  form4:
    min_value: 100000          # PER-TRANSACTION $ floor (never an aggregate -- see §7).
                               # UNFITTED PRIOR. Measured 2025Q1: median buy $23.7k and
                               # 31% of buys clear $100k, so this keeps roughly the top third.
    roles: [officer, director] # tenpercent excluded in v1 (often funds/PE, different animal)
    exclude_10b5_1: true
    tier_strength: {opportunistic: 1.0, unclassified: 0.6}
    daily_cap: 25              # LIVE-ONLY knob; a backfill cohort must run uncapped
    dera:
      quarters: 16             # ~4y history, ~205 MB
      cache_dir: .cache/dera
```

Removing the `form4` block must leave behaviour byte-identical to the pre-feature signal —
the invariance convention every other block in this repo follows, pinned by a test.

## 9. Measurement — an explicit dependency, not a footnote

`preregister/edgar_form4.yaml` is committed **before** any run (the anti-p-hacking guard).
The cohort itself is **blocked** on the evaluator's level bias.

State of the instrument as of 2026-07-26
(`docs/audits/2026-07-26-funnel-composition-audit.md` §3a, §4):

- ✅ event-level bootstrap CI — fixed
- ✅ smooth-path calendar-time portfolio — fixed
- ❌ **residual level bias — NOT fixed.** The corrected 13D raw alpha is +3.04%/mo (+43%/yr),
  which is not a credible number. Prime suspect is bid-ask bounce in an equal-weighted,
  monthly-rebalanced microcap book (Blume-Stambaugh 1983;
  Asparouhova-Bessembinder-Kalcheva 2013).

**Until that is fixed, only the double-sort spread is decision-grade, and no KILL verdict may
be issued on a level.** This must be treated as a parallel track. Shipping originators we
cannot evaluate is how this project ends up in three months with a better-looking funnel and
no idea whether it works.

Interim measurement that does *not* depend on the evaluator: the **picks ledger** records
every emission with an as-of price, so live forward returns accumulate from day one, and the
tier field lets opportunistic and unclassified be compared later.

## 10. Testing

- **Pure leaf (`insider.py`):** classification truth table (routine / opportunistic /
  unclassified, including the 3-consecutive-year boundary); strength monotonicity; the
  10b5-1 and dollar-floor gates; malformed XML abstains rather than raising.
- **Cross-path identity:** one real filing parsed via Form 4 XML and via its DERA row →
  identical `InsiderTxn`. (§5 guard.)
- **Index build:** an insider selling every March for 3 years classifies routine; the same
  insider with a gap year does not.
- **Config invariance:** removing `scout.form4` reproduces pre-feature behaviour byte-identically.
- **No network in unit tests** — DERA and XML fixtures committed as small samples.

## 11. Known limits

- Not an information edge (§2). Drift capture only.
- Effect size likely well below the published 82bps (§2).
- Role weights, dollar floor and tier multipliers are unfitted priors.
- `tenpercent` holders excluded in v1 — often funds/PE with different motives; revisit
  with evidence, not intuition.
- DERA lags one quarter, so the most recent quarter's insiders classify against slightly
  stale history. Acceptable: the classification is a multi-year behavioural pattern.
- Reverse splits are not handled in the size band; use nominal prices, never the
  split-adjusted `as_of_price` (the trap found in §4.1 of the audit).
