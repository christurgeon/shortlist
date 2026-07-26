# Daily-scan funnel audit — the scorer works, the originators feed it junk (2026-07-26)

**What this is:** a review of 21 committed daily scout sessions (2026-06-03 → 2026-07-24),
the 141-row selection ledger, and the four existing backfill cohorts, run to answer one
question: *what is the highest-impact change to the discovery layer?*

**Headline:** the composite **ranks well inside every measured event cohort** (double-sort
spreads +1.0 to +6.3 %/mo, CIs positive) while **every cohort's level is deeply negative**
(−0.8 to −8.6 %/mo). That is the signature of a **universe-composition** problem at
origination, not a scoring problem. The originators select nano-caps and mega-caps; almost
nothing lands in the $0.3–10B band where a retail-sized book can actually be early.

Committed here (not under the gitignored `docs/superpowers/specs/`) per the CLAUDE.md
"commit the evidence" rule.

---

## 1. What the funnel actually produced

21 sessions, 244 raw candidates → 178 after prefilter → 143 deep-screened → 141 ledger rows.

| catalyst | picks | share | median mkt cap | in $0.3–10B |
|---|---|---|---|---|
| `wsb:hype` | 85 | **60%** | **$310B** | 8% |
| `edgar:activist_13d` | 29 | 21% | **$50M** | 21% |
| `edgar:form4_cluster_buy` | 21 | 15% | $280M | 39% |
| `edgar:13f_new_position` | 6 | 4% | $57B | 33% |

**The funnel is barbelled at the two worst places.** 60% of everything surfaced is Reddit
chatter on mega-caps (AAPL ×4, MSFT, AMD ×3, IBM ×3, GOOG, AMZN, NVDA…) — names where no
informational edge is available to anyone. The next 21% is the 13D firehose, whose median
subject is a **$50M** shell-adjacent nano-cap that the `below_min_mktcap` gate then rejects.
46% of all ledger rows (65/141) were gated — i.e. surfaced, deep-screened at the cost of one
of ten daily FMP slots, then thrown away.

Live originator status as of the last session: `edgar_form4`, `wsb_hype`,
`edgar_activist_13d`, `edgar_13f` running; `yahoo_screener` permanently WAF-blocked on the
VPS; `edgar_8k`, `edgar_buyback`, `edgar_13d_stake_increase`, `finra_short_interest`,
`quiver` disabled. Raw candidate flow is down to **7–13/day**.

## 2. Realized live-pick performance (weak evidence, directionally consistent)

Harvested from the scoreboard sections of the 21 committed `report.txt` files, deduped to
one latest reading per (ticker, evidence):

| catalyst | n | mean vs SPY | hit rate |
|---|---|---|---|
| `wsb:hype` | 82 | −0.6% | 44% |
| `edgar:activist_13d` | 26 | −0.8% | 35% |
| `edgar:form4_cluster_buy` | 17 | −0.5% | **18%** |
| `edgar:13f_new_position` | 6 | −0.3% | 17% |
| **all** | **131** | **−0.6%** | **37%** |

**Caveats, load-bearing:** ~2 months, one market regime, mostly [1m] readings, overlapping
holdings, integer-percent report rounding, and picks re-shown across sessions. This is *not*
evidence any signal is dead — it is only consistent with §3, which is the real evidence.

## 3. The decisive result — positive spread, negative level

From the committed verdict JSONs (`scout/backfill/verdict-*.json`). All four cohorts are
flagged SYNTHETIC (rank/KILL-only, M1) and keyless-reconstructed; treat levels as
directional, not tradeable.

| cohort | events | alpha/mo (scored_gated) | **composite double-sort spread/mo** | spread 90% CI |
|---|---|---|---|---|
| `edgar:activist_13d` | 3,645 | −5.99% | **+2.97%** | [+2.73, +3.17] |
| `edgar:8k` | 1,843 | −8.57% | **+6.26%** | [+3.93, +8.67] |
| `edgar:buyback_auth` | 588 | −0.84% | +0.99% | [−0.04, +1.92] |
| `edgar:8k_negative` | 11,612 | −5.80% | (n/a) | — |

