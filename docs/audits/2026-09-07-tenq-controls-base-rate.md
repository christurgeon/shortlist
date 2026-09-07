# 10-Q adverse-controls detection: measured, and NOT shipped

**Date:** 2026-09-07 · **Kind:** verdict (a null)
**Scripts:** `scripts/probe_tenq_controls.py`, `scripts/probe_tenq_incremental.py`
**Answers:** `TODO.md` §5, first bullet — "10-K only; the 10-Q base rate is unmeasured"

`docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md` shipped adverse-conclusion
detection on the 10-K and left the 10-Q explicitly open: *"A weakness first disclosed in
a 10-Q Part I Item 4 is invisible for up to three quarters."* `fetch_bundle` already
holds the 10-Q object and `controls.detect` already takes a `form` argument, so the
build was three lines. `TODO.md` gated it on measurement instead.

**Measured. Both pre-registered bars fail. Nothing ships.**

Bars, registered before any data was collected:

1. **Precision 1.000** on hand-read excerpts — the bar the 10-K path cleared.
2. **Incremental yield ≥ 2 tickers** the 10-K path misses. Below that, at a ~1% signal on
   a survivorship-biased currently-listed universe, a real gain is indistinguishable from
   sampling noise.

Result: precision **27/27 = 1.000 (PASS)**, incremental yield **0 of 228 (FAIL)**.

---

## 1. The detector works on 10-Qs. That was never the question.

228 of 233 symbols in `universe_largecap.txt` + `universe_smallmid.txt` resolve to a CIK
— the same 228 as the 2026-08-23 10-K probe, so the two base rates are directly
comparable rather than measured over drifting membership.

EDGAR full-text search over the four narrow phrases in `controls._PHRASES` plus the two
broad recall nets, `forms=10-Q`, 2024-09-07 to 2026-09-07, then the **shipped**
`controls.detect` run against every downloaded hit at `form="10-Q"`:

| | 10-Q (this note) | 10-K (2026-08-23) |
|---|---|---|
| Tickers flagged | **11 / 228 = 4.8%** | 12 / 228 = 5.3% |
| Filings flagged | 27 | 16 |
| Hand-read precision | **27/27 = 1.000** | 16/16 = 1.000 |

Flagged: AMBA, CASH, CWH, JJSF, NEOG, NSSC, SLAB, SMP, TALO, UCTT, USNA.

Every one of the 27 excerpts was read. All 27 are genuine current-period conclusions. The
tense rule ported to the quarterly form without modification — including the two cases
that look like the 10-K probe's dominant failure mode and are not:

- **SLAB** and **USNA** each cite a prior-year 10-K *inside the flagged sentence*. In both
  the citation is the CAUSE clause ("material weaknesses ... as described in ... our Form
  10-K for the fiscal year ended December 30, 2023") while the conclusion verb attaches to
  the quarter end ("were not effective as of September 28, 2024"). The nearest-date rule
  picks the quarter end. Correct, and correct for the right reason.
- **JJSF** and **USNA** were *false positives* on the 10-K path. On the 10-Q path they are
  true positives — these filers really did conclude ineffective for those quarters. The
  same ticker being an FP annually and a TP quarterly is the tense rule working, not a
  contradiction.

No missed positive was found: 9 tickers hit a broad net without hitting a narrow phrase
(BL, CMP, COLL, DIOD, GIII, GRBK, SITM, UBER, VRNS) and none is a narrow-phrase miss.

## 2. The trap: the obvious incremental metric is wrong, and it says "ship"

The first stage-2 metric compared 10-Q hits from a two-year window against the *latest*
10-K. It returned **9 of 11 "missed by the 10-K"** — comfortably over the bar.

It is meaningless. In all 9 cases the clean 10-K's period end is **later** than the
flagged quarter. AMBA's flagged 10-Q covers 2024-10-31; its latest 10-K covers 2026-01-31,
fifteen months on. That 10-K is not missing anything — the weakness was remediated. The
metric was measuring **remediation and calling it lead time.**

Shipping on it would have put resolved findings into briefs as current facts — the exact
failure the 2026-08-23 tense rule exists to prevent, reintroduced one level up in the
measurement instead of in the detector.

The naive number is kept in `probe_tenq_incremental.py` and printed alongside the correct
one, labelled, so the next person meets the trap instead of rediscovering it.

## 3. The correct metric: 0 of 228

A 10-Q buys lead time only when it covers a quarter the annual report does not yet cover.
So the case that matters — and the only one `fetch_bundle` could act on, since it takes
`get_filings(form="10-Q").latest(1)` — is: **the latest 10-Q flags, its period end is
after the latest 10-K's period end, and that 10-K is clean.**

| ticker | latest 10-K | 10-K verdict | latest 10-Q | last flagged quarter | uncovered quarter? | latest 10-Q flags? |
|---|---|---|---|---|---|---|
| AMBA | 2026-01-31 | clean | 2026-07-31 | 2024-10-31 | yes | no |
| CASH | 2025-09-30 | **FLAG** | 2026-06-30 | 2026-03-31 | yes | no |
| CWH  | 2025-12-31 | clean | 2026-06-30 | 2025-09-30 | yes | no |
| JJSF | 2025-09-27 | clean | 2026-06-27 | 2025-06-28 | yes | no |
| NEOG | 2026-05-31 | clean | 2026-02-28 | 2026-02-28 | **no** | yes |
| NSSC | 2026-06-30 | clean | 2026-03-31 | 2025-03-31 | **no** | no |
| SLAB | 2026-01-03 | clean | 2026-07-04 | 2024-09-28 | yes | no |
| SMP  | 2025-12-31 | **FLAG** | 2026-06-30 | 2026-06-30 | yes | yes |
| TALO | 2025-12-31 | clean | 2026-06-30 | 2024-09-30 | yes | no |
| UCTT | 2025-12-26 | clean | 2026-06-26 | 2025-09-26 | yes | no |
| USNA | 2026-01-03 | clean | 2026-07-04 | 2024-09-28 | yes | no |

Two tickers have a flagging latest 10-Q, and neither is incremental:

- **SMP** — the latest 10-Q flags on an uncovered quarter, but **the 10-K flags too**. The
  shipped path already surfaces it. Nothing gained.
- **NEOG** — the latest 10-Q flags, but its period (2026-02-28) is *older* than the latest
  10-K's (2026-05-31, FY end May). The annual report supersedes it and is clean, i.e.
  remediated by year end. Reporting the 10-Q here would be strictly worse than silence.

**Incremental lead-time yield: 0 of 228.**

### One correction, which changes nothing

The first run of stage 2 selected the newest 10-K **or 10-K/A**, the pre-#199 rule (the
checkout it ran against predated that fix). Since #199, merged 2026-09-05,
`_fetch_10k_parsed` takes the newest **exact-form** 10-K and never an amendment
(`2026-09-06-tenk-amendment-selection.md`). Re-checked against submissions data: **none of
the 11 flagged tickers has a 10-K/A newer than the 10-K read**, so every comparison above
used the document the shipped path would use and the verdict is identical under either
rule. The script now enforces the exact-form rule and reports `amendment_superseded` per
ticker, because #199 measured 462 listed tickers currently in that state — a future corpus
will not be so lucky.

