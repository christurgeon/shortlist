# Moat durability — verdict (2026-10-04 pre-registration)

**Pre-registration:** `2026-10-04-moat-durability-prereg.md`, committed before any predictor was
measured and amended four times, each time before any SEC bulk data was read. Probe:
`scripts/probe_durability.py`. Raw output: `raw-2026-10-04-durability/`. Every table below is
printed from those files by `scripts/print_durability_tables.py`.

**Written in two parts.** The gates and the discovery section were written and committed before
the holdout was computed. The script checks that in git and has no override.

The question is about fundamentals, not returns: among firms with a top-quintile return on
invested capital (ROIC), is it predictable from free SEC data which ones still have it three
years later? Nothing here measures a return.

## Gates (run 2026-10-07)

All four passed.

| gate | result |
|---|---|
| completeness | 9 of 9 dead filers reach their required last bucket |
| reproduction | the comparison count is 0.3% to 1.2% above its target in every year (band ±15%) |
| instrument | slope of `held` on the ROIC rank, within year, on discovery: **+0.550** |
| tag artefact | `gap` rate 2.1% to 4.3% by outcome year; no year is 5 points above both neighbours |

Data: `companyfacts.zip` and `submissions.zip`, downloaded 2026-10-07. Compacted file SHA-256
`2d617f70…aa5639`. 12,539 filers on forms 10-K and 10-K/A, 919 more on foreign annual forms only
(read by the reproduction gate and by nothing else), SIC code for 12,523. No archive member was
unreadable. 127 rows of 45 filers were filed before their own period ended and are never read.

| year | target (frames) | comparison count | ratio | study universe | same, no sector mask | ROIC and no revenue value (assets ≥ $500M) | zero-debt share |
|---|---|---|---|---|---|---|---|
| 2011 | 2114 | 2135 | 1.010 | 1662 | 1772 | 788 (282) | 0.381 |
| 2012 | 2102 | 2110 | 1.004 | 1714 | 1833 | 701 (257) | 0.342 |
| 2013 | 2052 | 2063 | 1.005 | 1672 | 1790 | 683 (258) | 0.342 |
| 2014 | 2066 | 2090 | 1.012 | 1690 | 1819 | 712 (265) | 0.334 |
| 2015 | 1995 | 2017 | 1.011 | 1640 | 1772 | 690 (272) | 0.319 |
| 2016 | 2161 | 2179 | 1.008 | 1543 | 1668 | 664 (265) | 0.307 |
| 2017 | 2254 | 2268 | 1.006 | 1543 | 1678 | 629 (271) | 0.306 |
| 2018 | 2214 | 2227 | 1.006 | 1840 | 1967 | 225 (34) | 0.296 |
| 2019 | 2190 | 2200 | 1.005 | 1811 | 1927 | 208 (17) | 0.299 |
| 2020 | 2207 | 2227 | 1.009 | 1792 | 1910 | 247 (27) | 0.305 |
| 2021 | 2359 | 2374 | 1.006 | 1967 | 2091 | 325 (32) | 0.322 |
| 2022 | 2309 | 2324 | 1.006 | 1931 | 2043 | 325 (39) | 0.301 |
| 2023 | 2245 | 2265 | 1.009 | 1881 | 1992 | 281 (36) | 0.307 |
| 2024 | 2179 | 2186 | 1.003 | 1812 | 1933 | 271 (39) | 0.308 |

What the three counts say, as the pre-registration asked them to be read:

- **The data path reproduces the targets.** The comparison count (latest values, no mask, the
  foreign annual forms included) is within 1.2% of the frames count in every year.
- **The study's universe is 17% to 32% below the comparison count.** That is the size of the
  differences amendment 3 named and could not measure: the sector mask (110 to 135 firms a
  year), the 120-day rule, first-reported values and the form filter. The gate as first
  registered compared this universe with the targets and would have failed in every year.
- **The revenue-tag hole closes at 2018 on first-reported values.** 257 to 282 firms a year
  with assets of $500M or more have a ROIC and no revenue value in 2011–2017, and 17 to 39 from
  2018. So the `t+3` universe is the wider one for start years 2015, 2016 and 2017, as the
  limitation in the pre-registration expected. The universe steps from 1,543 to 1,840 between
  2017 and 2018.