Two facts sit on top of each other here:

1. **Every cohort's baseline is deeply negative.** Four independent event families at −0.8 to
   −8.6 %/mo is not four independent signal failures — it is one shared property of what
   enters the cohort.
2. **Within every cohort the composite sorts, with tight positive CIs.** The scorer is doing
   its job. It is being handed a universe where the *best* it can do is lose less.

**The shared property is entry-price/size composition:**

| cohort | share of events under $5 | median entry price | measured alpha/mo |
|---|---|---|---|
| `edgar:activist_13d` | 33% | $9.24 | −5.99% |
| `edgar:8k` | 27% | $11.90 | −8.57% |
| `edgar:buyback_auth` | **10%** | **$27.72** | **−0.84%** |

**Correction (2026-07-26, same session):** an earlier draft called this relationship
*monotonic*. **It is not.** 8-K has *lower* penny density than 13D (27% vs 33%) yet a *worse*
level (−8.57% vs −5.99%). What the table actually supports is a single contrast — the one
cohort that is clearly not penny-heavy (buyback, 10% sub-$5, median $27.72) is ~7–10× less
negative than the two that are — on **n=3 cohorts**. That is suggestive, not a dose-response
curve, and three points cannot distinguish "size composition" from any other property the
buyback cohort has (it selects firms with cash to return, i.e. profitable ones — an obvious
confound that would produce the same picture).

Gate rate inside the 13D cohort is the better-powered evidence for the mechanism:

| entry price | n | gated | median composite |
|---|---|---|---|
| $0–1 | 186 | **90%** | 25.2 |
| $1–5 | 488 | 83% | 21.4 |
| $5–20 | 683 | 65% | 26.6 |
| $20+ | 627 | 47% | 26.0 |

**This is a hypothesis with moderate support, not a proof.** The confirming test is cheap and
uses machinery that already exists: re-run `shortlist-scout validate` on the existing 13D and
8-K JSONLs with a market-cap/price band applied at cohort-assembly time, and check whether
the level moves toward zero while the spread survives. That should be done before any large
build lands.

### 3a. The error bars in every verdict above are not trustworthy

Found while stress-testing §3, and it is a bigger deal than the composition question.

`validate.py:_ctp_rows` builds the calendar-time portfolio like this:

```python
contribs = [(1.0 + m.ret) ** (1.0 / k_months) - 1.0 for m in held]
r = sum(contribs) / len(contribs)
```

Each event's **whole K-month realized return is geometrically flattened to a constant monthly
rate, then assigned to every month of its holding window.** The monthly series therefore has
no price-path variation at all — the only month-to-month movement comes from which events
enter and leave the held set. Two consequences:

1. **Residual variance is far too small, so IR and every CI are inflated.** Backing the
   implied monthly tracking error out of the published `(alpha, ir)` pairs:

   | cohort | implied monthly TE | plausible for this book |
   |---|---|---|
   | `13d` raw | **0.32%** | 3–8% |
   | `13d` scored_gated | **1.04%** | 3–8% |
   | `8k` scored_gated | 3.67% | 3–8% |
   | `buyback` scored_gated | 2.85% | 3–8% |

   An information ratio of −46.97 is not a finding, it is an artifact. The distortion is
   worst where the cohort is largest (13D, 3,645 events), because averaging more constant
   per-event rates smooths the series further.

2. **The KILL rule is weaker than it looks — and not only because of the CI.** `decide()` has
   *two* kill triggers, and the second one carries no uncertainty at all:

   ```python
   elif verdict == "HOLD" and ci is not None and ci[1] < 0:
       verdict = "KILL"                       # CI entirely negative
   elif verdict == "HOLD" and alpha is not None and alpha <= 0:
       verdict = "KILL"                       # bare point estimate <= 0
   ```

   **Any negative point estimate kills a signal**, regardless of how wide the interval is.
   On noisy data roughly half of all genuinely-null signals will have a negative point
   estimate, so this rule kills about half of everything that has no real effect. It may be
   deliberate — the docstring says "Kill is cheap; promote is out of scope for v1" — but it
   means "KILL on evidence" in the existing audits overstates what was actually established.

