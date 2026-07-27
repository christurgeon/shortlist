# Form 4 opportunistic-insider originator — design (2026-07-26)

**Status:** IMPLEMENTED 2026-07-27. Supersedes the retired count-heuristic
`EdgarForm4Signal` (`cluster_buys_from_records`) with the rewrite described below:
`scout/dera.py` + `scout/insider.py` + a rewritten `scout/signals.py:EdgarForm4Signal`,
config in `config.yaml: scout.form4`, pre-registration committed at
`scout/preregister/edgar_form4.yaml`. The backfill cohort itself is **deliberately NOT
wired** (see §9 and `TODO.md`) — this doc's design content below remains current.

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
    "issuer_cik": str,    # the COMPANY's CIK -- carried so Emission.cik can be set (see below)
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

**Guard:** a test parses one real filing through *both* paths and asserts the same
`InsiderTxn`. Categorical fields must match exactly; **price is compared with a tolerance** —
DERA rounds `TRANS_PRICEPERSHARE` to 2dp while the XML carries full precision (`24.57` vs
`24.5686` on the fixture filing, found 2026-07-26). Normalising the XML down to 2dp to force
agreement would discard real precision from the live path; the rounding is immaterial against
a $100k floor.

### 5.1 Joint filings are ABSTAINED (amended 2026-07-26)

A Form 4 may carry **several `<reportingOwner>` blocks** (a fund and its affiliated director,
a family group). Neither the XML nor DERA joins a transaction to a *particular* owner — the
filing is made jointly, so per-transaction attribution does not exist in the source. Taking
the first owner, which both parsers did in their first draft, silently assigns every
transaction to one person.

**Measured on 2025Q1** (why this is not a rounding-error concern):

| population | share |
|---|---|
| all Form 4s that are joint | 1.72% |
| Form 4s **containing an open-market purchase** that are joint | **12.05%** |
| the v1 population (P buys ≥ $100k), joint **and** carrying officer/director | **9.5%** |

A 7× concentration in exactly the population this signal reads. Roughly **1 in 10 emissions
would carry a wrong `owner_cik`, and therefore a wrong CMP tier.**

**Rule:** `InsiderTxn` gains `joint_filing: bool` (set when a filing has >1 reporting owner on
either path) and `qualifies()` rejects those transactions. This follows the repo's
abstain-never-guess idiom (`stake.py`, the CUSIP resolver, the gov-contract matcher). The
count must be **surfaced in the signal's `available()` detail, never dropped silently** — the
same rule every other originator follows for truncation.

Signal-wise this costs little: a joint director+10%-owner purchase is a fund-affiliated
transaction, not the individual discretionary trade the CMP effect is about.

### 5.2 Emissions carry the issuer CIK (added 2026-07-26)

`Emission.cik` exists so the **selection ledger can re-resolve a renamed ticker** and so
firehose events can use **CIK-based delisting classification**. `edgar_13f` ships with
`cik=None` and CLAUDE.md records that as a known limit blocking exactly those two things.

We are not repeating it voluntarily: both sources carry the issuer CIK inline
(`<issuerCik>` in the XML, `ISSUERCIK` in DERA), so `InsiderTxn` carries `issuer_cik` and
`emissions_from_txns` sets `Emission.cik`. Retrofitting this after the ledger has entries is
far more expensive than carrying it from day one.

## 6. Classification

CMP-2012: an insider is **routine** if they traded in the **same calendar month for 3
consecutive years**; **opportunistic** if they have enough history but no such pattern;
**unclassified** if history is insufficient or stale.

**Precise semantics (corrected 2026-07-26 during implementation).** The first draft collapsed
"enough history" and "is the pattern routine" into one calendar window, which made a trader
with a *gap year* come out UNCLASSIFIED when they should be OPPORTUNISTIC — the reference
pseudocode contradicted this spec's own worked examples. The two checks are deliberately
separate:

1. **Enough history to judge at all** — ≥ `lookback_years` **distinct** trading years anywhere
   in the record, **and** the most recent trade no more than `lookback_years` years before
   `as_of`. Uses the insider's full history. A gap year should make someone opportunistic, not
   unjudgeable.
2. **Is the pattern routine** — checked only over the strict last `lookback_years` calendar
   years. This is what stops a long-since-ended same-month streak from branding a trader
   routine forever: March 2022–24 evaluated as-of 2026 is **opportunistic**, not routine.

**Deliberate deviation from the strictest CMP reading.** CMP can be read as requiring a trade
in *each* of the past 3 years to classify at all. We require 3 *distinct* years plus recency.
The strict reading would push most sporadic officer/director traders into UNCLASSIFIED and the
filter would do little work.

**MEASURED 2026-07-26 — the open question is answered, and the filter bites hard.** Index
built from 15 published quarters (66,337 insiders); evaluated against the newest quarter's v1
population (P buys ≥ $100k, officer/director, not 10b5-1, not joint; n=887, as-of 2026-03-31):

