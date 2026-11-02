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