**Fix applied (this session):** `alpha_ci` is now built by `event_bootstrap_alpha`, which
resamples the **events** with replacement and rebuilds the CTP inside each replicate (drawn
events are relabelled so the CTP's same-ticker dedup cannot silently discard a duplicate
draw). Falls back to the month bootstrap when a cohort carries no event list. `ir` now
carries a permanent "upward-biased — display only" note. Tests:
`test_scout_validate_stats.py::test_event_bootstrap_ci_is_wider_than_month_resampled_ci_on_a_dispersed_cohort`
and two in `test_scout_validate_verdict.py`. Two pre-existing verdict fixtures hand-built
`ctp_rows` that contradicted their own `measurement.events`; they now build both from one
cohort.

**Re-derived verdict — `edgar_buyback_auth` (2026-07-26):**

| cohort | before | after |
|---|---|---|
| raw | INSUFFICIENT · α −0.83%/mo · CI [−1.66%, −0.02%] | INSUFFICIENT · α −0.85%/mo · CI [−1.49%, −0.20%] |
| scored_gated | **KILL** · α −0.84%/mo · CI [−1.80%, **−0.00%**] | **KILL** · α −0.82%/mo · CI [−1.72%, **+0.13%**] |

The scored CI now **straddles zero**, so the CI trigger no longer fires — but the verdict is
unchanged, because the bare point-alpha rule catches it instead. The earlier prediction that
this KILL would soften was half right: the stated *reason* in
`docs/audits/2026-07-11-buyback-backfill-kill.md` ("90% CI entirely negative") is no longer
true, while the verdict stands on a weaker rule. Buyback's CI barely widened (1.80 → 1.85pp)
because its implied TE was already plausible; the 13D cohort (implied TE 0.32%) is the severe
case and is being re-derived separately.

**Operator decision (2026-07-26): the point-alpha trigger was REMOVED.** A negative point
estimate whose CI spans zero now yields INSUFFICIENT with the note "point alpha …/mo <= 0 but
CI spans zero — inconclusive". KILL now requires the CI to be entirely negative.

### 3b. All four cohorts re-derived (2026-07-26)

| cohort | before | after | changed? |
|---|---|---|---|
| `edgar:activist_13d` raw | INSUFF · −4.37%/mo · CI [−4.48, −4.26] (0.22pp) | INSUFF · −4.45%/mo · CI [−5.22, −4.47] (0.75pp, **3.4× wider**) | label no |
| `edgar:activist_13d` scored | INSUFF · −5.99%/mo · CI [−6.43, −5.61] | INSUFF · −6.15%/mo · CI [−6.92, −5.70] | label no |
| `edgar:8k` scored | **KILL** · −8.57%/mo · CI [−9.86, −7.36] | **INSUFF** · −8.53%/mo · CI [−10.21, −7.15] | **yes — but not for the CI** |
| `edgar:buyback_auth` scored | **KILL** · −0.84%/mo · CI [−1.80, −0.00] | **INSUFF** · −0.82%/mo · CI [−1.72, **+0.13**] | **yes — KILL retracted** |

Three distinct outcomes, and they must not be conflated:

1. **`edgar:buyback_auth` — the KILL is genuinely RETRACTED.** Its CI now straddles zero. The
   ILV/Peyer-Vermaelen drift was never *disproved* in this funnel; it was never *established*.
   `docs/audits/2026-07-11-buyback-backfill-kill.md` now carries a SUPERSEDED header. The
   signal stays `enabled: false` — "not shown to work" is not a reason to turn something on.
2. **`edgar:8k` — the negative evidence is UNCHANGED and is the strongest in the set.** Its CI
   is still entirely negative ([−10.21%, −7.15%]) and *widened* under the honest bootstrap
   without approaching zero. Its verdict label moved to INSUFFICIENT for an unrelated,
   fragile reason: a vintage-stratified measurability floor (2023: 0.89 vs a 0.90 floor;
   n_measurable drifted 400 → 396 on re-fetch). **Do not read this as 8-K being rehabilitated.**
