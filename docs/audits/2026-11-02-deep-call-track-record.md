# 2026-11-02 — how did past `/deep` screening calls turn out?

The owner asked whether the `/deep` screening call (STRONG_BUY | BUY | HOLD | AVOID |
STRONG_AVOID, plus a conviction) can be trusted. This note measures the persisted production
calls against SPY. At this sample size it **cannot show an edge**. It can show a gross failure,
for example bullish calls trailing bearish calls by a wide margin.

## Pre-registration (written and committed before any post-call price was fetched)

What has been seen: the record metadata only (ticker, `as_of`, model, stance, conviction,
clamp fields). No price after any `as_of` has been read. A date-only count, before any price,
gives about 48 records old enough, on 42 tickers, all from `claude-sonnet-4-6` and
`claude-sonnet-5`.

### Population

- **Source.** `/opt/shortlist/research/*/*.json` as of 2026-11-02 (76 records, 57 tickers),
  copied with only `ticker`, `as_of`, `model`, `filing_date` and `screening_call` into
  `raw-2026-11-02-calls/records/`.
- **Exclusions.** No `screening_call`; no `as_of_price`; no Yahoo price series for the ticker
  or for SPY; fewer than 21 sessions between the entry session and the cut-off (below). Every
  excluded record is listed with its reason.
- **Cut-off.** The latest close is the **2026-10-30** close, fixed in the script, so a re-run
  gives the same numbers. Any bar after it is ignored.
- **Dedupe: first call per ticker per stance change.** Sort a ticker's eligible records by
  `as_of`; keep a record only when its stance differs from the last kept record's stance.
  Repeat briefs with the same stance (PGR twice in one day, HDSN on consecutive days, AXON
  three times) are one opinion, not independent evidence; a changed stance is a new call. Each
  kept record is an **episode**, evaluated over its own fixed windows; a later call on the
  same ticker does not truncate it.
- **Dependence.** A ticker can carry several episodes, so every interval below resamples
  **tickers**, not episodes.

### Entry price

- **Entry** = the adjusted close of the **entry session**: the last session whose close
  (20:00 UTC; every `as_of` here is in daylight time) is at or before `as_of`. A brief written
  intraday or at a weekend enters at the prior close. The brief's `as_of_price` is **not** the
  entry: it can be an intraday or cached quote, and it is not adjusted for later dividends.
- **Check.** `as_of_price` agrees when it is within 2% of the entry session's unadjusted close,
  or inside the `as_of`-date session's low–high range widened by 1% (an intraday quote). A
  record that fails is **flagged**, not dropped; the note lists it with the nearest session
  whose close is within 0.5% of `as_of_price` (a stale price) or the ratio (a split). A
  sensitivity run drops flagged records; it is reported and decides nothing.

### Horizons

- **21 sessions (primary)** and **63 sessions** after the entry session, plus **to the latest
  close**. A horizon that has not elapsed by the cut-off leaves that episode out of that
  horizon only; nothing is extrapolated. "To latest close" uses the population of the 21-session
  horizon, so its windows run from 21 to roughly 100 sessions.
- A series that ends before the cut-off (a delisting) is flagged; it enters a horizon only if
  the horizon elapsed before the series ended.

### Return and benchmark

- Stock return = adjusted close at exit / adjusted close at entry − 1.
- Benchmark = SPY, same formula, same entry and exit dates (adjusted close, so dividends are
  roughly in). **Excess = stock return − SPY return.**

### Groups

- **Primary**, by the stance the user saw (after guards): **bullish** = STRONG_BUY, BUY;
  **HOLD**; **bearish** = AVOID, STRONG_AVOID.
- **Secondary**, by the model's pre-clamp stance: `model_stance` when set; else `stance` when
  `stance_clamped` is false; else unknown and left out (older records clamped before
  `model_stance` was persisted). The same dedupe rule is applied on this key.

### Statistics

Per group and horizon: n episodes, n tickers, mean and median excess, and hit rate (excess > 0
for bullish, < 0 for bearish; for HOLD the share > 0 is reported, not scored).

**The deciding number: the bullish-minus-bearish spread of mean excess at 21 sessions**, with a
95% percentile interval from a ticker-clustered bootstrap: resample the tickers that carry a
bullish or bearish episode, with replacement, taking all of each ticker's episodes; 10,000
draws, seed 20261102; a draw with an empty group is redrawn and the count of redraws is
reported. The same spread and interval are reported at 63 sessions and to the latest close,
and for the secondary grouping; none of those decides anything.

Descriptive only, no test: the stance distribution over all 74 calls; how often a guard clamped
a stance or capped conviction; mean excess by conviction within bullish and bearish.

### Decision rules (applied mechanically to the 21-session spread)

| result | reading | register |
|---|---|---|
| fewer than 5 episodes or 4 tickers in the bullish or the bearish group | **not measurable** | index row only; question open |
| spread < 0 and the interval's upper bound < 0 | **gross failure**: bullish calls trailed bearish calls beyond noise. Do not trust the call's direction | verdict |
| interval spans 0 | **no evidence either way** (the expected outcome at this n) | index row only; question open |
| spread > 0 and the interval's lower bound > 0 | **no sign of failure** | verdict |

