# Options surface as a `/deep` context line — design + feed evidence (2026-08-24)

**Status:** design, not shipped. Every number below was measured from **oracle-prod** on
2026-08-23/24 — the box's own IP is what blocks Yahoo's screener and `fredgraph.csv`, so
vendor docs are not the answer that matters here.

**One sentence:** add the options market's forward-looking view of a name — implied
volatility against realized, the move priced into the next earnings print measured against
what recent prints actually delivered, and the 25-delta skew — as a **prompt-only `/deep`
context line**, fetched per deep dive from keyless endpoints, never scored, never a flag,
each item carrying a measured cross-sectional reference, and abstaining wherever the quotes
are too thin to mean anything.

---

## 1. The prior position, and precisely what changed

`docs/ASSESSMENT_GAPS.md` §"Net-new clues" already carries an entry:

> **Options-implied signals** — IV rank, put/call skew, unusual-options flow (cf. Unusual
> Whales). *Vet:* no keyless feed known (mostly paid); likely noise for a fundamental
> pre-screen. Justify hard before building.

That entry is **half right, and the half that is right is conceded by this design.**

- **"No keyless feed known" is now measured false.** `cdn.cboe.com/api/global/delayed_quotes/
  options/{TICKER}.json` returns 200, keyless, from oracle-prod: AAPL 3,404 contracts across
  23 expiries in 0.31s, carrying per-contract `iv`, full greeks, `open_interest` and
  `volume`, plus an underlying-level `iv30`. §3 has the coverage numbers.
- **"Likely noise for a fundamental pre-screen" is correct, and this is not one.** This
  design puts nothing on `/screen`, nothing in `composite`, `passed`, `scored` or `flags`,
  and adds no scoring leg. It renders on the `/deep` path only — a name a human has already
  chosen to examine. Those are different surfaces with different bars.
- The measured small-cap result in §4.5 **independently confirms the "noise" judgment** for
  exactly the population where it was aimed, and the design responds by abstaining there
  rather than arguing with it.

`ASSESSMENT_GAPS.md` also sets a gate: *"A new feature should justify why it outranks §2.3
sector-relative scoring and §3.1 earnings-call transcripts."*

**It does not outrank them on leverage. It outranks them on availability.** §2.3 is a
scoring change gated on cross-universe rank IC and blocked on peer-data cost
(`TODO.md` §2c, §4); §3.1 transcripts are recorded as paid-or-no-free-point-in-time-source.
Both are blocked on a resource. This is unblocked and costs **three HTTP requests per
`/deep`** (one options chain, one 8-K index lookback, one daily-close series). It does not
compete with either for budget or for scoring risk, and it should not displace §2.3 in the
queue.

**Required doc fix:** the `ASSESSMENT_GAPS.md` entry must be updated when this ships — its
"no keyless feed known" claim is now false, and a stale authority file is the failure mode
`CLAUDE.md` names explicitly.

---

## 2. Why this is worth building

Everything that reaches a score today is either **accounting the company published about
itself** or **the equity price**. The advisory layer adds short interest (bi-monthly),
analyst ratings, WSB mentions, gov contracts and lobbying. None of it is forward-looking,
and none of it is another market's opinion of the same issuer.

The stock price is a single number — roughly a conditional mean. The options surface prices
the **distribution around it**: expected variance, asymmetry, and *timing*. The funnel has
no access to any of the three.

Three concrete questions a human deep-diving a name asks, which `/deep` cannot answer today:

1. **Is the market pricing more risk than this stock has actually delivered?** `realized_vol`
   is backward-looking by construction; implied volatility is the forward half. They diverge
   materially and routinely.
2. **How big a move is priced into the next print, and how soon is it?** This changes the
   decision — "enter now or wait for the print" is a sizing and timing question, not a
   thesis question.
3. **Is the market paying for crash protection, or for upside?** A cheap-looking value name
   whose options market bids calls over puts is telling you something the 10-K will not.

This complements, and does not duplicate, the shipped `research/reverse_dcf.py` line. Reverse
DCF gives the market's implied *central expectation* (a growth rate); the options surface
gives the *uncertainty and asymmetry* around it. Two halves of "what is priced in," and the
repo currently has only the first.

---

## 3. The feed — liveness and coverage, measured

