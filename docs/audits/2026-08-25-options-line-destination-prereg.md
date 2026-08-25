# PRE-REGISTRATION — does the options line earn its slot in the brief? (2026-08-25)

**Written and committed BEFORE any result was seen.** Pre-registering is the rule this repo
adopted after the 2026-07-26 postmortem retracted four conclusions built from data.

## The observation that prompted it

`/deep` on NVDA (2026-08-24, live, `outcome=ok`, 189s, $0.4531) rendered the options line
into a 250,446-char prompt — verified present, after the instruction block — and the brief
**did not reference it at all**: zero mentions of options, straddle, skew or implied
volatility across every output field. n=1.

## The hypothesis

The line has **no destination field**. This is the defect
`2026-08-22-deep-arithmetic-clause-verification.md` already diagnosed once, in its own words:

> The clause is conditional and unlocated. It opens "Where the filing and the QUANT CONTEXT
> give you the inputs" and never says when the computation is *required* or which output
> field should carry it. Every other instruction in `SYSTEM_PROMPT` is attached to a named
> field.

That fix took refinancing coverage from 1 of 4 to 3 of 3. The options line has the same
shape: ambient context, no named field, no trigger.

## Design — three arms, byte-identical filing inputs

| arm | prompt |
|---|---|
| **A** | `research.options.enabled: false` — no line at all (baseline) |
| **B** | shipped wording, no destination (what #194 merged) |
| **C** | shipped wording + a REQUIRED destination clause naming `thesis` |

Tickers: **NVDA, CRM, ORCL** — all carry a near-term print, so all three clauses (implied
vs realized vol, implied earnings move, skew) render. ORCL is gated (`negative_fcf`) and is
run with `require_passed=False`, exactly as the bot's `/deep` does (`telegram.py:335`).

Controlled, not two samples: the **context digest must be identical across arms within each
ticker**. Any pair where it differs is discarded — filing inputs would not be byte-identical.

## Pre-registered criterion

**Primary — "used":** a brief counts as USING the line when a named output field makes a
decision-bearing statement about what the market has priced in (the implied move against
this company's own realized prints, the implied/realized vol ratio, or the skew). Incidental
occurrences of the word "volatility" from a risk-factor quote do **not** count. Counted by
reading, keyword search only to locate candidates.

**Decision rule, fixed in advance:**

- **B ≥ 2 of 3** → the line works as ambient context. Ship as-is. No prompt change.
- **B ≤ 1 of 3 and C ≥ 2 of 3** → the defect is the missing destination. Ship arm C's clause.
- **B ≤ 1 of 3 and C ≤ 1 of 3** → the line does not earn its slot in the brief. **Disable it**
  (`enabled: false`) and record the verdict. Per `CLAUDE.md`, disabling a leg that cannot
  earn its slot is a win, not a regression. The fetchers stay (cheap, tested) but ship dark.

**Secondary, recorded but not decision-bearing:** whether any *other* output field changes
between arms; cost and duration per arm.

## Stated limitations, before the fact

- **n=3 tickers per arm.** Model sampling varies run to run, so only a lopsided result
  (0-of-3 against 3-of-3) is decisive. A 1-versus-2 split is noise at this n and will be
  reported as inconclusive, not as support.
- **One run per cell.** No within-cell repetition, so a single anomalous generation cannot be
  distinguished from an effect.
- **Arm C's wording is one draft.** A null result for C falsifies *that clause*, not every
  possible destination.
- The register warns that the arithmetic clause's blunt "REQUIRED, no exceptions" phrasing is
  what produced its 3/3 and that a softened version could revert it. Arm C copies that
  bluntness deliberately.

## Arm C — the exact clause under test

> When an `Options market` line is present, you MUST account for it in `thesis`. State
> whether the move priced into the next report is large or small RELATIVE TO what this
> company's own recent prints actually delivered, and whether the implied-vs-realized
> volatility ratio and the skew agree or disagree with the filing narrative you have just
> read. REQUIRED whenever the line is present, no exceptions. Do not restate the numbers —
> say what they imply. If the options market and your reading of the filing disagree, say so
> explicitly: that disagreement is the most decision-relevant thing on the page. These are
> market prices, NOT filing facts — never present them as filing evidence and never attach a
> filing quote to them.