- **Zero debt is common.** 30% to 38% of the universe has debt of zero, and 39% to 51% of each
  discovery cohort. That is an upper bound on untagged debt; it also holds firms with no debt.

Discovery cohort: 2,290 firm-years (308 to 342 a year). States at `t+3`: observed 81.4%,
`exit` 12.9%, `gap` 2.9%, `low_ic` 2.8%.

**Read before any β:** 3.8% of discovery cohort rows are alone in their SIC-2 × start-year cell
and 17.3% in their SIC-3 cell. On the synthetic tables of amendment 2, rule 5 cost no power at
15% alone. It was not weak evidence here for that reason.

## Discovery (start years 2011-2017) — written BEFORE the holdout was computed

Hold rate (outcome A) 58.6%; compounded (outcome B) 32.9%, among firms in the primary sample.
`track` and `stability` have no 2011 rows (amendment 3). No bootstrap had a replication that
could not be fitted. No row lacked a SIC code.

| test | n | β | SE | bar | 95% interval | β SIC-3 | raw spread (the wrong metric) | bound: exits held | bound: exits not held | rank β | magnitude / bounds / sign / sub-industry |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `track/held` | 1552 | +0.109 | 0.069 | 0.137 | -0.028 to +0.242 | +0.038 | +0.243 | +0.079 | +0.116 | +0.083 | n/Y/Y/n |
| `stability/held` | 1552 | +0.150 | 0.059 | 0.118 | +0.029 to +0.264 | +0.086 | +0.219 | +0.112 | +0.146 | +0.116 | Y/Y/Y/Y |
| `investment/held` | 1883 | +0.149 | 0.047 | 0.100 | +0.060 to +0.245 | +0.150 | +0.189 | +0.166 | +0.133 | +0.098 | Y/Y/Y/Y |
| `share_stability/held` | 1438 | +0.068 | 0.062 | 0.124 | -0.053 to +0.191 | +0.104 | +0.112 | +0.107 | -0.005 | +0.008 | n/n/Y/Y |
| `gross_margin/held` | 1507 | +0.121 | 0.066 | 0.131 | -0.007 to +0.242 | +0.123 | +0.058 | +0.156 | +0.140 | +0.044 | n/Y/Y/Y |
| `incremental_roic/held` | 983 | -0.056 | 0.079 | 0.159 | -0.205 to +0.106 | +0.024 | +0.098 | -0.033 | -0.047 | -0.016 | n/n/n/n |
| `track/compounded` | 1536 | -0.004 | 0.069 | 0.138 | -0.144 to +0.126 | -0.026 | +0.104 | -0.021 | +0.018 | +0.083 | n/n/Y/n |
| `stability/compounded` | 1536 | +0.047 | 0.070 | 0.140 | -0.091 to +0.185 | -0.010 | +0.109 | +0.019 | +0.051 | +0.116 | n/n/Y/n |
| `share_stability/compounded` | 1422 | -0.094 | 0.066 | 0.132 | -0.230 to +0.029 | -0.030 | -0.046 | -0.013 | -0.125 | +0.008 | n/n/Y/n |
| `gross_margin/compounded` | 1493 | +0.167 | 0.069 | 0.138 | +0.038 to +0.312 | +0.113 | +0.071 | +0.171 | +0.155 | +0.044 | Y/Y/Y/Y |
| `incremental_roic/compounded` | 970 | +0.062 | 0.081 | 0.161 | -0.084 to +0.222 | +0.138 | +0.095 | +0.075 | +0.064 | -0.016 | n/Y/n/Y |

β is the change in the probability of the outcome from the worst to the best rank of the
predictor, among firms of the same SIC-2 sector and start year, with ROIC level (rank and its
square) and revenue rank held fixed. The bar is max(10 pp, 2 × SE). Every predictor is oriented
so that its registered sign is positive: for `investment` the favourable end is LOW growth of
invested capital.

**Discovery survivors** (all four discovery rules true), with the SIC-3 β beside each:

| test | β (SE) | bar | margin over the bar | β under SIC-3 cells (half of β needed) |
|---|---|---|---|---|
| `stability/held` | +0.150 (0.059) | 0.118 | 3.2 points | +0.086 (0.075) |
| `investment/held` | +0.149 (0.047) | 0.100 | 4.9 points | +0.150 (0.074) |
| `gross_margin/compounded` | +0.167 (0.069) | 0.138 | 2.9 points | +0.113 (0.084) |