**A positive spread with the interval above zero is still NOT evidence of an edge.** The calls
overlap in time: all of them sit in June–October 2026, one market regime, so a single sector
or factor move can carry a whole group. The effective n is far below the record count, and a
ticker bootstrap does not repair the time overlap. The tickers were also chosen by the owner,
not sampled. "No sign of failure" means only that the gross-failure check did not fire.

Two scope limits fixed now: every call old enough comes from `claude-sonnet-4-6` or
`claude-sonnet-5` with older prompts and guards; no `claude-sonnet-5-5` call is 21 sessions old.
The result says nothing direct about the current model. Re-run when at least 20 more calls,
including current-model calls, have passed 63 sessions.

Script: `scripts/probe_call_track_record.py`. Price cache: `raw-2026-11-02-calls/prices/`.

## Results

Run 2026-11-02 against the 2026-10-30 close. `scripts/probe_call_track_record.py --offline`
reproduces every number from `raw-2026-11-02-calls/` (`results.json`, `episodes.csv`).

**Population.** 76 records → 74 with a call (GOOG and NVDA, 2026-06-07, have none) → 48 at
least 21 sessions old, on 42 tickers: 25 `claude-sonnet-4-6`, 23 `claude-sonnet-5`. The 26
younger records are exactly the 26 `claude-sonnet-5-5` calls. Dedupe leaves **44 episodes on
42 tickers**: 7 bullish (all BUY), 25 HOLD, 12 bearish (11 AVOID, 1 STRONG_AVOID). Every
eligible ticker had a Yahoo series running to the cut-off: no missing or delisted names.

**Price check.** No record was flagged. The largest gaps between `as_of_price` and the entry
close come from briefs written intraday, after a move, and sit inside that session's range:
AMZN 2026-07-31 (271.43 against a 235.50 prior close, an earnings gap), AXON 2026-09-01
(−8.5%) and IT 2026-07-01 (+4.8%). So `as_of_price` is a live quote, not a stale one; see the
post-hoc entry check below for what that does to the entry.

### Excess return over SPY by group (primary: the stance shown)

| group | horizon | n | mean | median | hit rate |
|---|---|---|---|---|---|
| bullish | 21 sessions | 7 | +3.5% | +5.9% | 71% |
| bullish | 63 sessions | 6 | +2.0% | −2.3% | 50% |
| bullish | to 2026-10-30 | 7 | −1.1% | −2.0% | 14% |
| HOLD | 21 sessions | 25 | −0.7% | +0.5% | 52% (> 0) |
| HOLD | 63 sessions | 11 | +12.5% | +8.7% | 73% (> 0) |
| HOLD | to 2026-10-30 | 25 | +8.0% | +4.3% | 64% (> 0) |
| bearish | 21 sessions | 12 | +0.7% | +1.6% | 50% |
| bearish | 63 sessions | 7 | −12.2% | −3.5% | 57% |
| bearish | to 2026-10-30 | 12 | −13.7% | −14.8% | 75% |

Each episode is one ticker in every cell (n tickers = n).

### Bullish minus bearish, ticker-clustered bootstrap

| grouping | horizon | bullish / bearish | spread (mean) | 95% interval | median spread |
|---|---|---|---|---|---|
| **stance shown** | **21 sessions** | **7 / 12** | **+2.8%** | **[−9.7%, +14.2%]** | +4.3% |
| stance shown | 63 sessions | 6 / 7 | +14.2% | [−1.2%, +31.5%] | +1.2% |
| stance shown | to 2026-10-30 | 7 / 12 | +12.7% | [−4.1%, +30.1%] | +12.8% |
| pre-clamp | 21 sessions | 7 / 9 | +0.3% | [−14.7%, +13.2%] | +1.1% |
| pre-clamp | 63 sessions | 6 / 5 | +20.1% | [+2.1%, +38.1%] | +23.4% |
| pre-clamp | to 2026-10-30 | 7 / 9 | +11.3% | [−9.8%, +33.6%] | +5.6% |

Bootstrap redraws for an empty group: 1 at 21 sessions, 7 and 10 at 63 sessions. The
pre-clamp grouping leaves out ORCL and SNDK (clamped before `model_stance` was persisted) and
moves HDSN to HOLD (its 2026-08-21 record has `model_stance` HOLD). Its 63-session interval
excludes zero on 6 against 5 episodes; it is a secondary cut and decides nothing.

Sensitivity, flagged records dropped: nothing was flagged, so it equals the primary.

### Bullish and bearish episodes

