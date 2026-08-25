# VERDICT — the options line needs a destination field (2026-08-25)

**Result: the shipped line was used in 0 of 3 briefs. With a REQUIRED clause naming
`thesis`, 3 of 3.** Pre-registration, written and committed before any result was seen:
`2026-08-25-options-line-destination-prereg.md`.

This replicates, on a second feature, the exact defect
`2026-08-22-deep-arithmetic-clause-verification.md` diagnosed for the arithmetic clause —
a clause with no named destination field is ignored — and the exact fix.

## What prompted it

A live `/deep` on NVDA (2026-08-24, `outcome=ok`, $0.4531) rendered the options line into a
250,446-char prompt — verified present, after the instruction block — and the brief
referenced none of it. n=1, so it could have been one anomalous generation.

## Design

Three arms over NVDA / CRM / ORCL, all carrying a near-term print so all three clauses
(implied-vs-realized vol, implied earnings move, 25-delta skew) render. ORCL and NVDA are
gated (`negative_fcf`, `heavy_insider_selling`) and were run with `require_passed=False`,
exactly as the bot's `/deep` does (`telegram.py:335`).

| arm | prompt |
|---|---|
| A_off | `research.options.enabled: false` — no line (baseline / false-positive control) |
| B_shipped | the wording #194 merged, no destination |
| C_destination | same line + `OPTIONS_SYSTEM_ADDENDUM`, REQUIRED, naming `thesis` |

**Controlled, not nine samples.** Per ticker: **one** context digest across all three arms
(byte-identical filing inputs and card facts) and **three** distinct prompt fingerprints
(the only thing that varied).

| ticker | context digest | distinct prompt fingerprints |
|---|---|---|
| NVDA | `a6dfc92b` | 3 |
| CRM | `aa363e74` | 3 |
| ORCL | `676a55e0` | 3 |

## Result

| ticker | A_off | B_shipped | C_destination |
|---|---|---|---|
| NVDA | — | not used | **used** |
| CRM | — | not used | **used** |
| ORCL | — | not used | **used** |
| | control | **0 of 3** | **3 of 3** |

Decision rule, fixed in advance: *B ≤ 1 and C ≥ 2 → ship arm C's clause.* Satisfied.

### The baseline arm earned its place

Arm A was included as a false-positive control and immediately justified itself. A keyword
locator flags `implied` and `market prices`, and **both fire in briefs with no options line
at all** — they are `reverse_dcf`'s "price-implied perpetual FCF growth". NVDA's arm-A brief
carried one such hit and CRM's arm-B brief carried two, none of them options-related.

The same applies to figures: `6.1` appears in NVDA's arm-A *and* arm-B briefs, so it cannot
be evidence of the implied move being read. **A number or phrase present in the baseline is
disqualified as evidence by construction.** Every `option` string in arms A and B traced to
filing text ("open-source", "resiliency options").

This is why the pre-registration named three specific quantities — the implied move against
realized prints, the implied/realized ratio, the skew — rather than "mentions the data".

### What arm C actually produced

All three landed in `thesis`, the named field, and all three are interpretive rather than
number dumps — which the clause demanded ("Do not restate the numbers — say what they imply").

- **CRM** — the comparison the feature exists for: *"an imminent, unusually large-implied-move
  earnings print (options price ±7.8% vs. a ~3.5% average realized move over the last six
  quarters) mean the market is bracing for more uncertainty than the filing's steady operating
  narrative alone would suggest."*
- **ORCL** — all three clauses, and a genuine disagreement with the filing read: *"options
  pricing (~16.4% implied move, in line with the stock's own recent realized swings, with a
  call-favoring skew) suggests the market is leaning into the growth narrative rather than
  pricing tail risk from the leverage build."*
- **NVDA** — *"the options market pricing a larger-than-typical, modestly put-skewed move into
  an imminent print signals the market shares some of that two-sided uncertainty rather than
  treating the setup as a one-way bet"*, plus a falsifier keyed on it.

## Diagnosis

Identical to the arithmetic clause's, and the audit's own words apply unchanged:

> The clause is conditional and unlocated … never says when the computation is *required* or
> which output field should carry it. Every other instruction in `SYSTEM_PROMPT` is attached
> to a named field.

The options line had **no** instruction at all — it was context with nowhere to land. The
contrast inside the same prompt is instructive: `PROXY_SYSTEM_ADDENDUM` names its destination
("fold anything decision-relevant into your reconciliation, using the signal token
`governance`") and the proxy line does get used. The data was never the problem.

## What ships

`research.options.require_in_thesis: true`. `OPTIONS_SYSTEM_ADDENDUM` is keyed on the
**surface**, not the config, matching `EIGHTK_SYSTEM_ADDENDUM`: a name whose options line
abstained must not be told to account for a line that is not there.

The clause copies the arithmetic clause's blunt "REQUIRED … no exceptions" phrasing
deliberately. The register warns that that bluntness is what produced its 3/3 and that a
softened version could revert it; the same caution applies here. **Do not soften this wording
without re-running this A/B.**

## Limitations — stated in the pre-registration, unchanged by the result

- **n=3 tickers, one run per cell.** This establishes that the clause works, not how
  reliably. 0-of-3 against 3-of-3 is lopsided enough to act on; it is not a rate.
- **All three carry a near-term print**, so all three clauses rendered. A name whose implied-move
  clause abstains (most small caps, and any print more than ~6 weeks out) was not tested, and
  the addendum then instructs against a thinner line than any measured here.
- **Arm C's wording is one draft.** It passing does not mean it is optimal.
- No measurement of whether the model's *interpretation* of the options data is correct — only
  that it uses it. A wrong-but-confident reading would score as a use here.

## Follow-up

Re-count on names where the implied-move clause abstains, to confirm the addendum degrades
gracefully rather than inviting the model to reason about a clause that is not there. Cheap
to fold into the next accumulation of briefs rather than run standalone.