3. **`edgar:activist_13d` — the negative level SURVIVES the fix.** This is the cohort whose
   error bars were most distorted (implied TE 0.32%/mo), and it is the one that matters most
   for §3's thesis: at −4.45%/mo with CI [−5.22%, −4.47%] after a 3.4× widening, the nano-cap
   13D firehose really does lose money. That **strengthens** the composition argument rather
   than undermining it — the negative level was not an artifact, and the composite still sorts
   inside it (+2.92%/mo spread).

**Known remaining gap:** `double_sort`'s `spread_ci` still uses the month-resampled bootstrap,
so the spread CIs quoted in §3 remain too tight. No verdict reads `spread_ci` (it is display
only), but the digest shows it. Fixing it needs a per-bucket event resample — not done here.

---

## 4. STOP — the measured LEVEL is sign-flipped. §3's thesis is unsupported.

Running §3's own confirming test (item 1) broke the analysis that motivated it. Recorded in
full because it invalidates this document's headline.

### 4.1 What the test showed

Re-validating the 13D cohort under an entry-price band, first on the stored `as_of_price`
and then — after discovering that field is **split-adjusted**, so serial reverse-splitters
like `LGMK` "enter" at $18,487.50/share — again on true nominal prices via
`PriceHistory.nominal_close_asof`:

| band | n | FF3 alpha/mo |
|---|---|---|
| ALL (no band) | 2,256 | −4.45% |
| ADJUSTED ≥ $5 / ≥ $20 | 1,513 / 681 | −5.21% / −8.39% |
| **NOMINAL ≥ $5 / ≥ $20** | 1,533 / 719 | **−5.20% / −8.16%** |

Removing cheap stocks made the measured alpha **monotonically worse**, the opposite of §3's
prediction, and nominal banding reproduced the adjusted result almost exactly — so reverse
splits were not the explanation either.

### 4.2 Why — `calendar_time_portfolio` cannot measure a level

Comparing the cohort's **actual** returns against what the evaluator reports:

| band | n | mean 12m return | median | monthly-geo of the mean | CTP mean/mo | ratio |
|---|---|---|---|---|---|---|
| ALL | 2,256 | **+7.0%** | −22.4% | **+0.56%** | **−4.46%** | **−7.9×** |
| NOM ≥ $5 | 1,533 | −18.6% | −24.6% | −1.70% | −5.20% | 3.1× |
| NOM ≥ $20 | 719 | −34.6% | −45.3% | −3.47% | −8.22% | 2.4× |

**The full 13D cohort's mean 12-month return is POSITIVE (+7.0%), and the evaluator reports
−4.46%/mo (≈ −42%/yr).** The sign is flipped.

The cause is the same line as §3a, but a first-order defect this time, not a variance one:

```python
contribs = [(1.0 + m.ret) ** (1.0 / k_months) - 1.0 for m in held]
r = sum(contribs) / len(contribs)
```

**Precise statement of the defect (an earlier draft of this section got the mechanism
slightly wrong and is corrected here).** It is *not* simply "Jensen's inequality on averaging
compounded quantities" — that framing is misleading, because if every name really did move at
a constant rate, the old average would be the *correct* return for a monthly-rebalanced
portfolio. The actual defect is narrower and worse:

> The code **fabricates a smooth price path** for every event — a constant
> `(1+ret)**(1/K)-1` every month — and a calendar-time portfolio is **equal-weighted and
> rebalanced monthly**, which is acutely sensitive to path shape.

A name that collapses 90% in one month and then sits flat is modelled as declining steadily
for K months. Under monthly rebalancing that means the portfolio keeps buying into the
fabricated decline and eats the drag K times instead of once. The bias is largest where paths
are most jagged — i.e. exactly the microcap-heavy event cohorts this funnel produces. A
calendar-time portfolio's month-*t* return must be the mean of held names' **actual month-*t*
returns**.

This explains everything §3 treated as signal:

- why all four cohorts landed at −0.8 to −8.6%/mo;
- why `buyback_auth` (fewest catastrophic losers) was least distorted at −0.84%;
- why the ≥$20 band looked *worse* — that slice holds more near-total losses, so the bias
  is larger. Not higher-priced 13D targets performing worse; the aggregator amplifying them;