`GET https://cdn.cboe.com/api/global/delayed_quotes/options/{TICKER}.json` — keyless, no
signup, no key rotation.

Probed over **both committed universes** (`backtest/universe_largecap.txt`,
`universe_smallmid.txt`) with `docs/audits/scripts/probe_cboe_surface.py`, which is
committed alongside this note so every number below is reproducible.

| | large cap (n=80) | small/mid (first 80) |
|---|---|---|
| HTTP 200 | 80 / 80 | 79 / 80 |
| with at least one live expiry | 80 | 77 |
| live expiries (median) | 16 | 4 |
| payload (median / max) | 674 KB / 3.0 MB | 67 KB / 253 KB |
| fetch time (median) | 0.26 s | — |
| files carrying expired contracts | 21 / 80 (max 260) | 17 / 77 (max 108) |

Coverage is **not** the constraint: essentially every name in both universes returns a
chain. Quote *quality* is the constraint, and it is severe (§4.5).

Two other endpoints were probed and are **not** part of this design, recorded so they are
not re-probed: Yahoo `v7/finance/options` now returns **401** (crumb-gated; the `v8` chart
endpoint this repo already uses still works — the endpoint-specific block, again), and
FINRA's TRACE corporate-bond slug returned **404**, so credit data is *unconfirmed* and
must not be assumed free.

---

## 4. Measured constraints — all five are load-bearing

### 4.1 A rolling per-IP request budget, enforced by Cloudflare

A naive `urllib` loop at 0.25s spacing got **60 successes then a hard 429**, with zero
recovery across the following 172 attempts. The response carries
`set-cookie: __cf_bm=` — Cloudflare Bot Management — and `server: cloudflare`.

An `httpx.AsyncClient` that **persists cookies** did **40/40** at 0.5s, and a later run
reached **72** before hitting the same wall. Slowing to **1.0s completed the full 80-name
large-cap universe with zero 429s.** So there are two effects: a bot-management component
that a session client clears, and a rate ceiling that pacing clears. The lockout recovered
within ~1 minute of going quiet.

**Operating rule: one cookie-persisting client, ≥1.0s between requests.** At that pacing 80
consecutive fetches are demonstrably fine, which is far more than any single `/deep` needs.

**Consequences, and they are hard scope boundaries:**

- **This can never be a universe scan.** Any design that sweeps 238 names is dead on
  arrival. Fetching one ticker per `/deep` is comfortably inside the budget; fetching ten
  per `/screen` is not worth the budget it spends (§5).
- The client **must** persist cookies and pace itself. A source that opens a fresh
  connection per request will 429 and look like an outage.
- Do **not** give this its own retry storm. On 429, abstain — the line is optional.

### 4.2 The data is end-of-day, not intraday

`last_trade_time` was **2026-08-21 on all 80** large caps (62 of them at exactly
`15:59:59`, the rest within seconds) — Friday's close, probed on Sunday/Monday. The
file-level `timestamp` field varies per symbol
(GE `2026-08-22 12:20`, WFC `2026-08-24 00:38`, DHR `2026-08-21 23:08`) and reflects when
CBOE regenerated the JSON, **not** when the quotes were taken.

**`last_trade_time` is the only honest staleness anchor.** The rendered line must state it,
and the design must never claim intraday freshness.

### 4.3 Expired contracts are retained in the file

**21 of 80** large-cap files carried contracts whose expiry is already past — up to **260**
of them on one name. This is what produced a nonsense 82% "implied move" on HDSN in the
first ad-hoc probe: the nearest expiry was 2026-08-21, three days before the probe date.

Every consumer **must** filter `expiry >= today` from the OSI symbol before any selection.

### 4.4 Junk IV on one-sided and deep-ITM quotes

AAPL's first contract carried `iv: 2.0684` (207%) at `delta: 0.9998` — a deep-ITM strike
whose stale/wide quote inverts to a meaningless implied vol. Any strike selection must
require a **two-sided quote** (`bid` and `ask` both present) and a positive `iv`.

### 4.5 Small/mid caps: the data exists and is mostly unusable

This is the decisive negative result, and it drives the abstention design.

