# `net_debt_to_ebitda` axis — re-measured on de-polluted data, verdict STANDS

**Date:** 2026-08-18/19 · **Change:** none (`net_debt_to_ebitda` is a **standalone/backtest-only
diagnostic axis** — `TODO.md` §3, not a scored production leg). This doc closes the TODO.md §3
bullet asking for a clean re-measurement.

## Why re-measured

Every prior `net_debt_to_ebitda` IC run — the 2026-07-05 per-universe residualized-IC results
and the 2026-07-11 combined-universe run that produced the "leverage tilt NOT earned" verdict
(both in gitignored `docs/superpowers/specs/`, still present on this disk but not evidence of
record) — predate PR #144 / commit `67bb7e8` (merged 2026-07-20, "negative-EBITDA leverage
abstention"). Before that fix, `ratio_latest()` computed `net_debt / EBITDA` with no sign guard
on the denominator: a leveraged name with **negative EBITDA** produced a **negative** ratio,
which the inverted leverage band reads as *low leverage / net cash* — i.e. distressed,
debt-laden names were scored at the top of the band. The verdict may still hold, but it was
measured on polluted data.

## Confirmed in code before running: the abstention IS in the path this backtest exercises

`providers/_xbrl_facts.py:400` (the extractor `backtest/xbrl.py`'s `XbrlSignalSource` calls —
per `CLAUDE.md`, this file, not `_edgar_facts.py`, is the one shared with the XBRL backtest):

```python
m.net_debt_to_ebitda = ratio_latest(net_debt_series, ebitda_series, positive_den=True)
```

`ratio_latest()` docstring (`_xbrl_facts.py:175-187`):

> With `positive_den=True`, also None when the denominator is negative — for ratios whose sign
> convention is meaningless over a negative base (net_debt/EBITDA: a negative-EBITDA denominator
> would make a leveraged name read as net cash).

This is exactly the fix TODO.md refers to, and it sits directly in the backtest's extraction
path. The re-run below measures clean data, not the old pollution.

## Commands run (verbatim)

```bash
set -a && . ./.env && set +a && \
uv run shortlist-backtest --source xbrl --universe largecap --horizons 1,3,6,12 --json
```
Succeeded first try — `universe_size` 80 (all 80 lines in `universe_largecap.txt` resolved,
none stale).

```bash
set -a && . ./.env && set +a && \
uv run shortlist-backtest --source xbrl --universe smallmid --horizons 1,3,6,12 --json
```
**Refused** (exit 2):

```
universe contains 5 symbol(s) absent from SEC's current ticker map: THR, SCVL, TPH, FDP, VTLE
Each is renamed or delisted, so it yields no data and silently shrinks the cross-section — which is why this aborts at measurement time rather than producing a quietly narrower result.
Fix the universe file (a rename keeps the same CIK), or re-run with --allow-stale-universe to proceed anyway.
```

5 of 153 tickers (3.3%) is in line with the ~3–4%/yr ticker rot documented in TODO.md's "Pin
universe membership by CIK, not ticker" entry — not a new pathology, and the universe file's own
2026-08-15 maintenance comment shows this is a recurring, known drift. Re-ran with the flag:

```bash
set -a && . ./.env && set +a && \
uv run shortlist-backtest --source xbrl --universe smallmid --horizons 1,3,6,12 --json --allow-stale-universe
```
Succeeded — `universe_size` 149. Reconciliation: of the 5 flagged-stale symbols, 4 (THR, SCVL,
TPH, VTLE) have no Yahoo price history at all and drop out of `universe` entirely; FDP has price
history but no SEC companyfacts match, so it stays in `universe` (149 = 153 − 4) but contributes
no `net_debt_to_ebitda` value (folds into the abstention count below, not double-counted).

Raw JSON tee'd to (recoverable):
`/tmp/claude-1000/-home-chris-shortlist/0ea049f9-aef5-4b84-a330-0e8a7292b5c4/scratchpad/largecap-xbrl-1.json`
`/tmp/claude-1000/-home-chris-shortlist/0ea049f9-aef5-4b84-a330-0e8a7292b5c4/scratchpad/smallmid-xbrl-1.json`

## Results (`net_debt_to_ebitda`, post-fix, `price_asof` 2026-08-18)

### largecap (universe_size = 80)

| h | XS mean | XS t | XS n | XS hit | TS mean | TS t | TS n | TS hit | breadth | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1  | +0.0360 | 1.310 | 89 | .528 | +0.0087 | 0.645  | 35 | .600 | 27.1 | EXPLORATORY |
| 3  | +0.0647 | 1.413 | 29 | .586 | +0.0240 | 0.927  | 35 | .571 | 26.9 | EXPLORATORY |
| 6  | +0.0646 | 0.815 | 14 | .643 | −0.0076 | −0.211 | 34 | .412 | 27.0 | EXPLORATORY |
| 12 | +0.1076 | 0.983 |  7 | .571 | +0.0229 | 0.465  | 33 | .545 | 26.9 | EXPLORATORY |

