# Brief self-consistency — base rate before building a detector (2026-08-22)

**Verdict: DO NOT build a per-brief consistency detector.** Measured rate is **2 of 17**
briefs overall and **0 of 9** under the current (2026-08-18 materiality) prompt. `TODO.md`
§2a pre-committed the decision rule — *"if the rate is ~1 in 17, the honest fix is a
prompt-only clause, not a per-brief second call"* — and the measurement came in at or below
that line, on a prompt that already ships.

This closes the *build* question. It does **not** claim the defect is eliminated; §4 below
is explicit about how weak a `0/9` is.

---

## 1. Why this was measured before it was built

The work item was opened after the 2026-08-20 HDSN brief said operating cash flow "turned
sharply negative in FY2025 **on an inventory build**" in its Bear case while a red flag called
the same facts "a genuine cash-burn/liquidity stress trend" — two incompatible readings of one
number in one document, which sent the 2026-08-21 inventory investigation down a wrong path.

`TODO.md` §2a flagged the bias directly: the evidence was `n=1` and the "highest-value" label
was written by the session that had just lost hours to it — the point of maximum bias. It
required a base-rate count first, and it named two things the count had to settle. Both are
answered in §3.

## 2. Method

**The criterion was written before any brief was read.** Verbatim:

> A **contradiction** = two statements in ONE brief that cannot both be true of the same
> fact — same metric, same entity, same period — where at least one is asserted as fact
> rather than framed as a competing interpretation.
>
> NOT a contradiction: (N1) bull_case vs bear_case disagreeing about outlook; (N2) different
> periods or scopes; (N3) hedged language against a directional claim; (N4) a risk naming a
> downside the bull case omits.
>
> IS a contradiction: (C1) two different VALUES for the same number; (C2) opposing CAUSAL
> attributions of the same fact, both stated as fact; (C3) section A asserts X, section B
> asserts not-X about the same present-tense state.

**Corpus:** all 17 persisted briefs under `research/`, 11 tickers. Sections compared:
`business_model_summary`, `moat.summary`, every `risks[]` and `red_flags[]` claim,
`management_capital_allocation`, every `reconciliation[]` tension, `thesis.bull_case`,
`thesis.bear_case`, `what_would_change_my_mind`, `synthesis`.

**One interpretation was fixed during the scan and applied uniformly**, and it is recorded
here rather than left implicit: N1 was read as covering *any* pairing where one side is
`bull_case` or `bear_case`, not only bull-vs-bear. Those two sections are advocacy by
construction, so a conflict with an analytical section is the document working as designed.
Numeric conflicts (C1) still count regardless of which section they sit in.

## 3. Results

Split at the 2026-08-18 materiality prompt, never pooled — as §2a required.

| | briefs | tickers | clean hits | borderline | clean |
|---|---|---|---|---|---|
| **Pre**-2026-08-18 prompt (sonnet-4-6) | 8 | 7 | **2** | 3 | 3 |
| **Post**-2026-08-18 prompt (sonnet-5) | 9 | 6 | **0** | 2 | 7 |

### The two confirmed hits, both pre-prompt

1. **NVDA 2026-06-15 — C2, red flag vs reconciliation.** `RECON[quality]`: the FY2026 gross
   margin dip was "driven by the one-time H20 charge **rather than structural degradation**".
   `REDFLAG`: the same 390 bp FY2026 compression "signal[s] **recurring structural** margin
   risk during each annual architecture transition". One number, two causal readings, both
   asserted, and the reconciliation explicitly negates the word the red flag asserts.
   **This is the HDSN shape exactly** — red flag against another analytical section.
2. **VMI 2026-06-22 — C1, bull case vs reconciliation.** `BULL`: Utility backlog
   "$1.55B, +22% YoY". `RECON[growth]`: Infrastructure backlog "grew 22% to **$1.65B**". The
   same growth rate attached to two different bases. Either a value conflict or an unlabelled
   scope change (Utility is a product line *inside* Infrastructure); a reader is misled either
   way.

### The five borderline cases, and why they were not counted