- why the "penny density" relationship in §3 was noisy and non-monotonic — the real driver
  is extreme-loss density, which price level only loosely proxies.

### 4.3 What this retracts

- **§3's headline is WITHDRAWN.** "Every cohort's level is deeply negative" is an artifact.
  The composition thesis is **neither confirmed nor refuted** — the instrument cannot measure
  a level, so the test that would decide it does not exist yet.
- **Every level-based verdict is void**, including the ones re-derived in §3b today. The
  `edgar:8k` "entirely negative CI" and the `edgar:activist_13d` −4.45%/mo both inherit this.
- **§3a's CI fix remains correct but is second-order** — it fixed the error bars around a
  point estimate that is itself wrong.
- **The double-sort spread is the one survivor**, and only because it is a *difference*
  between two cohorts measured identically, so a common bias largely cancels. That the
  composite sorts inside a cohort (+2.92 to +7.22%/mo) is the only §3 claim still standing.

### 4.4 What still stands, independent of all of this

Nothing in §1, §4 or §5 touches the evaluator: the funnel really is 60% WSB at a $310B median
market cap; `edgar_form4` really is unmeasured at the joint-highest weight, with no dollar
floor, reading ~48% of a median Form 4 day; the DERA dataset really does carry what the
rebuild needs. The **reasons** for the `edgar_form4` rebuild survive; the **evidence framing**
around cohort levels does not.

### 4.5 Fix applied, and what it changed (2026-07-26)

`MeasuredEvent` gained `monthly_rets` (appended after `immature`, preserving its positional
slot); `measure_cohort` populates it via `_monthly_path`, which reads the real price series
month by month and returns None if any leg is missing (so a partly-imputed path can never be
half-real — the caller falls back to the old constant). `calendar_time_portfolio` uses the
real month-*i* return when present.

**Correctness check:** for all 2,254 measurable 13D events carrying a path,
`|Π(1+rᵢ) − (1+ret)| ≤ 1.07e-14` — the reconstructed path compounds back to the independently
measured total return on every event. 2 events fall back to the old constant.

**Effect on the 13D cohort:**

| cohort | smooth-path (old) | real-path (new) |
|---|---|---|
| raw | −4.45%/mo, CI [−5.22, −4.47] | **+3.04%/mo**, CI [+1.79, +5.83] |
| scored_gated | −6.15%/mo, CI [−6.92, −5.70] | **−0.43%/mo**, CI [−2.43, +1.46] |
| double-sort spread | +2.92%/mo | +2.42%/mo |

The raw sign **flips**, which is what the +7.0% mean 12-month return said it must. The scored
cohort collapses to approximately zero with a CI spanning it. The composite's sorting spread
survives at +2.42%/mo — the one §3 claim that was ever safe.

### 4.6 Do NOT now trust the new levels either

**+3.04%/mo is +43%/yr. That is not a credible alpha**, and it should be read as evidence that
a *residual* level bias remains, now pointing the other way. The prime suspect is well
documented: an equal-weighted, monthly-rebalanced portfolio of illiquid microcaps earns a
spurious premium from bid-ask bounce — rebalancing systematically buys at the bid and sells at
the ask on noise (Blume-Stambaugh 1983; Asparouhova-Bessembinder-Kalcheva 2013). The 13D
cohort is exactly that population.

So the honest state of the instrument is: **the two known first-order defects are fixed, and
the levels are still not decision-grade.** §5 settles why.

---

## 5. Why levels are structurally unmeasurable here — and why to stop trying

A bounded experiment was run to decide whether a weighting correction could rescue the
levels. **The answer is no, and the reason is not weighting.**

### 5.1 The band test

Under the fixed CTP, the 13D raw alpha by nominal entry price:

| band | n | alpha/mo | annualised |
|---|---|---|---|
| ALL | 2,256 | **+3.04%** | +43.2% |
| nominal ≥ $5 | 1,533 | −1.82% | −19.8% |
| nominal ≥ $20 | 719 | −4.15% | −39.9% |