None is within 2 points of its bar. None is far over it. Three of eleven survive discovery;
under the null the pre-registration puts the false-pass rate of the discovery arm alone at up to
3.5% a test. The holdout decides. A survivor passes only if its holdout β clears max(6 pp,
1.64 × SE) and half of it survives the SIC-3 cells there too.

What each survivor would mean if it passes, fixed before the holdout:

- `stability`: a firm whose ROIC rank moved less over the last four years holds more often. By
  the pre-registered reading this is a longer measure of the same level, a track-record line,
  not a separate trait. `track` itself did not survive: +0.109 against a bar of 0.137, and
  +0.038 under SIC-3 cells.
- `investment`: a firm that grew its invested capital LESS in the last year holds more often.
  Its SIC-3 β equals its β, so it is not a sub-industry label.
- `gross_margin` on outcome B: a higher gross margin goes with holding AND growing revenue
  faster than the cohort median. On outcome A alone it did not survive (+0.121 against 0.131).

**What the raw spread would have said.** Where it differs from β by more than 5 points:

| test | raw spread | β | reading |
|---|---|---|---|
| `track/held` | +0.243 | +0.109 | over half of the raw spread is level and sector |
| `stability/held` | +0.219 | +0.150 | a third of it is |
| `incremental_roic/held` | +0.098 | -0.056 | the raw sign does not survive the controls |
| `track/compounded` | +0.104 | -0.004 | all of it is level and sector |
| `stability/compounded` | +0.109 | +0.047 | over half of it is |
| `gross_margin/held` | +0.058 | +0.121 | the controls RAISE it: the raw spread hid it |
| `gross_margin/compounded` | +0.071 | +0.167 | the same |

On outcome A a raw spread ranks `track` first of six and `gross_margin` last. The controlled β
puts `gross_margin` above `track`.

**Opposite-signed results** (findings, not passes): none. Three β are negative
(`incremental_roic/held` −0.056, `share_stability/compounded` −0.094, `track/compounded`
−0.004) and every 95% interval of the three includes zero.

**Reported, not decision-bearing, for the three survivors.**

| test | β by start year, 2011 → 2017 | exit rate by tercile (worst / mid / best) | `gap` rate | `low_ic` rate | β without energy and mining |
|---|---|---|---|---|---|
| `stability/held` | — · +0.007 · +0.199 · +0.320 · −0.023 · +0.128 · +0.219 | 14.4% / 12.8% / 10.8% | 3.6% / 2.0% / 2.6% | 2.9% / 2.6% / 2.3% | +0.147 |
| `investment/held` | +0.236 · +0.219 · +0.147 · +0.131 · +0.062 · +0.144 · +0.122 | 12.7% / 12.6% / 12.7% | 2.6% / 2.6% / 3.8% | 1.3% / 1.2% / 5.2% | +0.145 |
| `gross_margin/compounded` | +0.060 · +0.240 · +0.168 · +0.112 · +0.108 · +0.194 · +0.323 | 12.1% / 13.4% / 12.8% | 4.0% / 1.7% / 3.4% | 1.8% / 3.0% / 2.9% | +0.170 |

- `investment/held` and `gross_margin/compounded` are positive in all seven start years.
  `stability/held` is positive in five of six.
- No survivor has an exit rate that differs by 5 points across its thirds, and both bounds
  runs are over 3 points for each.
- **One thing to carry into the reading of `investment`.** Its best third (the lowest growth of
  invested capital) ends in `low_ic` 5.2% of the time, against 1.3% and 1.2% for the other two.
  A `low_ic` firm with positive operating income is coded as held, as registered. The rank
  outcome of rule 4 uses observed firms only and is positive (+0.098), so the coding is not the
  whole effect; it is part of it.
- `investment` on outcome B, which has no registered sign and is not a test: −0.090.

The other eight tests, the full per-year table and the counts of rows the sector control could
not use are in `raw-2026-10-04-durability/discovery.json`.

## Holdout (start years 2018-2021)

Computed after the section above was committed (`80bf4a7`), on the same data file and the same
code digest. A temporal replication with overlapping firms, not an independent sample.