Recorded because a naive detector would fire on all five, and four of them are noise:

- **FCN** — `$858.7M` (management section) vs `$858.6M` (reconciliation) for one buyback
  program. A rounding slip. Technically C1; counting it would inflate the rate with noise.
- **NVDA 2026-06-07** — two red flags attribute the same 390 bp compression to two causes
  (rack complexity; the H20 charge) with no explicit negation. Both can be true.
- **DGX** — `RECON[value]` calls the implied rate conservative against a 25.4% FCF CAGR while
  `RECON[growth]` says that CAGR "overstate[s] sustainable organic momentum". The value entry
  hedges ("if recent FCF expansion proves durable"), so N3 applies.
- **AAPL 2026-08-18** — `RECON[quality|confirms]` cites the margin-expansion narrative while a
  red flag calls recent margin strength "materially inflated by one-off tariff refunds". Plausibly
  different periods (FY2025 structural vs the latest quarter), so N2 applies. The same brief's
  `RECON[momentum]` *does* name the tariff refund, so the brief is not blind to it.
- **INTC 2026-08-19** — "investing heavily in AI-capable products" (business) against "R&D
  spend has been cut" (management). A mix shift inside a smaller budget reconciles them.

### The two questions §2a said the scan had to settle

- **Is the bull/bear boundary the detector's only real target?** No — and this is the more
  useful finding. Neither confirmed hit is a bull-vs-bear conflict. Both involve the
  **reconciliation** section, which is where the brief is *supposed* to name and resolve
  tensions between the quant view and the filing. That is a narrower and better-defined target
  than "the whole document", and it is not the boundary the HDSN case suggested.
- **Do pre-prompt briefs over-represent padding-driven contradictions?** Yes, consistently.
  Both hits are pre-prompt, and the mechanism is visible in the corpus: pre-prompt briefs run
  5–8 red flags and 12–13 risks, post-prompt briefs run 1–7 red flags (UAL and AAPL 2026-08-19
  emit **none**) and 8–12 risks. Fewer marginal assertions means fewer chances for two of them
  to collide. This is the same list-shortening the 2026-08-19 live verification measured for
  `risks`, showing up here as a second-order consistency benefit that was not its stated goal.

## 4. What this measurement does NOT support

- **`0/9` is not "solved".** By the rule of three, zero events in nine trials puts the 95%
  upper bound near **33%**. The honest reading is "no longer the most likely defect", not
  "absent".
- **The effective post-prompt sample is smaller than 9.** It is 6 tickers, and 7 of the 9
  briefs are repeat runs over AAPL (×2), JPM (×3) and INTC (×2) against the same filing sets.
  Only **UAL** and **FISV** are independent fresh names. Treat the post-prompt evidence as
  roughly n=4–5.
- **The motivating case is not in the corpus.** No HDSN brief was ever persisted under
  `research/` (only a `.cache/famafrench/` artifact survives). The one known positive example
  is therefore excluded by construction, and the measured rate is a rate over briefs that
  happen to have been kept.
- **Single unblinded rater.** The criterion was pre-registered before reading, which is the
  main defence available here, but there is no second rater and no blinding.

## 5. A cross-brief inconsistency, found incidentally

Out of scope for "a brief against itself", recorded because the scan surfaced it for free and
nothing else would have: **JPM 2026-08-18 and JPM 2026-08-19, over the same filing set, two
days apart, disagree about the direction of capital.** The 08-18 brief says the Firm is
"still **building** capital ratios"; the 08-19 brief says buybacks and a raised dividend come
"alongside a **shrinking** CET1 buffer", and its bear case says the buffer "has thinned".

This is a stability question about the model's reading, not a self-consistency defect, and it
is not actionable on `n=1`. Noted so a later session with more repeat-run briefs can test it.

## 6. Reproduction

The scan needed no new code and no network. `scripts/` holds nothing for it because the
extraction is four lines of `json.load` over `research/*/*.json`; the sections compared are
listed in §2. Re-running it after more briefs accumulate is the intended follow-up — the
post-prompt count is the one worth growing.