This is a complete answer for the universe, not a sample of it. Any lead-time case must be
a ticker that flags on some 10-Q, and all 228 were swept for exactly that.

### Why the mechanism fails even though the base rate is real

The "invisible for up to three quarters" worry assumed a weakness first appearing in an
interim quarter and *persisting* to the point where a brief is generated. Two behaviours
close that window, and both show up in the table:

1. A filer whose controls are ineffective at fiscal year end says so in the 10-K as well
   (CASH, SMP). The annual disclosure is not late; it is concurrent.
2. A filer whose weakness spans only interim quarters has usually remediated by year end,
   so at brief time the 10-K *and* the latest 10-Q are both clean (AMBA, JJSF, SLAB, TALO,
   UCTT, USNA, CWH — 7 of 11).

The uncovered-quarter window is real, but the state "adverse in it, clean in the last
annual" was not observed once.

## 4. The `_SELF_REF` regex gap is real and worth exactly nothing

`controls._SELF_REF` is `end of the period covered by this (annual )?report`. It cannot
match "this **quarterly** report", and FTS counts 2,955 10-Qs using that phrasing in
2026 alone (against 1 in 10-Ks). The gap looked load-bearing for a 10-Q port.

Every flagged filing was scored twice, once with the shipped regex and once widened to
`(annual |quarterly )?`:

```
tickers flagged, SHIPPED regex : 11
tickers flagged, WIDENED regex : 11   (identical set)
filings flagged                : 27 / 27
gained by widening             : none
```

**Zero.** The `_AS_OF` date branch rescues every case — quarterly Item 4 language almost
always names the period-end date somewhere in the 240-char window even when it also uses
the self-referential phrasing, and filers who write "covered by this **Report**" (SMP)
already match the bare-`report` branch. Do not widen this regex on the phrase counts; they
predict an effect that does not exist.

A second variant, `covered by this Form 10-K`, appears in 98 10-K filings YTD 2026 and is
likewise unmatched. It was NOT fixed, on the same reasoning: at a ~5% adverse base rate it
is worth ~0.2 names across the whole 228, below anything this repo can measure.

## 5. What this does NOT close

- **The `ScoreCard` flag question** (`TODO.md` §5, second bullet) is untouched and stays
  open. It is a separate cost question — a whole-document download per screened ticker.
- **Small caps.** Both universes are survivorship-biased currently-listed names. The
  10-K base rate roughly doubled out-of-sample in $300M–$5B names (5.3% → 10.0%), so the
  10-Q base rate probably rises there too. That would change §1, not §3: remediation and
  concurrent annual disclosure are filer behaviours, not size effects. Reopening needs a
  measured lead-time case, not a higher base rate.
- **A remediation/staleness line.** 7 of 11 filers flagged a weakness and then went clean.
  Whether "adverse last year, clean now" is worth a `/deep` line is a different question
  and was not measured here.