| ticker | as_of | model | stance | conviction | 21 sessions | 63 sessions | to 10-30 |
|---|---|---|---|---|---|---|---|
| MELI | 2026-07-05 | sonnet-4-6 | BUY | MEDIUM | +5.9% | −7.3% | −9.6% |
| ISRG | 2026-07-05 | sonnet-4-6 | BUY | MEDIUM | −13.6% | −8.6% | −0.3% |
| JPM | 2026-07-14 | sonnet-4-6 | BUY | MEDIUM | +3.8% | −5.8% | −2.0% |
| NFLX | 2026-07-18 | sonnet-4-6 | BUY | MEDIUM | +6.3% | +1.2% | −11.9% |
| MSFT | 2026-07-21 | sonnet-4-6 | BUY | MEDIUM | +16.3% | +28.7% | +27.9% |
| AMZN | 2026-07-31 | sonnet-4-6 | BUY | MEDIUM | +9.4% | +3.7% | −0.1% |
| UBER | 2026-08-16 | sonnet-5 | BUY | MEDIUM | −3.5% | n/a | −11.7% |
| QBTS | 2026-06-12 | sonnet-4-6 | AVOID | MEDIUM | −22.6% | −33.3% | −58.2% |
| EWSB | 2026-07-02 | sonnet-4-6 | STRONG_AVOID | MEDIUM | +8.8% | +4.1% | +6.2% |
| SNDK | 2026-07-08 | sonnet-4-6 | AVOID | MEDIUM | −19.5% | +1.5% | −30.2% |
| ORCL | 2026-07-13 | sonnet-4-6 | AVOID | MEDIUM | +7.7% | +3.7% | −1.4% |
| ASTS | 2026-07-16 | sonnet-4-6 | AVOID | MEDIUM | +4.8% | −32.4% | −38.6% |
| INTC | 2026-07-16 | sonnet-4-6 | AVOID | HIGH | −1.5% | −3.5% | −36.5% |
| AXON | 2026-08-04 | sonnet-4-6 | AVOID | MEDIUM | −10.5% | −25.7% | −25.7% |
| HDSN | 2026-08-20 | sonnet-5 | AVOID | LOW | −8.5% | n/a | −22.0% |
| HP | 2026-08-23 | sonnet-5 | AVOID | MEDIUM | −9.9% | n/a | −0.2% |
| SDGR | 2026-08-28 | sonnet-5 | AVOID | MEDIUM | +45.0% | n/a | +34.7% |
| TSLA | 2026-09-06 | sonnet-5 | AVOID | MEDIUM | +6.1% | n/a | +14.6% |
| VST | 2026-09-09 | sonnet-5 | AVOID | MEDIUM | +8.3% | n/a | −7.6% |

### Descriptive only

- **Stance, all 74 calls:** HOLD 45, AVOID 15, BUY 13, STRONG_AVOID 1, STRONG_BUY 0.
  Conviction: MEDIUM 60, LOW 13, HIGH 1 (INTC, AVOID).
- **Guards:** 8 stances clamped: `negative_fcf` 5 (to AVOID), `below_min_mktcap` 2 and
  `heavy_insider_selling` 1 (to HOLD). Only 3 of the 8 carry `model_stance`; the 5 older ones
  predate the field. `conviction_capped` is false on all 74, and the 25 `claude-sonnet-4-6`
  records carry no `confidence`, so no conviction can be attributed to a cap.
- **Conviction against outcome:** almost every directional call is MEDIUM, so there is
  nothing to compare. The only HIGH (INTC AVOID) and the only bearish LOW (HDSN) both trailed
  SPY to 2026-10-30 (−36.5%, −22.0%).

## Deviations from the pre-registration

1. **Intraday range rule, made precise.** The `as_of`-date session range is used only for a
   brief written during regular hours (13:30–20:00 UTC). The text said "an intraday quote" but
   did not fix the hours.
2. **Post-hoc entry check, not pre-registered, decides nothing.** The pre-registered entry is
   the prior close, so a brief written intraday after a move is credited or debited with that
   move (AMZN's +15% earnings gap counts for its BUY). Entering instead at the first close after
   `as_of`: 21 sessions **−0.6% [−16.1%, +14.0%]**; to 2026-10-30 **+10.2% [−6.8%, +28.2%]**.
   Neither interval excludes zero, so the reading below does not change.
3. **Fetch.** Prices come from Yahoo's chart endpoint with the repo's own Yahoo User-Agent,
   over IPv4: on this host an IPv6 connect stalls about 40 s first. No effect on any number.

## Verdict — no evidence either way

The rule is applied to the 21-session spread of the stance shown: **+2.8%, 95% interval
[−9.7%, +14.2%]**, on 7 bullish and 12 bearish episodes (at least 5 episodes and 4 tickers per
group, so it is measurable). **The interval spans zero: no evidence either way.** No
gross failure: bullish calls did not trail bearish calls beyond noise. Under the pre-registered
table this is not a verdict; the question stays open. Re-run when at least 20 more calls,
including `claude-sonnet-5-5` calls, have passed 63 sessions.

**For the owner.** This check cannot say the calls are good; it was designed only to catch
calls that are badly wrong, and it did not catch that. Over one month, BUY and AVOID calls did
about the same against SPY. Over the longer windows the AVOID names fell well behind SPY
(median −14.8% to 2026-10-30), but the BUY names did not beat it either (1 of 7 ahead), and
HOLD names did better than both. All of this is 19 directional calls from June to September
2026, in one market, from two older models; none of the calls the current model made is old
enough to count. Read the call as one input to a deep dive, not as a forecast. A long run of
consistent results across different markets is what would make it more than that, and this
sample cannot provide it.
