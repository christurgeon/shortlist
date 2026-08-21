# `operating_income` missing on the EDGAR-only path — diagnosed, NO FIX WORTH BUILDING

**Date:** 2026-08-21 · **Change:** none. This closes the `TODO.md` §3 entry
"`operating_income` missing on 41% of the FMP-gated path — DIAGNOSE, do not assume".
Reproduce with `docs/audits/scripts/probe_operating_income_gap.py {store,live}`.

**Verdict: the entry's two candidate causes are both refuted, its proposed remedy would
change nothing, and its stated payoff is unreachable.** Measured ceiling for an EDGAR-side
fix is **62.8% → 67.4%** of EDGAR-only snapshots, not "~56% toward ~97%", and 23 of the 68
recoverable snapshots carry a value that CONFLICTS with the one FMP supplies for the same
fiscal year.

## Measurement basis

Accumulate snapshot store at `/opt/shortlist/state/snapshots`: **2,146 snapshots, 42
tickers, 60 capture days (2026-06-22 .. 2026-08-20)**.

**Scan trap, cost an hour on the first pass.** The store gzips snapshots after a few days,
so most files are `.json.gz`. A `.json`-only scan reads just the first two uncompressed
weeks — a 24-ticker / 15-day subset that happens to predate the break — and reports the gap
as **0.4%** instead of 25.5%. It also silently excludes every bank and every affected
non-financial. Any future store scan must read both extensions.

## What actually drives presence: FMP, not extraction

| statements merge | `operating_income` present | absent |
|---|---:|---:|
| FMP won (`provenance.statements` contains `fmp`) | **672** | **0** |
| EDGAR only | 926 | **548** |

`operating_income` is present on **100%** of FMP-won snapshots and **62.8%** of EDGAR-only
ones. All 548 misses are EDGAR-only. `gross_profit` is **0 / 1,474** on EDGAR-only snapshots —
EDGAR supplies it *never*, which is the same root cause as the `TODO.md` §2a
"cost of revenue from EDGAR" entry.

## Both hypotheses in the TODO entry are refuted

The entry asked whether the intra-ticker flips (DIS, JPM, NKE) were "a clean split at one
date (version change) or interleaved (non-determinism, a real bug)".

**Neither. Every flip is a clean contiguous run, and every boundary is an FMP quota event.**

```
DIS  present 06-26..07-07 | MISSING 07-08..07-30 | present 07-31..08-20
NKE  present 06-26..07-07 | MISSING 07-08..07-30 | present 07-31..08-20
JPM  present 06-22..07-07 | MISSING 07-08..08-03 | present 08-04..08-11 | MISSING 08-12..08-20
```

Diffing the snapshots across each boundary, `provenance.statements` flips in lockstep:
`['fmp'] → ['edgar']` at DIS/NKE 07-08, `['edgar'] → ['edgar','fmp']` at DIS 07-31,
`['edgar','fmp'] → ['edgar']` at JPM 08-12 (with an `fmp.quote` **429** in that snapshot's
errors). `gross_profit` and `ebitda` move with it. There is **no edgartools version drift and
no non-determinism** — per-ticker EDGAR behaviour is 100% deterministic across all 60 days.

This makes the entry a downstream symptom of `TODO.md` §4 (FMP quota over-subscribed), not an
independent defect.

## The third "population" was a red herring

16 tickers each showed exactly **one** missing day. All 16 are **2026-07-07** (WMT's is
2026-06-26) — the day the accumulate universe widened to 42 tickers. On those snapshots the
statements block is `{}` **entirely** and `provenance` has no `statements` key at all: a
whole-run statements failure under blanket FMP 429s, not an `operating_income` extraction
gap. Do not count these as extraction misses.

## Why EDGAR fails, and why the proposed remedy does not help

The entry nominated the raw-`concept`-first rule (`_rows_by_concept`, the `_row_net_income`
pattern) as the candidate remedy, on the theory that these filers "tag the line differently"
and edgartools' lossy `standard_concept` bucket misses it.

**Live-probed, refuted.** For all 9 affected non-financials the rendered income statement
carries **zero** rows with raw concept `us-gaap_OperatingIncomeLoss` — the tag is *absent*,
not mis-bucketed. Controls behave as expected (AAPL 7 rows, MSFT 4). Switching
`_edgar_facts.py:433` to raw-concept-first would recover **nothing**.

HON and LLY tag custom extensions instead (`hon_CostsAndOperatingAndNonoperatingExpenses`,
`lly_CostOfSalesOperatingExpensesAndOtherNet`); XOM presents no operating/gross-profit
concept at all.

## The only surviving route — SEC `companyconcept` — and its ceiling

`companyconcept` carries facts the rendered statement omits, so it was probed separately.
Of the 9 affected non-financials, **6 return HTTP 404** (XOM, IBM, LLY, MRK, CVX, NKE): they
never tag the concept anywhere, in any filing. All 4 banks 404 as well, consistent with the
entry's correct "banks are not a bug" call.

Accounting for the 548-snapshot gap:

| population | snapshots | recoverable? |
|---|---:|---|
| whole-statements-run failure (2026-07-07) | 16 | not an extraction issue |
| banks — correctly uncomputable | 171 | no (and should not be) |
| tag genuinely absent from XBRL | 248 | **no** |
| tag present but STALE — JNJ | 45 | **must not** |
| tag present and current — HON, DIS | 68 | yes, with a caveat |

**JNJ is the trap.** `companyconcept` returns 12 annual facts, but the newest fiscal year end
is **2014-12-28** and the last filing carrying it is **2015-02-24**. JNJ abandoned the tag
eleven years ago. A naive `companyconcept` fallback backfills FY2012–FY2014 operating income
into a 2026 snapshot with no staleness signal. Any such fallback needs a hard recency bound
against the snapshot's own fiscal calendar.

**DIS is the second caveat, and it is a concept conflict, not a bug.** For FY2025 (ending
2025-09-27) SEC's tagged `OperatingIncomeLoss` is **17.551B** while the FMP-won snapshot for
the same fiscal year records **13.832B** — a 27% difference. The SEC tag is total *segment*
operating income; FMP's is the consolidated figure. Feeding the SEC value in as a fallback
would make one ticker's `operating_income` jump by a quarter depending on which source won
the merge that day — and `operating_income` feeds `ebitda`, hence `net_debt_to_ebitda`, hence
the `over_leveraged` **gate**. That is a source-dependent discontinuity in a scored input.

So the honest ceiling is **68 / 548 = 12.4%** of the gap, and only **45 / 548 = 8.2%** (HON
alone) without accepting the DIS conflict. EDGAR-only coverage moves **62.8% → 67.4%**.

## Recommendation

**Do not build the fallback.** It reaches at most 2 of 42 tickers, requires a recency bound
fitted to one observation (JNJ) and a concept-conflict decision on the other (DIS), and the
entry's motivating payoff — "~56% toward ~97%" — is not attainable by any EDGAR-side change,
because 248 of the 548 missing snapshots belong to filers who never tag the concept.

The gap is real but its cause is the **FMP quota** (`TODO.md` §4), and the leverage sits
there: on this store FMP won only 672 / 2,146 snapshots, and 23 of 60 capture dates have zero
FMP-won statements. Resolving the quota decision closes ~100% of the recoverable gap; the
EDGAR route closes 12%.
