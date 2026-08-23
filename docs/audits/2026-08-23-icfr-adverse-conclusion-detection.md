# Adverse internal-control conclusions, and the filing forms we were not watching

**Date:** 2026-08-23 · **Kind:** verdict (item 2) + evidence (item 1)
**Scripts:** `scripts/probe_filing_form_base_rates.py`,
`scripts/probe_icfr_phrase_precision.py`, `scripts/probe_icfr_out_of_sample.py`,
`scripts/probe_icfr_window_sensitivity.py`, `scripts/probe_edgar_index_limit.py`

`docs/DATA_SOURCES.md` §2 gap 3 — "no earnings-quality red-flag" — asked for accruals,
Beneish M and Altman Z. Those are inference. Management's own statement that internal
control over financial reporting was not effective is a **disclosure**, it is free, and
nothing in the stack was reading it.

Two things ship. **Item 2** (the signal): an adverse-conclusion detector on the `/deep`
research path. **Item 1** (the ride-along): six filing forms the events source was not
watching, at zero request cost.

---

## 1. The naive version does not work

Phrase hit rates over the 228 resolvable tickers in the two committed universes
(`universe_largecap.txt` + `universe_smallmid.txt`), 10-K only, two-year window:

| Phrase | Tickers hit |
|---|---|
| `"material weakness"` | **226 / 228 — 99.1%** |
| `"internal control over financial reporting was not effective"` | 7 — 3.1% |
| `"did not maintain effective internal control over financial reporting"` | 5 — 2.2% |
| `"disclosure controls and procedures were not effective"` | 12 — 5.3% |
| `"substantial doubt about our ability to continue as a going concern"` | 0 — 0.0% |

`"material weakness"` is in the auditor's standard definition paragraph of nearly every
10-K. 215 of its 226 hits are boilerplate-only. **A material-weakness keyword search is
worthless and must not be built.**

Two deliberately broad recall nets were also run — `"identified a material weakness"`
(15) and `"material weaknesses in our internal control over financial reporting"` (20).
The second is actively dangerous: it matches **negations**. SPGI and HMN both state
there were *no* material weaknesses and both hit it.

## 2. Tense, not boilerplate, is the dominant failure mode

The false positives that survive the adverse phrasing are **prior-period weaknesses,
since remediated, discussed in a later filing**:

- **JJSF** FY2025 10-K says "internal control over financial reporting was not
  effective" — dated 2024-09-28, against a 2025-09-27 period end.
- **USNA** repeats a 2023-12-30 conclusion in the risk factors of both its FY2024 and
  FY2025 10-Ks.
- **CENT**'s FY2024 10-K states the conclusion "as of such date", where the date is
  2023-09-30 and appears *before* the phrase.

So a phrase counts only when its sentence anchors to **this filing's** period end: an
`as of <date>` within `window_chars` matching `period_of_report`, or the self-referential
"end of the period covered by this report" (which is the only thing that catches SMP).

## 3. Results

**In-sample** — every candidate context read by hand and labelled, then the mechanical
rule run back against those labels, 68 filings:

```
tp=16  fp=0  tn=52  fn=0     precision=1.000  recall=1.000
```

Base rate **12 tickers of 228 = 5.3%**. This is in-sample: the rule was written after
reading the filings, so it formalises the labels rather than independently confirming
them.

**Out-of-sample** — 120 held-out names, $300M–$5B, seeded sample, disjoint from the 228:

- **12 tickers / 16 filings flagged (10.0%)**. All 16 excerpts read: **16 of 16 genuine.**
- The 14 names flagged only by the broad nets were each checked: **14 of 14 concluded
  their controls were effective** (2 file no 10-K). No missed positive found.

The base rate roughly doubles in smaller names — 5.3% versus 10.0% — which is where
`/prospect` sources candidates.

**Independent corroboration.** CASH filed 8-K Items 3.01 *and* 4.01; GRBK filed Item
4.02. Two unrelated mechanisms, the same companies.

**Restatement pattern.** GRBK, CASH, CMP and TALO all show a clean original 10-K
followed by an adverse 10-K/A. `get_filings(form="10-K")` includes amendments and
`latest(1)` sorts by filing date, so the research path sees the amendment.

## 4. Parameter sensitivity — a plateau, not a cliff

23 filings (13 positive), sweeping both knobs; cells are `tp/fp/fn`, ideal `13/0/0`:

```
 window        tol=7      tol=20      tol=45      tol=90     tol=200
    100       13/0/0      13/0/0      13/0/0      13/0/0      13/0/0
    240       13/0/0      13/0/0      13/0/0      13/0/0      13/0/0
    800       13/0/0      13/0/0      13/0/0      13/0/0      13/0/0
   1600       13/3/0      13/3/0      13/3/0      13/3/0      13/3/0
```

Flat from 100 to 800; only 1600 bleeds into unrelated dates. Tolerance is nearly inert
because prior-period references are ~365 days away. Both shipped values are **slack, not
tuned knobs**.