| | large cap (n=80) | small/mid (n=77) |
|---|---|---|
| ATM bid-ask spread, % of mid (median) | 21.5% | 61.5% |
| … p90 | 41.7% | 133.3% |
| live expiries (median) | 16 | 4 |
| 25-delta selection lands within ±0.10 of target | **80 / 80 (100%)** | **38 / 77 (49%)** |

A bid-ask spread wider than the mid means the "price" is not a price. And the 25-delta
*selection itself* fails on half the small/mid names: with only two or three usable
contracts per side, "nearest to 0.25 delta" lands wherever it can. `RES` produced a **77
vol-point** skew from a put at delta −0.888 against a call at delta 0.869 — a number that
looks like a violent signal and is pure selection artifact.

**Diagnosing that artifact produced the sharpest guard in this design** (§6, *The quote-quality guards*): reject on
the *achieved delta*, not on a contract count.

---

## 5. Where it lands — the research layer, not a `Source`

**Decision: this is a research-layer fetch on the `proxy_facts` precedent, NOT a
`data/sources/` `Source` and NOT a `TickerSnapshot` section.**

`research/assess.py:812` already does exactly this for DEF 14A, with the comment that
states the rule:

```python
# DEF 14A proxy facts — research-layer fetch (per deep-dive, NOT on every screen).
# Failure-isolated: any error → None → the line simply abstains.
```

Rejected alternative — registering a `CboeSource` in `_REGISTRY` and adding an aux section:

- **It would spend the §4.1 budget on data nobody renders.** The line appears only in
  `/deep`; a 10-ticker `/screen` would fetch 10 chains (~7 MB) and display none of them,
  competing for the same per-IP budget the `/deep` path needs.
- **It would bloat the snapshot store.** `TickerSnapshot.raw` is `source → section →
  payload` and `save()` persists `to_dict()`, so raw payloads land in the gzipped store. At
  a 674 KB median this is not a rounding error.
- The aux-section machinery (`_AUX_DEFAULTS`, coverage exclusion, bridge derivation) exists
  to get a signal onto `StockMetrics`. Nothing here needs to be on `StockMetrics`.

The research-layer route touches **no** scoring path, adds **no** `StockMetrics` field,
changes **no** snapshot, and is byte-identical on `/screen` and in the backtest.

### Point-in-time / look-ahead

CBOE serves **only the current end-of-day surface** — there is no history endpoint. A
replay that fetched today's surface for a 2026-06 snapshot would be look-ahead
contamination. `fetch_surface()` therefore takes an `as_of` parameter and **returns `None`
whenever `as_of` is set to anything but today**, mirroring `fetch_proxy`'s look-ahead guard.
Since the line is research-only and never scored, this guards a gap rather than fixing an
observed leak — but the guard is cheap and the failure would be silent.

### The haystack rule

`CLAUDE.md`: *"`/deep` grounding is per-segment. Anything added to the prompt that is not
filing text must stay out of the quote-verification haystack, or a computed value can pass
itself off as a filing fact."*

Options data is **market prices** — the furthest thing from filing text in the whole brief.
It renders **after** the "Return at most …" instruction block, alongside `macro_section`,
`events_line`, `insider_line` and `proxy_section` — never as a `=== … ===` segment. Pinned
by a test mirroring `tests/test_research_insider_detail.py::test_insider_line_not_in_haystack`.

---

## 6. The signals — three, each with its own abstention

Each item abstains **independently**. A name with tradeable IV but an untradeable straddle
renders the IV comparison and drops the implied move — the same discipline as *"a missing
sub-score is excluded and its weight redistributed, never zeroed."*

### 6.1 Implied vs realized volatility — the primary item