Strongly positive with cheap names in, strongly negative with them out. Neither end is
credible. Dropping 5-letter `*F`/`*Y`-style untradeable OTC tickers (2.8% of events, e.g.
`FMTOF` quoted at $5,831/share, `TIRXF` at $320) moved the number by **0.13pp** — so the
contamination is not the driver either.

### 5.2 The actual blocker: outcome-correlated attrition

| reason an event leaves the cohort | n | share |
|---|---|---|
| measured | 2,269 | 62.2% |
| **no price series** | **783** | **21.5%** |
| immature | 394 | 10.8% |
| **unresolved ticker** | **131** | **3.6%** |
| no entry price | 67 | 1.8% |

Missing-price rate by event year: **2022 33.7% · 2023 31.3% · 2024 20.5% · 2025 14.1%.**

Monotonic in age. That is names **disappearing** — acquired, delisted, renamed. For a 13D
cohort this is maximally damaging: forcing a sale is a common *successful* outcome of an
activist campaign, so the acquisitions — the winners, at a premium — are exactly the events
that vanish from the sample. A quarter of the cohort is missing, and the missingness is
correlated with the outcome.

**No weighting scheme, factor model, or bootstrap fixes non-random attrition.** Correcting it
needs point-in-time delisting and acquisition returns (CRSP-style), which free Yahoo cannot
provide. This is not a bug to fix; it is the boundary of the data, and CLAUDE.md's design
premise already names it: *"we validate on free-tier, survivorship-biased, currently-listed
names."*

### 5.3 The guard was already right, and I ignored it

The pre-registration's `min_measurable_frac: 0.90` exists for precisely this. The 13D cohort
measures 0.62–0.70 and is therefore INSUFFICIENT — *not* because the alpha was weak, but
because the sample cannot support any alpha. **That guard was firing correctly the whole
time.** §3 of this document quoted the levels anyway and built a thesis on them. The system's
own floor was the thing to believe.

### 5.4 Standing conclusions

1. **Do not build the ABK / value-weighting correction.** It addresses a real but secondary
   bias and cannot touch the attrition problem. Deleted from the roadmap.
2. **Never quote a cohort alpha whose measurable fraction is below the floor.** The evaluator
   currently reports the level *and* the INSUFFICIENT verdict side by side, which is how this
   analysis went wrong. Recommended change: suppress the level (or mark it unusable) whenever
   the floor fails, so the number cannot be read as evidence.
3. **The double-sort spread is the decision-grade statistic**, because it compares two buckets
   drawn from the same attrition-affected pool, so the common bias largely cancels.
4. **Level-based KILL verdicts are retired on this data.** Signals earn or lose their place on
   the within-cohort spread and on the live picks ledger, not on cohort alphas.

**What survives:** the *point estimates* are still roughly the cohorts' mean realized returns,
so the **signs** in §3 — negative levels, positive composite spreads — are probably real. The
magnitudes, CIs, IRs, and the confidence attached to the KILL verdicts are not. Nothing in §1,
§2, §4 or §5 depends on this code path.

**This needs its own fix before any further signal is killed or promoted on these numbers.**

## 4. The originator with the worst evidence-to-weight ratio

`edgar_form4` is **enabled at weight 1.5 — the joint-highest in the config — and has never
been measured.** There is no `preregister/edgar_form4.yaml`, no `_BACKFILL_SPECS` row, no
audit. Three *lower*-weighted originators were killed or shelved by measurement while the
highest-weighted one has never faced the same bar. That is the clearest governance hole in
the discovery layer.

Its implementation (`scout/edgar_index.py:cluster_buys_from_records`) is a bare count
heuristic:

- **No dollar floor at all.** `min_buyers=2` and nothing else. Real emissions from the
  ledger: *"2 insiders bought $5k"*, *"4 insiders bought $6k"*, *"2 insiders bought $5k"*.
- **No routine/opportunistic split**, no role weighting, no 10b5-1 exclusion, no size band.
- **It sees under half the universe.** `scout.edgar_index_daily_cap: 400` caps documents
  fetched per session. Measured from SEC bulk data, Form 4 filings/day in 2025Q1 were
  **median 838, p90 1,498, max 3,496** — so the scanner reads ~48% of a median day and ~27%
  of a busy one, truncated in index order rather than sampled.

