# `/deep` prompt: materiality bar, closed `red_flags`, no quote reuse, do-the-arithmetic

**Date:** 2026-08-18
**Scope:** `research/assess.py` — `SYSTEM_PROMPT` and the trailing instruction block of
`_build_user_prompt`. No schema change, no renderer change, no back-compat surface.
**Baseline evidence:** `docs/audits/2026-08-04-deep-brief-assessment.md` (D3, D6, D7 and
`worth-building` #1, #2, #4), measured on a 35-brief corpus. This note records what shipped
and how to re-measure it; it does **not** claim a new measurement.

## What shipped

Four clauses, all prompt-only. They were queued together because they share the one cost a
prompt edit carries — `PROMPT_FINGERPRINT` hashes `assess.py`, so every cached brief is
invalidated whether one clause changes or four.

1. **Materiality bar instead of a quota (D3).** The `at most N` numbers were fighting an
   anti-padding instruction that lived in a different message, and the caps won: `risks`
   33/35 at cap, `what_would_change_my_mind` 34/35, `reconciliation` 25/35 — the last one
   *against* an explicit "this list is sparse" instruction. The prompt now states that the
   numbers are hard ceilings and never targets, names the decision test ("would this change a
   buy/sell decision"), and extends the bar explicitly to `reconciliation` and
   `what_would_change_my_mind`. The user-prompt line keeps its `Return at most …` opening
   (two tests anchor the section/instruction boundary on it) and gains the same framing.

   **The justification is false precision and attention dilution, not filler.** §3 of the
   baseline audit measured the tail as company-specific — ranks 7–12 are 56% specific vs 65%
   for ranks 1–6. Do not re-justify this as "removing boilerplate"; that reading was measured
   and rejected.

2. **Closed `red_flags` enumeration (D7).** Only 24% of 214 red flags matched any enumerated
   category, because the definition read as a definition followed by exemplars. The list is
   now stated as CLOSED, with the residual explicitly routed to `risks` or the bear case.

3. **No cross-section quote reuse (D6).** 31 of 35 briefs reused a ≥40-char quote across
   sections (62 instances); the dominant pattern was `reconciliation` + `red_flags`, and the
   rendered brief gave a reader no way to see that three bullets were one fact. One quote may
   now support one item across `risks`, `red_flags`, `added_risks` and `reconciliation`.

4. **Do the arithmetic (`worth-building` #4).** The baseline audit calls this the single most
   consistent qualitative gap across three close reads: the briefs assemble the inputs and
   stop. The prompt now asks for normalized earnings ex-one-offs, cash runway, and refinancing
   coverage, with the inputs shown beside the result.

## The constraint that makes #4 safe, and must survive any edit

A derived figure is **not a filing fact**. The prompt therefore confines it to `claim`,
`tension` and thesis fields and forbids it inside `evidence`/`filing_says`. Without that
clause the instruction would be an invitation to compute a number and quote it back through
`_verify_grounding`, which only checks that a quote is a contiguous substring of one segment —
it cannot tell a computed number from a disclosed one.

It is also deliberately **not** routed to `management_capital_allocation`. That field became
judgment-only on 2026-08-17 (`docs/audits/2026-08-17-moat-management-evidence-design.md`)
precisely to get 14–27 bare numbers per paragraph out of it; sending arithmetic there would
undo that cut three days later.

## One downstream interaction, deliberate

`assess.py:_high_corroborated` lets a **bearish** HIGH-conviction call be corroborated by
`contradicts OR red` — where `red` is "any verified red flag". Closing the `red_flags`
enumeration narrows that route: a generic bearish observation no longer lands in `red_flags`,
so an AVOID must now rest on a genuine distress marker or on a verified `contradicts`
reconciliation entry. That is the intended reading of the guard, not a side effect to undo.
Its practical blast radius is nil today — the baseline audit measured the HIGH-corroboration
demotion firing **0 of 32** — but a future session comparing guard-fire rates should attribute
any change here rather than to the guard code, which is untouched.

## How to re-measure

The baseline is reproducible from the brief corpus, not from a fixture: for each section,
count items and compare against the cap.

- **D3:** the distribution should stop being a spike at the cap. The pre-change reference is
  `risks` {8:1, 11:1, 12:33}, `what_would_change_my_mind` {3:1, 6:34}, `reconciliation`
  {3:1, 4:3, 5:6, 6:25}. `red_flags` (0/35 at cap) and `added_risks` (0/35) are the controls —
  they were never saturating, so a large move there is a signal that the bar over-applied.
- **D7:** re-run the punctuation-insensitive keyword match of each `red_flags` claim/evidence
  against the seven categories. Baseline 24% of 214.
- **D6:** count ≥40-char evidence quotes appearing in more than one section per brief.
  Baseline 62 instances across 31/35 briefs.

**Watch for over-application.** `config.yaml` records that Sonnet 5 follows instructions more
literally than the generation the prompt was tuned against. The failure mode to look for is a
brief that returns two risks on a filer that genuinely discloses ten, or a `red_flags` array
that is empty because a real covenant/liquidity item was routed to `risks`. If that appears,
the fix is to soften the bar, not to restore the quota.

## Pinned by

`tests/research/test_assess.py::test_system_prompt_carries_the_three_2026_08_04_audit_clauses`
and `::test_user_prompt_frames_the_caps_as_ceilings`. Prompt-only changes have no other
observable surface, so the text itself is the only thing a test can hold.