`iv30` (CBOE's own headline, no strike selection needed) against `realized_vol`.

**A 30-day implied vol against a 252-day realized vol is a horizon mismatch, and the
obvious fix is the wrong one.** The first draft of this design added a `realized_vol_30d`
field to the Yahoo source to make the windows match. Measuring it killed that idea.

Joining CBOE `iv30` to Yahoo daily closes for all **80** large caps (2026-08-21 quotes),
the ratio distribution depends heavily on which realized window you pick:

| denominator | min | p10 | p25 | **median** | p75 | p90 | max |
|---|---|---|---|---|---|---|---|
| realized 21d | 0.43 | 0.69 | 0.79 | **0.93** | 1.07 | 1.17 | 1.61 |
| realized 63d | 0.55 | 0.69 | 0.74 | **0.83** | 0.92 | 1.01 | 1.16 |
| **realized 252d** (the shipped field) | 0.73 | 0.83 | 0.87 | **0.93** | 0.99 | 1.07 | **1.36** |

The 21-day denominator is **cycle-contaminated**: a trailing 21-day window in late August
contains the Q2 earnings reaction, while the forward 30 days contains no print at all for
most of these names. That is the same horizon-mismatch disease in a subtler form — matching
the *label* on the window while mismatching what the window contains. The 252-day window
spans four earnings cycles and is correspondingly tighter (range 0.73–1.36 against
0.43–1.61).

**So the design uses the existing `realized_vol` and adds no new field.** That removes the
only change outside the research layer, and the whole feature becomes research-layer-only.

**The reference distribution, and a correction it forces.** As with skew, the raw ratio is
uninterpretable alone, so the line prints it against the measured cross-section:

> **Large-cap IV30 ÷ realized-vol(252d) reference, n=80, quotes as of 2026-08-21:**
> min 0.73 · p10 0.83 · p25 0.87 · **median 0.93** · p75 0.99 · p90 1.07 · max 1.36

**The median is below 1.0, and 60 of 80 names price implied *under* realized** on this
denominator (50 of 80 on the 21-day, 71 of 80 on the 63-day — the direction is not an
artifact of one window choice). An earlier
draft of this note asserted the textbook variance-risk-premium claim — that implied moves
run larger than realized ones — as a caveat to print in the brief. On this cross-section
that is **false**, and shipping it would have put a confident wrong statement in front of
the model. The rendered line states the measured reference instead of a textbook prior.

Live-verified on the `/deep` path: a `--provider yahoo` collect on AAPL returns
`realized_vol 0.2504`. **It is absent from the snapshot store (0 of 42 latest snapshots)** —
the deployed accumulate timer runs `--sources fmp,finnhub,edgar`, the same root cause as the
`pe_vs_history` gap in `TODO.md` §3. Harmless here, since this design never reads the store,
but it does mean the line **cannot be replayed** from stored snapshots.

**Abstains** when either side is missing. Takes no liquidity guard — `iv30` is CBOE's own
surface-fitted number and selects no strike.

### 6.2 Implied move to the next earnings print — the flagship

Report the ATM straddle of a post-earnings expiry as a percentage of spot, **against what
this company's own recent prints actually delivered**.

#### The earnings date is not as reliable as its presence suggests

`Earnings.next_date` is present on **42 of 42** latest stored snapshots, and
`m.earnings_days_to_next` already reaches `StockMetrics` (`bridge.py:426`). But presence is
not accuracy, and the first draft of this design checked only presence.

Replaying every stored `next_date` across 42 tickers and 31–48 captures each (~2 months)
gives **14 genuine pre-event revisions** — the predicted date changing while still in the
future:

| |revision| | median **7 days** | max **8 days** | >3 days: **10 of 14** |
|---|---|---|---|

CSCO oscillated between 2026-08-11 and 2026-08-19 **four times**; DIS moved 8 days, ORCL 7,
CRM 6, GOOGL 6, MCD 6. Finnhub's free-tier calendar carries no past entries and its rows
hold only the fiscal `period` (quarter-end), never the print date
(`data/sources/finnhub.py:_earnings`), so there is no way to cross-check it in-stack.

**Why this matters more than it looks:** if the date is revised *later* after we pick the
first expiry following it, that expiry now falls **before** the print. The straddle then
prices no earnings event at all while the line labels it as pricing one — a silent wrong
answer, which is worse than an abstention.

**Guard:** require the selected expiry to be at least `earnings_date_uncertainty_days`
(default **8**, the measured maximum revision) *after* the predicted date, and no more than
`max_earnings_expiry_gap_days` (default **14**) after it. A late revision then cannot
invalidate the choice.

**The buffer is proximity-aware, and that refinement came from implementation.** A constant
8-day buffer is wrong close to the print: it would skip the weekly expiry that actually
straddles a report two days away, which is the case the reader cares about most. Measuring
the *lead time* of each revision settles it — **none of the 14 revisions happened with fewer
than 12 days to go** (leads ran 12 to 36 days). The date firms up as the print approaches,
so inside `earnings_date_firm_within_days` (default **7**, comfortably below the measured
minimum of 12) the predicted date is taken at face value and the buffer drops to zero.
Reproduce with `probe_earnings_timing.py`.

**This is a NEAR-PRINT signal by construction, and that is not a defect.** The listed ladder
is dense near-term and sparse after: AAPL's tradeable expiries run 0, 2, 4, 7, 9, 11, 18, 25,
32, 39, 53 days and then jump to 88, 116, 144. With a print 50-65 days out — where AAPL,
INTC, KO, MSFT and JPM all sat when this was verified — **no listed expiry brackets it**, and
the clause abstains on all five. The weekly that will eventually straddle the print is simply
not listed yet. Live-verified firing on the near cases: NVDA and CRM at 2 days out and ORCL
at 21. Do not "fix" the abstention by widening the gap ceiling — a straddle 30+ days past a
print prices the event plus a month of ordinary drift, which is the failure the ceiling
exists to prevent. JPM is the instructive rejection: its 53-day expiry sits only 3 days after
a 50-day predicted date, so an 8-day slip would put the print *after* the expiry.

#### The anchor: what recent prints actually moved

An implied move alone is uninterpretable — the same trap as skew. The fix is to print it
beside the realized post-announcement moves for the same company.

**8-K Item 2.02 (Results of Operations) is the announcement date, and it is exact.**
Validated against print dates recovered independently from the store's `next_date`
roll-forwards: AAPL, GOOGL and MSFT each matched at **+0 days**, with 6+ quarters of history
available per name. This is free, authoritative, point-in-time, and the repo already has the
EDGAR filings-index machinery.

Realized close-to-close moves spanning the announcement, measured:

| | last six prints (%) | \|median\| |
|---|---|---|
| AAPL | −7.4, +3.2, +0.5, −0.4, −2.5, −3.7 | 3.2% |
| INTC | −7.9, +23.6, −17.0, +0.3, −8.5, −6.7 | 8.5% |
| MSFT | +15.5, −3.9, −10.0, −2.9, +3.9, +7.6 | 7.6% |
| KO | +0.9, +0.7, +2.3, −0.6, −0.7, +0.3 | 0.7% |

Reproduce with `probe_earnings_timing.py --moves AAPL INTC MSFT KO`.

That spread is the whole argument. "±8% implied" means nothing on its own; "±8% implied
against a company that has moved 0.8% on its last six prints" is a finding, and so is the
reverse. It also lets the reader judge the risk premium from this company's own record
rather than from a textbook prior that §6.1 shows is not even directionally safe.

**Cost, and a latency trap found while implementing it.** This is a *second*
research-layer fetcher, not a free rider on the options call: one EDGAR 8-K index lookback
plus a daily-close series. The first implementation issued its own uncached Yahoo
`range=2y` request and added **40-60 seconds to every brief** — Yahoo answers the v8 chart
slowly from a datacenter IP even while returning 200 and 55 KB. EDGAR was never the problem
(the 8-K index lookback is ~0.2s).

The fix is to take nothing new: `data/sources/yahoo.py` already fetches `range=5y&interval=1d`
and day-caches it at `.cache/yahoo/{SYMBOL}-{date}.json`, and it leads the harness merge for
price fields, so on any `/deep` that payload is already on disk. `earnings_moves.daily_closes`
now reads that same cache key with the same params, falling back to a live fetch only on a
`--provider`-narrowed path. **Measured added latency per `/deep`: 0.6-1.6s**, of which the
options chain is ~0.4s. Pinned by a test asserting the cache key matches the price source's
and that a warm cache issues no HTTP call at all.

**Known imprecision, not yet resolved:** an 8-K 2.02 filed after the close reacts on the
next session, one filed pre-open reacts the same day. The close-to-close span above is
correct for after-close filers (most large caps) and shifts by a session for pre-open ones.
The filing's acceptance timestamp can disambiguate this and should be used; it was not
tested here. Spot-checked against reality on NVDA, whose six announcements are **all
Wednesdays** with the reaction landing on the Thursday close, exactly as the span assumes —
pinned by a live test.

**A matching-bug worth recording:** the first draft matched item codes with
`"2.02" in items`, which also fires on **12.02** (a different disclosure) and on 2.021. A
wrong announcement date shifts every realized move computed from it, silently. The shipped
matcher is anchored on both sides, and the probe script carried the same bug until the unit
test for it was written.

**Abstains** on both quote-quality guards. This is the item most exposed to them — it is
priced off premium mids, not IV.

Measured reference, guards applied: implied move at ~30d is p10 4.9% / median 6.5% /
p90 10.8% on large caps.

### 6.3 25-delta skew, against a dated reference

Put IV at ~25 delta minus call IV at ~25 delta, in volatility points, on the expiry nearest
30 days.

A single firm's absolute skew is **uninterpretable without a cross-section** — the same trap
that killed the Lazy Prices cosine (`TODO.md` §2a: *"a single-firm absolute cosine is
uninterpretable regardless"*). So the line always renders the reference alongside the value:

> **Large-cap 25-delta skew reference, n=80, quotes as of 2026-08-21 close:**
> min −3.6 · p10 −0.9 · p25 −0.1 · **median +0.9** · p75 +1.9 · p90 +3.0 · max +5.2
> (vol points; 23 of 80 negative, i.e. calls bid over puts)

This is committed as a dated constant with its `n`, **not** as a percentile function. A
percentile would hide its staleness inside a single number; printing the distribution with
its date lets the reader discount it. It is **regime-dependent** and it will drift — a
volatility shock moves the whole cross-section, not just one name. Re-measure with the
committed probe script; treat a reference older than ~6 months as indicative only. (The
regime prevailing on the measurement date was not captured alongside it; a re-measure
should record the `MacroContext` line with the distribution.)

**The reference is large-cap only.** Even after the delta guard, small/mid skew spans
**−17.8 to +15.9** against the large-cap **−3.6 to +5.2** — more than three times the
spread, on names whose median ATM quote is 61.5% wide. The line names the reference as
large-cap so a small-cap reader can discount it. A market-cap-conditional reference is
**deferred**, not designed: it would need a small/mid distribution that the measurement
says is not yet meaningful.

### The quote-quality guards

Two guards, both derived from measured failures, and they apply to **different** items:

1. **Achieved-delta tolerance** — reject when the selected contract's own delta is further
   than `delta_tolerance` (default **0.10**) from the 0.25 (or 0.50) target. Applies to
   every item that selects a strike: §6.2 and §6.3. This is the guard that kills the
   `RES` 77-vol-point artifact, and it is strictly better than a contract count because it
   tests the thing that actually went wrong.
2. **ATM bid-ask spread** — reject when the spread exceeds `max_atm_spread_pct` (default
   **40%** of mid). Applies to **§6.2 alone**, the one item priced off a premium mid rather
   than off an implied vol. Skew (§6.3) compares *volatilities*, which survive a wide spread
   far better than a mid does. Calibrated on EOD quotes, which is all this feed ever serves.

§6.1 takes neither guard: `iv30` is CBOE's own surface-fitted number and selects no strike.

**Measured pass rates:**

| | large cap (n=80) | small/mid (n=77) |
|---|---|---|
| delta guard only — gates skew (§6.3) | **80 (100%)** | **38 (49%)** |
| delta + spread — gates the implied move (§6.2) | **71 (89%)** | **10 (13%)** |

That asymmetry is the correct behaviour, not a defect — it is §4.5 being enforced rather
than argued with. A `/deep` on a thin small cap renders a reduced options line, or none at
all, and that is a better answer than a confident wrong one.

---

## 7. What this is NOT

Stated explicitly, because the `ASSESSMENT_GAPS` entry was right to be suspicious:

- **Not a scored signal.** Nothing touches `composite`, `passed`, `scored` or `rank_key`.
- **Not a gate and not a flag.** No `KNOWN_GATES` / `KNOWN_FLAGS` entry, so no
  `bot/glossary.py` change and no CI AST-scan involvement.
- **Not on `/screen`.** No `harness_sources` change; `/screen` output is byte-identical.
- **Not in the backtest.** No `StockMetrics` field, no snapshot section, nothing replayable.
- **Not a forecast, and not a textbook prior either.** The obvious caveat to print — "option
  prices embed a risk premium, so implied exceeds realized" — is **measured false on this
  cross-section**: median IV30/realized is 0.93 and 60 of 80 names price implied *under*
  realized (§6.1). The line prints the measured reference instead of the prior.
- **Not intraday, and not a universe scan** (§4.1, §4.2).
- **Not IV rank or IV percentile.** Both need a time series this feed cannot supply — see §10.

---

## 8. The rendered line

Prompt-only, after the instruction block. Illustrative shape, with every item present:

```
Options market (CBOE delayed quotes; quotes as of 2026-08-21 close, 3 days stale).
Implied volatility (30d) 59.7% against 25.0% realized (1-year) — ratio 2.39, versus a
large-cap reference of median 0.93, 10th-90th percentile 0.83 to 1.07 (n=80, 2026-08-21),
so this name is priced for far more movement than its own history has delivered.
Next earnings ~2026-10-23 (date from a vendor calendar, historically revised by up to 8
days): the 2026-11-06 expiry prices a +/-8.4% move from the at-the-money straddle
(bid-ask 12% of mid). The last six reported quarters actually moved -7.9, +23.6, -17.0,
+0.3, -8.5 and -6.7% close-to-close, announcement dates taken from 8-K Item 2.02.
25-delta skew -4.2 volatility points -- calls bid over puts; large-cap reference
(n=80, 2026-08-21): median +0.9, 10th-90th percentile -0.9 to +3.0.
These are MARKET PRICES, not filing facts and not a forecast. Reconcile against the
filing: an implied move far out of line with what this company's recent prints actually
delivered, or a skew inverted versus the reference, is a question about what the market
expects that the MD&A may answer.
```

Voice matches the shipped `gov_contracts` / `inventory` lines: self-disclosing about
provenance, explicit about what it is not, and ending in a reconciliation instruction
rather than a conclusion.

**No system-prompt addendum.** `proxy` ships one (`PROXY_SYSTEM_ADDENDUM`); this does not.
The caveat rides in the line itself, as it does for `gov_contracts`, `lobbying` and
`inventory`. Considered and declined for v1 — revisit only if a brief is observed treating
an implied move as a forecast.

---

## 9. Implementation surface

| File | Change |
|---|---|
| `research/options.py` | **new** — `fetch_surface(ticker, as_of=None)` (sync `httpx.Client`, cookie-persisting, `log_abstain` on failure, never raises) + `context_line(surface, m, cfg)`. Holds the dated reference constant. |
| `research/earnings_moves.py` | **new** — 8-K Item 2.02 announcement dates + Yahoo daily closes → realized post-announcement moves (§6.2). Never raises. |
| `research/assess.py` | fetch both on the `proxy_facts` pattern (~line 812); pass into `_build_user_prompt`; render after the instruction block, never as a segment. |
| `config.yaml` | `research.options` block (§below). |

**No change outside the research layer.** The first draft added `realized_vol_30d` to the
Yahoo source; §6.1's measurement removed the need for it, so `data/sources/`, `data/models.py`
and `data/bridge.py` are all untouched.
| `docs/ASSESSMENT_GAPS.md` | **required** — correct the "no keyless feed known" claim (§1). |
| `docs/RESEARCH.md` | document the new line, per the authority-file rule. |
| `docs/audits/scripts/probe_cboe_surface.py` | commit the probe so the §6.3 reference is reproducible. |

### Config

```yaml
  options:                     # options-surface context line (research-only; prompt-only,
                               # NOT a scored signal, NOT a flag, NOT on /screen).
                               # Feed + guard evidence: docs/audits/2026-08-24-options-surface-design.md
    enabled: true              # flip false for a byte-identical no-op (line omitted)
    timeout: 20                # seconds; a slow chain must never stall a brief
    delta_tolerance: 0.10      # reject a strike whose ACHIEVED delta is this far from target.
                               # Not a contract count: RES produced a 77-vol-point skew from a
                               # put at delta -0.888 vs a call at 0.869 because only 2-3
                               # contracts per side carried usable quotes.
    max_atm_spread_pct: 40     # reject the straddle above this bid-ask % of mid. Calibrated on
                               # EOD quotes (all this feed serves): large-cap median 21.5%,
                               # small/mid median 61.5%.
    earnings_date_uncertainty_days: 8  # the selected expiry must sit at least this far AFTER
                               # the predicted print date. Measured: the vendor calendar
                               # revised a future date 14 times across 42 tickers in ~2 months,
                               # median 7d and max 8d (CSCO oscillated 8-11 <-> 8-19 four
                               # times). Without this, a later revision leaves the chosen
                               # expiry BEFORE the print, pricing no event while the line
                               # says it prices one.
    max_earnings_expiry_gap_days: 14   # ...and no more than this far after it, or the straddle
                               # prices the event PLUS weeks of ordinary drift.
    earnings_lookback_quarters: 6      # realized post-announcement moves shown beside the
                               # implied one (8-K Item 2.02 dates; validated +0d on AAPL/
                               # GOOGL/MSFT against independently recovered print dates)
    max_stale_days: 5          # abstain when last_trade_time is older than this (holiday gaps)
```

### Tests

- **Fixture-based unit tests** over a trimmed captured chain: OSI parsing; expired-contract
  filtering (§4.3); two-sided-quote requirement (§4.4); delta-tolerance rejection using the
  real `RES` shape; spread rejection; per-item independent abstention.
- **Earnings-expiry selection**, which is where the silent-wrong failure lives: an expiry
  inside the uncertainty window is rejected; one past the gap ceiling is rejected; a date
  revised later by 8 days must not leave a selected expiry before the print.
- **Realized-move computation** against a fixture with a known announcement date, including
  the pre-open vs after-close session ambiguity (§6.2).
- `enabled: false` → `context_line` returns `None` → **byte-identical prompt**.
- `test_options_line_not_in_haystack`, mirroring the shipped insider-line guard.
- Never-raises: a 429, a timeout and malformed JSON each yield `None`, not an exception.
- `as_of` set to a past date → `None` (§5 look-ahead guard).
- One `-m live` test (skipped by default), matching `test_proxy_live.py`.

---

## 10. Deferred, with reasons

- **IV rank / IV percentile** — the single most-requested options statistic, and it needs a
  time series this feed does not serve. It becomes available only by accumulating `iv30`
  daily. Worth doing *later* and cheaply (one number per ticker per day), but it is a
  separate feature with a storage decision, and it gets better the longer it runs.
- **A market-cap-conditional skew reference** — needs a second measured distribution; only
  18 clean small/mid observations exist (§6.3).
- **Term-structure slope (front ATM IV minus ~90-day) — specced, then CUT.** It was
  justified as a cross-check on the implied move, and that justification does not survive
  scrutiny: front-month IV is mechanically elevated by short time-to-expiry regardless of any
  event, and when earnings sit 60-90 days out the "90-day" leg *contains* the print, so the
  slope partly restates §6.2 rather than checking it. Reviving it needs a measured
  cross-sectional reference like the other two items have, plus a reason it is not
  redundant. Do not re-add it as "cheap, it's already in the payload" — that was the
  original error.
- **Open interest and put/call ratios** — deliberately cut. Positioning is the least
  interpretable thing in the payload and would need its own base-rate work to mean anything.
  YAGNI.
- **Credit / TRACE** — the natural next "other market's opinion of the same issuer," and the
  probed slug 404s (§3). Unconfirmed; do not assume free.

## 11. What would falsify this

Stated up front so the post-ship read is not a story built from the data:

1. **The line abstains on most real `/deep` targets.** Guards are calibrated on universe
   files, not on what the user actually deep-dives. If the abstention rate on real briefs is
   high, the guards are wrong or the feature is aimed at the wrong names.
   The earnings-expiry window (§6.2) is the tightest constraint and the likeliest to bind.
2. **The model treats an implied move as a forecast** despite the caveat — that is the
   `PROXY_SYSTEM_ADDENDUM` question reopening (§8).
3. **Either reference distribution drifts enough to mislead** before anyone re-measures it.
   Both the skew reference (§6.3) and the IV/realized reference (§6.1) are single-day
   cross-sections from 2026-08-21, and the IV/realized one is the more fragile of the two:
   its median of 0.93 partly reflects a cross-section sitting *between* earnings seasons.
   Re-measure at a different point in the cycle before trusting it as a constant.
4. **The per-IP budget tightens** and `/deep` starts abstaining on 429 (§4.1).

Verification after shipping should be a small live pass over real `/deep` targets recording
which items rendered and which abstained — the same shape as the `controls.py` in/out-of-sample
pass, and cheap because it costs one request per name.
