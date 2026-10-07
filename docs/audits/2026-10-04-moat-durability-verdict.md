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