## 5. The fix, and why the data is already sitting there

SEC DERA publishes **Insider Transactions Data Sets** — quarterly ZIPs of every Form 3/4/5
since 2006, ~12.8 MB each (verified live 2026-07-26; `2026q1` published, `2026q2` not yet).
Four years is ~205 MB. Contents verified by download:

- `SUBMISSION.tsv` — `ISSUERTRADINGSYMBOL` **inline** (no CIK→ticker resolver needed, and
  it is genuinely point-in-time as-reported), `FILING_DATE`, `DOCUMENT_TYPE`, and
  **`AFF10B5ONE`** (the 10b5-1 checkbox, mandatory since Dec-2022).
- `REPORTINGOWNER.tsv` — `RPTOWNERCIK` (a stable person ID across years — this is what makes
  the routine/opportunistic classification computable), `RPTOWNER_RELATIONSHIP`,
  `RPTOWNER_TITLE`.
- `NONDERIV_TRANS.tsv` — `TRANS_CODE`, `TRANS_DATE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE`,
  `TRANS_ACQUIRED_DISP_CD`.

Measured on 2025Q1 (one quarter, 63,284 submissions):

- **5,185 open-market purchases** (code `P`, acquired) across **983 distinct tickers**.
- Buy sizes: p25 **$2,605** · median **$23,700** · p75 $160,000 · p90 $1.2M · p99 $17.1M.
  The current signal's "$5k cluster" emissions sit in the **bottom quartile** of insider
  buying — it is surfacing noise by construction.
- Issuers/day with **any** open-market insider buy: **median 32**.
- Issuers/day with a **≥$100k** open-market buy: **median 13, p90 29** — sized almost exactly
  to the 10/day deep-screen budget, and roughly **6× the ~2/day the current signal emits**.
- 10b5-1 flagged: 3.9% of buys. Roles parse cleanly (Director 1,450 / TenPercentOwner 1,030 /
  Officer 735 / combinations).

The literature this operationalizes is the strongest available on free data: Cohen, Malloy &
Pomorski, *Decoding Inside Information* (JF 2012) — over half of all insider trades are
predictable "routine" trades with **essentially zero** abnormal return, and stripping them
out leaves an opportunistic set worth **82 bps/month** value-weighted. Supporting:
Lakonishok & Lee (2001), Jeng-Metrick-Zeckhauser (2003).

It is also the free reconstruction of what the sell-side charges for: 2iQ (FactSet
marketplace), Smart Insider, and VerityData/InsiderScore all monetize exactly this pipeline —
clean the transactions, strip the noise, apply behavioural flags (cluster buys, buy/sell
inflections, cessation of selling), emit a score.

## 6. Recommendation

**#1 — Rebuild `edgar_form4` as an opportunistic-insider originator, backed by the DERA bulk
set for a real pre-registered backfill.** Dollar floor + role weighting + 10b5-1 exclusion +
routine/opportunistic classification + a size band. This upgrades the highest-weight enabled
originator, closes the measurement hole, uses a feed already in the chain, is keyless and
VPS-safe, and is the one change that natively selects the $0.3–10B band instead of hoping the
gate cleans up afterwards.

**#2 — Fix funnel composition (hours, not weeks).** Demote `wsb_hype` from originator to
confirmation-only (the `social_hype` flag already exists and covers the use case), and apply a
market-cap band at *prefilter* rather than at *gate*, so nano-cap 13D rows stop consuming
deep-screen slots they will never survive. Together these free roughly 6 of 10 daily slots.

**#3 — Defer, but keep:** a materiality-scaled government-contract-award originator
(USAspending daily; award ≥ X% of TTM revenue). The matcher and source already exist, it is
free and VPS-safe, it is aligned with the defense/AI-infrastructure capex cycle, and
materiality scaling naturally targets the small/mid band. Ranked below #1 only because #1 has
better literature and a far cheaper backfill.

**Do first, before any of it:** the §3 confirming test — re-validate the existing 13D/8-K
cohorts under a size band. If the level does not move toward zero, the composition hypothesis
is wrong and #1 and #2 both need rethinking.