| tier | n | share | treatment |
|---|---|---|---|
| **routine** | 430 | **48.5%** | **dropped** |
| opportunistic | 171 | 19.3% | strength × 1.0 |
| unclassified | 286 | 32.2% | strength × 0.6 |

**Nearly half the qualifying population is discarded as routine.** That independently
reproduces CMP-2012's own headline — *"over half the entire universe of insider trades are
routine"* — on a completely different sample two decades later, which is meaningful
corroboration that the classifier is working rather than misfiring. Unclassified does **not**
dominate, so the §6 deviation stands as chosen and needs no revisiting.

Sanity check on volume: ~13 issuers/day carry a ≥$100k buy, and dropping 48.5% leaves roughly
6–7/day — matching §7's expected 6–8 emissions/day. Reproduce with
`scratchpad/tiermix.py`.

**`owner_cik` is canonicalised (`.strip().zfill(10)`) on both the index build and the lookup.**
It is the join key between the live path and the DERA-built history, and a padding mismatch
would send *every* insider to UNCLASSIFIED with no error raised — a silent failure. Verified
2026-07-26: all 63,284 DERA CIKs in 2025Q1 are 10-digit zero-padded and 6/6 sampled live XML
filings agree, so canonicalisation is belt-and-braces against a silent mode, not a live bug.

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

**Config-absence contract (corrected 2026-07-26).** The original wording — "byte-identical to
the pre-feature signal" — is **unsatisfiable and has been withdrawn**: this task retires
`cluster_buys_from_records`, so the pre-feature behaviour no longer exists in the code to be
identical to. Worse, the test that claimed to pin it injected an empty fetcher and asserted no
emissions, which would pass against *any* implementation including a deleted one.

The achievable contract, which must be pinned by a test that actually distinguishes
implementations: **with no `scout.form4` block the signal is an explicit no-op** — `scan()`
returns `[]` without fetching anything and without building or downloading the DERA index
(~205 MB), and `available()` returns `(False, "no scout.form4 config")`. An unconfigured
signal must not silently run at wrong strengths (`tier_strength.opportunistic` defaulting to
0.6 instead of 1.0) nor pull hundreds of megabytes.

Historical note, kept because it is the general convention here:  removing a block should leave behaviour equivalent to the pre-feature state —
the invariance convention every other block in this repo follows, pinned by a test.

## 9. Measurement

**Amended 2026-07-26** — an earlier draft called the cohort "blocked pending a level-bias
fix". That was based on an over-generalisation from the *raw* firehose and is withdrawn;
see `docs/audits/2026-07-26-funnel-composition-audit.md` §5.4.

`preregister/edgar_form4.yaml` is committed **before** any run (the anti-p-hacking guard).

State of the instrument as of 2026-07-26:

- ✅ event-level bootstrap CI — fixed (§3a)
- ✅ smooth-path calendar-time portfolio — fixed (§4.5)
- ⚠️ **raw-firehose levels are unusable** — measurable fraction 0.68–0.70, alphas
  uninterpretable (13D raw +43%/yr). Outcome-correlated attrition: names vanish via
  acquisition/delisting, and for event cohorts the disappearances are the *winners*.
- ✅ **scored/gated cohort levels ARE usable** — they clear the 0.90 floor (13D 0.92, 8-K
  0.93) and produce credible figures (13D scored −0.43%/mo, CI [−2.43%, +1.46%]).

**Measurement rule for this signal:**

1. Measure the **scored/gated** cohort. The raw cohort is corroboration only, never the
   decision surface (design R-B5). Do not quote a raw-cohort level.
2. **Check the measurable fraction against the pre-registered floor first**, before reading
   any alpha. If it fails, there is no measurement — full stop. This guard was firing
   correctly all through the 2026-07-26 analysis while the levels were being quoted anyway;
   trust the floor over any narrative built on the numbers.
3. KILL requires an **entirely-negative CI on a floor-clearing scored cohort**. Never a raw
   level, never a bare point estimate (that trigger was removed 2026-07-26).
4. Read the **double-sort spread alongside** the level, never the level alone.

**Expected to pass the floor.** A $100k per-transaction floor plus officer/director roles
biases hard toward real operating companies, which are exactly the names that do not vanish.
If the scored cohort nonetheless fails the floor, that is the trigger to reconsider buying
survivorship-free price data (Sharadar SEP ~$50/mo, Norgate, EODHD) — **not before**, since
the decision-relevant cohorts currently measure without it.

**Live measurement, independent of the cohort:** the picks ledger records every emission with
an as-of price, so forward returns accumulate from day one, and the tier field (§6) lets
opportunistic and unclassified be compared as evidence arrives.

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