1,481 cohort firm-years. States at `t+3`: observed 86.6%, `exit` 7.8%, `gap` 3.4%, `low_ic`
2.2%. Hold rate (outcome A) 53.9%; compounded (outcome B) 32.4%. No bootstrap had a replication
that could not be fitted. The bar is max(6 pp, 1.64 × SE), and half of β must survive the SIC-3
cells.

| test | n | β | SE | bar | 95% interval | β SIC-3 | raw spread (the wrong metric) | holdout rule | β on firms in no discovery cohort (n) | passes all five |
|---|---|---|---|---|---|---|---|---|---|---|
| `track/held` | 1250 | +0.338 | 0.072 | 0.117 | +0.200 to +0.483 | +0.329 | +0.341 | Y | +0.312 (508) | no |
| `stability/held` | 1250 | +0.216 | 0.066 | 0.108 | +0.089 to +0.348 | +0.257 | +0.288 | Y | +0.272 (508) | **YES** |
| `investment/held` | 1288 | +0.201 | 0.055 | 0.091 | +0.090 to +0.306 | +0.202 | +0.158 | Y | +0.234 (541) | **YES** |
| `share_stability/held` | 1069 | +0.111 | 0.066 | 0.108 | -0.011 to +0.243 | +0.058 | +0.087 | Y | +0.118 (421) | no |
| `gross_margin/held` | 996 | -0.037 | 0.076 | 0.124 | -0.176 to +0.118 | -0.115 | +0.018 | n | -0.022 (412) | no |
| `incremental_roic/held` | 868 | -0.071 | 0.075 | 0.123 | -0.227 to +0.065 | -0.091 | +0.027 | n | -0.128 (313) | no |
| `track/compounded` | 1250 | +0.188 | 0.068 | 0.112 | +0.058 to +0.328 | +0.153 | +0.173 | Y | +0.125 (508) | no |
| `stability/compounded` | 1250 | +0.105 | 0.068 | 0.111 | -0.032 to +0.230 | +0.102 | +0.150 | n | +0.052 (508) | no |
| `share_stability/compounded` | 1069 | +0.032 | 0.071 | 0.117 | -0.114 to +0.174 | -0.003 | +0.055 | n | +0.098 (421) | no |
| `gross_margin/compounded` | 996 | +0.107 | 0.083 | 0.136 | -0.041 to +0.271 | -0.037 | +0.050 | n | +0.087 (412) | no |
| `incremental_roic/compounded` | 868 | +0.081 | 0.077 | 0.126 | -0.082 to +0.221 | +0.083 | +0.067 | n | +0.117 (313) | no |

The three discovery survivors:

| test | discovery β (SE) · SIC-3 β | holdout β (SE) · bar · SIC-3 β | result |
|---|---|---|---|
| `investment/held` | +0.149 (0.047) · +0.150 | +0.201 (0.055) · 0.091 · +0.202 | **passes all five** |
| `stability/held` | +0.150 (0.059) · +0.086 | +0.216 (0.066) · 0.108 · +0.257 | **passes all five** |
| `gross_margin/compounded` | +0.167 (0.069) · +0.113 | +0.107 (0.083) · 0.136 · −0.037 | fails rule 2 and rule 5 |

β by start year across both windows, for the two passes and for `track`:

| test | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `investment/held` | +0.236 | +0.219 | +0.147 | +0.131 | +0.062 | +0.144 | +0.122 | +0.169 | +0.072 | +0.200 | +0.333 |
| `stability/held` | — | +0.007 | +0.199 | +0.320 | −0.023 | +0.128 | +0.219 | −0.037 | −0.084 | +0.347 | +0.617 |
| `track/held` | — | −0.062 | +0.182 | +0.252 | +0.014 | +0.085 | +0.153 | +0.062 | −0.080 | +0.533 | +0.785 |

## Verdict

**Two tests passed all five rules: `investment/held` and `stability/held`.** By the
pre-registered decision rule, the first row applies (at least one test passes): Phase 1 is a
`/deep` context line with the passing predictors only. The row "only `track` and/or
`stability` pass" does not apply, because `investment` passed. `stability` is still to be
labelled a track-record line: that comes from the reading fixed in the pre-registration
(Limitations, amendment 4f), not from that row. No scoring leg, no gate, no flag, no discovery
list. `scoring.score()` is not touched.