## 5. Where the text has to come from

Measured over 15 filings, then 8:

| Source | Result |
|---|---|
| `FilingText.combined()` (business + MD&A + risk factors) | fires on **2 of 7** known positives |
| `+ part_ii_item_9a`, whitespace-normalised | 14 of 15; returns **0 chars for 3 of 15** filers; misses HP |
| `filing.text()` (whole document) | **8 of 8**, 1.4–3.8s, +0–27MB RSS |

The conclusion lives in Item 9A, which the research path does not extract, and
edgartools' Item 9A accessor under-captures. The whole-document text is the only
reliable source.

**Cost correction.** An earlier draft of this design claimed detection was free.
It is not: `filing.text()` re-downloads rather than reusing what `.obj()` parsed, so it
costs **2 extra sec.gov requests per brief** (index page + document), through the same
process-wide throttle. The first attempt to count them was invalidated by a broken test
harness and the wrong number stood for a while. On the `/deep` path — one ticker, a 900s
budget — this is negligible, but it is not zero.

## 6. Two bugs the validation caught

1. **Whitespace normalisation is load-bearing.** On raw section text the window
   straddles newlines and the date match fails; CASH and GPK both flipped
   false-to-true on the same document once flattened. Normalisation now happens
   inside `detect`, not in the caller.
2. **`as of` was case-sensitive**, so a sentence-initial "As of December 31, 2025,
   management concluded…" would have been missed. Found by a synthetic test, not by
   the corpus — no filing in the corpus opens that way. Fixed, and the whole-document
   corpus re-verified afterwards at `tp=13 fp=0 tn=10 fn=0`.

A third error was mine, not the code's: VITL was labelled positive from its FY2024
filing when its FY2025 10-K has no hits at all. The rule was right.

---

## 7. Item 1 — the forms, and what they are worth

Base rates over the same 228 names, from the submissions index the events source
**already fetches**:

| Form / item | 90 days | 365 days |
|---|---|---|
| NT 10-K, NT 10-Q | 0.0% | **0.0%** |
| 8-K 4.02 (non-reliance) | 0.0% | 0.4% |
| 8-K 4.01 (auditor change) | 0.4% | 1.8% |
| 8-K 3.01 (listing deficiency) | 0.0% | 1.3% |
| UPLOAD / CORRESP (comment letters) | 0.0% | 6.6% / 7.5% |
| 424B5 / S-3ASR (shelf) | 7.0% / 5.7% | 18.4% / 19.7% |

**Cost is zero.** Widening the filter from 8 forms to 18 made **0 additional HTTP
requests** — `Company(...).get_filings(form=[...])` filters an already-loaded index:

```
current  8-form filter : 2 requests, 504 filings
proposed 18-form filter: 0 requests, 573 filings
```

**Yield is low and that is the honest framing.** Late-filing notifications fired zero
times in a year across 228 names. These are insurance, bought because the price is zero,
not features. The universes are survivorship-biased currently-listed names, which is
precisely where distress forms are rarest.

**Form 25 / 25-NSE are excluded on the evidence.** 17 of 228 filed one within a year,
essentially all for a matured note or warrant rather than the issuer's common stock. As
a delisting flag it would be wrong ~7% of the time, on exactly the names where a
delisting flag would matter most.

### `index_limit` was already too small

The slice is taken newest-first **before** the lookback filter, so a high-frequency
filer's routine 144/13G stream can crowd a rare event out of the window. Matched filings
per ticker in 90 days:

| Filter | median | p95 | p99 | max |
|---|---|---|---|---|
| current 8 forms | 6 | 18 | 29 | 271 (BLK) |
| proposed 15 forms | 6 | 18 | 33 | 271 (BLK) |

**40 was already binding for BLK before this change**, and the wider form list moves p99
only 29 → 33. Raised to 120 (~3.6x headroom over p99, in-memory, no request cost).
A BLK-class filer still truncates. Raising it changes behaviour only for names that were
already above 40 — 1 of 228.

---

## 8. What this does NOT do

- **No `ScoreCard` flag for the controls finding.** `score()` runs before research and
  the screener path never downloads 10-K text, so a flag would mean a document download
  per screened ticker. Measure before paying. The item-1 *forms* do produce flags,
  because those genuinely cost nothing.
- **10-K only.** A weakness first disclosed in a 10-Q Part I Item 4 is missed for up to
  three quarters. The base rate there is unmeasured; `fetch_bundle` already holds the
  10-Q object, so the extension is cheap when someone measures it.
- **Recall is bounded by six phrases.** A weakness disclosed in wording outside all six
  is invisible to the detector *and* to the ground truth built from them. The broad nets
  suggest saturation; they do not prove it. An external cross-check needs Audit
  Analytics, which is paid.
- **Going-concern detection is unvalidatable here** (0 of 228), same as the late-filing
  forms, and for the same reason.