Every horizon is **EXPLORATORY** — mean breadth ≈ 27 names/date, under the engine's own ~30
names/date trust floor. This was ALSO true before the fix (pre-fix largecap breadth was
27.6–27.8). Read largecap directionally only, not as a significance test.

### smallmid (universe_size = 149)

| h | XS mean | XS t | XS n | XS hit | TS mean | TS t | TS n | TS hit | breadth | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1  | +0.0074 | 0.648 | 149 | .537 | −0.0036 | −0.264 | 49 | .490 | 40.2 | — |
| 3  | +0.0120 | 0.585 |  49 | .510 | +0.0067 | 0.280  | 49 | .490 | 40.0 | — |
| 6  | +0.0261 | 0.941 |  24 | .583 | +0.0115 | 0.294  | 48 | .521 | 40.2 | — |
| 12 | +0.0197 | 0.521 |  12 | .750 | +0.0020 | 0.039  | 48 | .458 | 39.7 | EXPLORATORY |

h=1/3/6 clear both trust floors (breadth ≥30, periods ≥24) — these are the trustworthy read.
h=12 is EXPLORATORY (12 periods < 24 floor).

## Coverage / abstention — the headline this re-run exists to produce

`net_debt_to_ebitda` has the **lowest breadth of any axis measured**, in both universes, before
and after the fix:

| universe | signal | breadth (h=3) | % of universe |
|---|---|---|---|
| largecap (80) | net_debt_to_ebitda | 26.9 | 33.6% |
| largecap (80) | next-lowest (ebit_ev_yield) | 33.6 | 42.0% |
| largecap (80) | median other axis | ~66 | ~82% |
| smallmid (149) | net_debt_to_ebitda | 40.0 | 26.8% |
| smallmid (149) | next-lowest (ebit_ev_yield) | 39.6 | 26.6% |
| smallmid (149) | median other axis | ~90 | ~60% |

`ebit_ev_yield` shares the same `net_debt_series` derivation, so it abstains for correlated
reasons; every other axis clears 55%+ coverage. Roughly **two-thirds of large-caps and
three-quarters of small/mid-caps abstain** on this axis on any given date — leverage needs a
common fiscal end across debt, cash, operating income and D&A, plus (now) a positive EBITDA at
that end, which is a demanding intersection.

**What the fix itself changed** (pre-fix numbers from the 2026-07-05
`docs/superpowers/specs/2026-07-05-leverage-residualized-ic-results.md` run, same-shape
universes, `--source xbrl` raw axis, not evidence of record but still on disk for this
comparison):

| universe | breadth pre-fix (2026-07-05) | breadth post-fix (this run) | delta |
|---|---|---|---|
| largecap | 27.6–27.8 (79 names, 1 unresolvable) | 26.9–27.1 (80 names) | ≈ −0.7, **−2.5%** relative |
| smallmid | 44.2–45.2 (152 names, 2 unresolvable) | 39.7–40.2 (149 names, 4 unresolvable) | ≈ −4.6, **−10.3%** relative |

The abstention fix's coverage effect is **small on large-caps** (established, profitable
issuers rarely report negative EBITDA) and **material on small/mid-caps** (~10% of names that
previously produced a — polluted — value now correctly abstain). This confirms the mechanism
TODO.md described: negative-EBITDA names, concentrated in the smaller/more-cyclical universe,
used to leak a bogus "net cash" reading into the axis and now don't.

## IC comparison, pre-fix vs post-fix (XS t-stat)

| universe | h | pre-fix t (07-05) | post-fix t (this run) |
|---|---|---|---|
| largecap | 1  | 1.279 | 1.310 |
| largecap | 3  | 1.548 | 1.413 |
| largecap | 6  | 0.892 | 0.815 |
| largecap | 12 | 1.124 | 0.983 |
| smallmid | 1  | 0.903 | 0.648 |
| smallmid | 3  | 0.508 | 0.585 |
| smallmid | 6  | 0.927 | 0.941 |
| smallmid | 12 | 0.633 | 0.521 |

No horizon, on either universe, before or after the fix, reaches the ≥2 significance bar the
repo requires before a signal is taken seriously (`CLAUDE.md`: "a single-universe t≈2 is usually
noise"; here nothing even reaches t≈2 on a single universe, let alone both). The changes from
the fix are small and directionless (some t-stats up, some down) — the abstention fix altered
*which names* contribute, not the axis's fundamental lack of edge.

## Verdict: the 2026-07-11 "leverage tilt NOT earned" conclusion STANDS

Measured on de-polluted data, on both committed universes, at all four horizons:
`net_debt_to_ebitda` shows no cross-sectional rank-IC that clears significance. The strongest
trust-passing read (smallmid, which clears both the breadth and period floors at h=1/3/6) tops
out at t=0.941 (h=6) — nowhere near the ≥2 bar. Largecap never clears the breadth floor at all,
before or after the fix, so it can only be read directionally. Per the pre-committed rule (must
reproduce across BOTH universes, and a single-universe t≈2 does not count), this axis has not
earned a slot and the prior kill/no-wire decision is correct — it just now rests on clean
evidence instead of evidence that could have been inflated by negative-EBITDA names reading as
net-cash.

No config or scoring code changed. This is a measurement-only note.