**How strong the evidence is.** No test of "marginal" was registered. The margins:

| | `investment/held` | `stability/held` |
|---|---|---|
| β over its bar, discovery | 4.9 points | 3.2 points |
| β over its bar, holdout | 11.0 points | 10.9 points |
| SIC-3 β over half of β, discovery | 7.5 points | **1.1 points** |
| SIC-3 β over half of β, holdout | 10.1 points | 14.9 points |

Two or more discovery survivors among eleven tests would occur by chance at most about 5% of
the time at the registered false-pass rate of 3.5% a test, before the holdout. The holdout
adds less than its name suggests: the windows share firms, the predictors are correlated, and
five of the eleven tests clear the holdout rule (three of them had failed discovery:
`track/held`, `track/compounded`, `share_stability/held`). This is evidence against chance for
`investment`, not proof. For `stability` chance is not the right comparison: a track-record
measure is expected to be positive with no durability trait at all (amendment 4f).

**`investment` is the more consistent pass.** Its β is not larger than that of `stability`
(+0.149 and +0.150 on discovery, +0.201 and +0.216 on the holdout). It is positive in every
start year, it does not rest on two of them, and its SIC-3 β equals its β. In words: among
firms with a top-quintile ROIC, of the same sector and start year and with ROIC level and size
held fixed, the firm with the lowest growth of invested capital in the last year is 15 points
(discovery) to 20 points (holdout) more likely to still be in the top quintile three years
later than the firm with the highest, on a straight-line fit across the rank range and around
a base rate of 59% and 54%.

- β is positive in all eleven start years, 2011 to 2021.
- The SIC-3 β equals β in both windows (+0.150 and +0.202): it is not a sub-industry label.
- Among firms that were in no discovery cohort the holdout β is +0.234 (n = 541).
- Both bounds runs on discovery are over 13 points (+0.166 and +0.133); without energy and
  mining β is +0.145 and +0.197.
- One caveat, seen in discovery and stated before the holdout: the lowest-growth third ends in
  `low_ic` more often (5.2% against 1.3% and 1.2% on discovery; 2.9% against 1.7% and 1.2% on
  the holdout), and a `low_ic` firm with positive operating income is coded as held. The rank
  outcome on observed firms is positive (+0.098 on discovery), so the coding is not the whole
  effect. The excess `low_ic` share of the best third is 4.0 points on discovery and 1.5 on the
  holdout, which bounds what the coding can add to a β of 15 and 20 points. The test was not
  run without those firms.
- A possible reading, NOT tested here: a firm that shrinks its invested capital, for example by
  buying back shares, raises an accounting ROIC with no change in the business, and a firm that
  adds capital fast, for example by an acquisition, lowers it.

**`stability` passes, and its holdout rests on two start years.** A firm whose ROIC rank moved
less over the four years to `t` holds more often: +0.150 on discovery, +0.216 on the holdout.

- The holdout β by start year is −0.037 (2018), −0.084 (2019), +0.347 (2020) and +0.617
  (2021). The pass comes from the cohorts formed in 2020 and 2021, whose outcome years are 2023
  and 2024. In 2018 and 2019 the sign is the other way. On discovery it is positive in five of
  six years.
- By the reading fixed before the data (pre-registration, Limitations and 4f), this is a longer
  measure of the same level: a track record adds to one year's figure. It is not evidence of a
  separate trait of durability, and Phase 1 must label it as a track-record line.
- `track`, the other history predictor, shows the same shape and did not pass: it failed two
  discovery rules (+0.109 against a bar of 0.137; +0.038 under SIC-3 cells) and then cleared
  the holdout rule with +0.338, which is +0.533 in 2020 and +0.785 in 2021. A predictor that
  fails discovery does not pass, whatever its holdout.

**`gross_margin` on outcome B survived discovery and failed the holdout.** +0.107 against a bar
of 0.136, and −0.037 under SIC-3 cells. This is what the holdout is for. It is positive in both
windows (+0.167, +0.107), so this too is "not shown" and not "no effect"; a failure of rule 5
says that most of the holdout β did not survive holding the sub-industry fixed.

**The other eight tests did not pass. That is "not shown", not "no effect".** To clear its bar
an effect had to be about 12 to 16 points on discovery and 11 to 13 on the holdout, from the
worst to the best rank. An effect of exactly that size clears the bar about half the time, and
a smaller one is not shown either way. `share_stability/held` is positive in both windows
(+0.068, +0.111): it failed discovery (bar 0.124) and cleared the holdout rule by 0.4 point.
`incremental_roic/held` is negative in both (−0.056, −0.071) with intervals that include zero;
the pre-registration expected that it could be wrong-signed.

**The raw tercile spread is not the result.** On discovery it is +0.243 for `track/held`
against a controlled β of +0.109, and +0.058 for `gross_margin/held` against +0.121. Do not
quote a raw spread.

What this does NOT show, in any case: that predicted persistence earns a return. The claim is
about an accounting ratio staying in the top quintile.

## Reported, not decision-bearing

- **β by start year:** the table above for the passes. For the other tests,
  `raw-2026-10-04-durability/discovery.json` and `holdout.json`. On the holdout the history
  predictors (`track`, `stability`, on both outcomes) are small or negative in 2018 and 2019
  and large and positive in 2020 and 2021 (`track/held`: +0.062, −0.080, +0.533, +0.785).
  `incremental_roic` goes the other way on both outcomes (on A: +0.155, +0.234, −0.252,
  −0.463).
- **Sample size by start year (`n_by_year`):** `track` and `stability` have no 2011 rows.
  On outcome A `share_stability` has 85 rows in 2011 and 212 to 241 a year after;
  `incremental_roic` has 47 in 2011, 103 in 2012 and 135 to 184 a year after: the thin early
  years the pre-registration expected.
- **Excluding mining, oil and gas, and refining:** every β moves by less than 2 points on
  both windows. The largest move is `incremental_roic/held` on the holdout, −0.071 to −0.089.
- **Exit rate by predictor tercile:** one predictor differs by more than 5 points across its
  thirds, in both windows: `share_stability` (9.9% / 15.9% / 12.7% on discovery, 11.4% / 6.0% /
  6.7% on the holdout). `stability` on the holdout is 10.1% / 6.6% / 5.6%. `investment` is
  level in both windows (12.7% / 12.6% / 12.7% and 7.9% / 7.3% / 7.1%).
- **`investment` on outcome B** (no registered sign, not a test): −0.090 on discovery, +0.065
  on the holdout.
- **Firms in no discovery cohort** (holdout): `investment/held` +0.234 (541), `stability/held`
  +0.272 (508), `track/held` +0.312 (508).
- **The sector control:** 3.8% of discovery rows are alone in their SIC-2 cell and 17.3% in
  their SIC-3 cell. No cohort row lacks a SIC code. Within the samples of the two passes the
  rows alone in their SIC-3 cell are 380 of 1,883 (`investment`) and 325 of 1,552
  (`stability`) on discovery, and 227 of 1,288 and 231 of 1,250 on the holdout; alone in their
  SIC-2 cell, 90 and 79, then 41 and 44.
- **A correction to the first half, which is not edited.** "Read before any β" says rule 5
  cost no power at 15% alone on the synthetic tables. The share for the two passes is about 20%
  on discovery and 18% on the holdout. The synthetic cost was nil at 15% and 5.5 points at 34%;
  it is likely small here and was not measured at this value.
- **`gap` and `low_ic` by tercile on the holdout** (worst / mid / best). `investment/held`:
  `gap` 4.4% / 1.9% / 4.1%, `low_ic` 1.7% / 1.2% / 2.9%. `stability/held`: `gap` 2.8% / 3.2% /
  4.3%, `low_ic` 2.8% / 2.4% / 0.9%. The best third of `stability` has the most `gap` rows, and
  `gap` is not bracketed by any bounds run.
- **The two windows differ.** The hold rate is 58.6% on discovery and 53.9% on the holdout; the
  `exit` share is 12.9% and 7.8%. The bounds runs are a discovery rule only.
- **Provenance.** The three outputs carry the same data hashes and the same code digest. Their
  `code_commit` differs (`8f86335`, `e3c6c77`, `80bf4a7`) because each result was committed
  before the next step ran; no code file changed between them.

## Limits of this verdict

All limitations of the pre-registration apply. Four matter most for a reader of the two passes:

- **Coverage.** In 2011–2017 the universe leaves out 257 to 282 larger filers a year for a
  revenue tag (§Gates). The discovery cohorts are drawn from the filers that used one of four
  revenue tags.
- **Accounting ROIC.** Invested capital is equity plus tagged debt. 39% to 51% of each
  discovery cohort has debt of zero, tagged or not. Buybacks and write-downs move the
  denominator.
- **One author, one sitting, six predictors.** Before the data, with no number seen and on
  reviewers' findings, the definition of `share_stability` was changed (amendment 4) and the
  2012 start of `track` and `stability` was enforced (amendment 3). The holdout is the only
  protection against the choice of predictors.
- **Two start years carry the `stability` pass.** In the 2020 and 2021 cohorts β is large and
  positive for the history predictors and for `share_stability`, and negative for
  `incremental_roic`. The study does not explain why.

## Not run, on purpose

A top-decile cohort, a 5-year horizon, an inflation-indexed revenue floor, cohorts formed
within SIC-2. Each needs its own pre-registration.

## Addendum 1 — after the final review of the whole branch (2026-10-07)

Appended. Nothing above is edited. A fresh reviewer read the whole branch after the verdict was
committed. The pass list is unchanged. What changes is how the `investment` pass may be read.

**The `investment` pass is under-caveated above.** The predictor is last year's growth of the
outcome's own denominator. Four ways that can produce the result with no change in what a
business earns on its capital, none of them tested by this study:

- **The denominator persists.** Growth of invested capital is serially correlated. Low growth
  at `t` forecasts a smaller invested capital at `t+3` than a fast grower has, and so a higher
  ROIC on the same profit.
- **Cash and payout.** Invested capital is equity plus debt, cash included. 39% to 51% of each
  discovery cohort has debt of zero, and for those firms `investment` is the growth of book
  equity, which is mostly retained earnings less payouts and buybacks.
- **Acquired capital.** A purchase is booked at the price paid, so the capital it adds earns
  close to the cost of capital by construction.
- **A windfall year.** A profit spike raises NOPAT and retained equity together. At a fixed
  ROIC rank, high `investment` can mark a firm that has just arrived in the top quintile.

So `investment` is a regularity that replicated in every start year, and it is NOT yet a
finding about businesses. `stability` carries a label that deflates it (a track-record line).
`investment` needs one at least as strong: **a statement about the ratio and about capital
growth.** Phase 1 must word it that way unless the decomposition below says more.

**Not shown: that the two passes are two findings.** No model holds `stability`, `track` or the
prior year's ROIC fixed while testing `investment`, or the reverse.

**Corrections and unregistered conditions.**

- The header says every table is printed by `scripts/print_durability_tables.py`. That holds
  for the gates table and the two eleven-row tables. The tables of survivors, margins and
  start years across both windows were assembled by hand from the same files; an independent
  check recomputed every cell.
- `investment` is defined only when the prior year's ROIC is defined in snapshot `t`
  (`durability_study._predictors`). The pre-registration says IC(`t`) / IC(`t-1`) − 1 and does
  not state the condition. It removes few rows: the `investment` sample is the largest of the
  six.
- The count of commits on the branch and the order of every commit against the timestamps in
  the raw outputs were checked: the pre-registration was last edited before the first
  real-data output, no code changed after it, and the first half of this note was not edited
  after the holdout ran. Git cannot prove more than local timestamps: the branch is not pushed.

**Before a `/deep` line imports `shortlist/durability.py` (Phase 1), two things must be fixed.**
The 10-K-only form filter lives in the compaction (`backtest/durability_data.py`), not in the
shared module, so `durability.snapshot` on raw company facts would read a 20-F row the study
never saw; the module's docstring says the two can never differ, and that is not yet true. And
importing it makes `providers/_xbrl_facts.py` a dependency of a production path, which the
repo's extractor rule says it is not.

**The data file.** `companyfacts.zip` is overwritten by the SEC every night and the copy this
study read is gone. The compacted file (11 MB, SHA-256 `2d617f70…aa5639`) is the only record of
the data. It is committed beside the raw outputs as `companyfacts-10k.jsonl.gz`; to run a step
again, copy it to `.cache/durability/`.

**A follow-up, registered before it is run:** `2026-10-07-durability-decomposition-prereg.md`.
It asks whether operating profit held or only the denominator, and whether the two passes are
one. It is a decomposition of a result that has been seen. It cannot change the pass list.
