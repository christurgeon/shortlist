# The arithmetic clause: measured at 1/4, fixed, re-measured at 3/3

**Date:** 2026-08-22
**Scope:** closes the three items `TODO.md` §2a carried from the 2026-08-18 prompt work —
the untested arithmetic clause, `what_would_change_my_mind` saturation, and residual quote
reuse — plus the window-labelling defect
`docs/audits/2026-08-20-debt-liquidity-notes-design.md` §7.2 recorded and nobody actioned.
**Verdict: the clause was BROKEN and is now FIXED.** Before/after on the same three filings.

## Why this run, and why these names

The 2026-08-19 note measured the arithmetic clause at 0/3 and correctly declined to call
that a failure: two of its three asks were *unanswerable* from what the prompt carried.
Cash landed as a rendered column on 2026-08-19, the maturity ladder as a statement note on
2026-08-20. `TODO.md` therefore said "nothing blocks it but a run".

That was half right. The 08-20 note had already run **UAL** with a ladder and seen the
clause fire once — so the real question was not "does it work" but "how often", and the
honest baseline was **1 of 1**, not 0 of 3.

Three fresh names, none in the brief corpus, each chosen so refinancing coverage is a live
question rather than a formality:

| ticker | why | gates |
|---|---|---|
| CCL | post-deleveraging cruise operator; export-credit ladder + large undrawn facilities | none (passed) |
| BA | negative FCF, $54B debt, interest coverage 1.54x | `negative_fcf`, `over_leveraged` |
| LUMN | leveraged telecom turnaround; divestiture-funded deleveraging | `over_leveraged` |

BA and LUMN are gated, so both went through the operator `/deep` path
(`research.enrich(..., require_passed=False, top_n=1)`) — the same library call
`bot/telegram.py` makes for a named ticker. No source was edited to reach them.

All six runs (three before, three after) logged `attempt 1/3 outcome=ok stop=end_turn`.

| ticker | before | after | context digest |
|---|---|---|---|
| CCL | 172s, $0.4357 | 240s, $0.5025 | `c39361d75` (both) |
| BA | 266s, $0.5371 | 230s, $0.2500 | `cc68f9a4d` (both) |
| LUMN | 187s, $0.5343 | 244s, $0.5953 | `c8300fe39` (both) |

**The context digest is identical within each pair** — `p00cf85ac` → `pb50bf89e` is a
prompt-only change against byte-identical filing inputs. This is a controlled A/B, not two
samples.

## Before: the clause failed 2 of 3, and its fallback failed the third

First the necessary check, because "the model declined" and "the model had nothing to work
with" are different findings and only one is a defect. Segments actually built, per ticker:

| ticker | debt notes in the haystack | 12-month ladder present? |
|---|---|---|
| CCL | `10-K note: Debt` (11,298), `10-Q note: Debt` (5,685) | **yes** — 2026–2030 rows, plus $10.8B undrawn export facilities |
| LUMN | `10-K note: Long-Term Debt and Credit Facilities` (15,929), `10-Q note:` … (7,952) | **yes** — `2026 (remaining six months) $46 … total $13,350` |
| BA | `10-K note: Debt` (5,069), `10-Q note: Debt` (775) | **no** — see below |

**CCL and LUMN are unambiguous misses.** Both had the ladder, both had cash and OCF in the
rendered series, both had undrawn capacity quoted in the note, and neither brief computed
anything. LUMN is the sharpest case: refinancing is the entire thesis, the 10-Q ladder shows
$46M due in the remaining six months against $1.9B of cash, and the brief said only that
leverage "remains high with negative interest coverage".

**BA is a data gap, and it exposed a second defect.** Selection was correct — `Debt` is the
only note in BA's 28-note index matching the title rule, it was picked from both forms, and
neither was truncated (5,069 and 775 against a 16,000 cap). Boeing simply **does not put a
principal-maturity schedule in that note**: it carries a *finance-lease* ladder (2026 $124 …
total $250) and `$10,000 available under credit line agreements`, while the $15.5B
three-year maturity figure lives in the MD&A liquidity discussion. So the extractor behaved
correctly and the input was genuinely absent — but the clause's own fallback ("If an input
is not disclosed, name the missing input rather than estimating it") **also did not fire**.
The brief restated `$54.1B of debt with $15.5B of principal due within three years`, which
is precisely the "restating the raw lines" the clause forbids, and named nothing as missing.

**Baseline, stated honestly: 1 of 4** — UAL (2026-08-20) fired; CCL, LUMN and BA did not.

### Diagnosis

The clause is conditional and unlocated. It opens "Where the filing and the QUANT CONTEXT
give you the inputs" and never says when the computation is *required* or which output field
should carry it. Every other instruction in `SYSTEM_PROMPT` is attached to a named field;
this one floats, and more than half its text is prohibition (where a derived figure may not
go) rather than production. A model filling a schema has no slot that comes back empty when
it skips this.

## The change

Two edits to `SYSTEM_PROMPT` (`research/assess.py`). Every existing safety prohibition is
byte-identical; only the production half moved.

1. **The arithmetic ask got a trigger and a destination.** Whenever a `STATEMENT NOTE`
   section shows a maturity schedule the brief MUST state refinancing coverage, with the
   leverage reconciliation `tension` named as the usual place and a `red_flags` claim as the
   alternative when coverage is thin. "A ladder shown and no coverage stated is a defect."
2. **The window rule** — the open item from the 08-20 note. UAL had summed the remaining
   2026 column *and all of 2027* and called it "due within 12 months", an 18-month window.
   The clause now says to use the columns falling inside twelve months and to NAME the window
   used. The fallback also gained a worked example: *"the debt note discloses no maturity
   schedule" is a useful sentence, silence is not.*

A third edit, same commit, addresses the separate falsifier finding below.

## After: 3 of 3, with the window named every time

| ticker | computed refinancing coverage | window named |
|---|---|---|
| CCL | `$745M of principal due in the remainder of 2026 against $2.2B cash plus $4.5B of revolver capacity — roughly 9x near-term coverage` | "the remainder of 2026" |
| BA | `$7.2B cash + $12.8B short-term investments + $10.0B undrawn revolver = $30.0B available liquidity versus only $4.6B of debt classified as short-term at June 30, 2026 — roughly 6.5x` | "at June 30, 2026" |
| LUMN | `$88M … due within the next 12 months against $1.0B cash + $722M revolver availability (~19.6x)`, and `just $46M due in the remaining six months of 2026 against $1.9B cash + $660M revolver (~44x)` | both, explicitly |

Three points worth recording beyond the count:

- **BA is the interesting one.** It still has no ladder in its note, and rather than restating
  the three-year figure the model reached for `debt classified as short-term` on the balance
  sheet — a legitimate twelve-month proxy — and said which date it applied to. The fallback
  language did its job without the brief having to abstain.
- **LUMN computed the ladder twice**, from the 10-K and the 10-Q, and reported both windows
  with their different answers rather than blending them. That is the behaviour the window
  rule was written to get.
- **The safety constraint held.** Scanned every `evidence` / `filing_says` field in all three
  after-briefs for a derived figure (`= Nx`, `roughly Nx coverage`, `~Nx`): **zero hits.**
  All the arithmetic sits in `tension`, `claim` or thesis fields, which is `SYSTEM_PROMPT`'s
  rule for a computed figure. The one thing that could have made this change dangerous —
  pushing computed numbers into quote-verified fields — did not happen.

## `what_would_change_my_mind`: saturation confirmed, then fixed

The 08-19 note left this open at 2 of 3 at the cap of 6 and called n=3 too small.

**Before: 6, 6, 6 — all three at cap.** Combined with the earlier sample that is **5 of 6**
post-prompt briefs pinned to the ceiling. Reading the lists, the tail is where it shows: CCL
#6 was "Clarity/normalization on the unusual CEO pay structure disclosed in the proxy" and
LUMN #5 "CEO pay realigning with TSR performance in the next proxy cycle" — monitorables, not
things that would flip a thesis.

The cause is the same shape as the arithmetic defect: the generic "HARD CEILINGS, not
targets" line covers the list, but the THESIS clause itself carried no materiality standard,
and unlike `risks` a falsifier costs nothing to produce because it needs no quote. So the cap
became the target.

The clause now defines a falsifier as an observable event that would flip the bull or bear
case just stated, excludes monitorables explicitly, and says "three sharp falsifiers are a
better answer than six soft ones; its ceiling is not a target."

**After: 4, 4, 4.** Off the cap on all three, and the content moved with the count — CCL's
list now ends on *"A covenant amendment, drop below the $1.5B minimum liquidity covenant, or
missed near-term debt maturity would flip the currently strong refinancing coverage finding
into genuine liquidity stress"*, which is a falsifier tied to the brief's own new finding.

## D6 (quote reuse): unchanged at ~1 per 3 briefs

Cross-section exact/substring duplicate scan over `risks`, `red_flags`, `added_risks` and
`reconciliation`, quotes ≥40 chars, before-run briefs:

| ticker | quotes | reuse |
|---|---|---|
| CCL | 16 | 0 |
| BA | 21 | 0 |
| LUMN | 19 | **1** (`red_flags#0` ↔ `reconciliation#2`) |

1 in 3, the same rate the 08-19 note measured (AAPL). Across both samples that is **2
violations in 6 post-prompt briefs**, against 62 across 31 of 35 pre-change. The verdict is
unchanged and should stay as recorded: **reduced by roughly an order of magnitude, not
eliminated.** No further action — the remaining rate does not justify another prompt clause,
and this note deliberately did not add one, so the arithmetic and falsifier results above are
not confounded by a third change.

## The over-application control: AAPL, and one measured side effect (2026-08-23)

The three names above were chosen because refinancing is a live question for them, which
makes them a strong test of the clause and no test at all of whether it stays quiet when it
should. AAPL is the control: net-cash, a debt note with a ladder, and no refinancing question
any reader would ask.

**The clause fired there too.** From `reconciliation[risk]`:

> $132.4B cash & marketable securities plus $111.5B FY2025 operating cash flow (~$243.9B)
> versus $20.4B of debt due within 12 months (the $12.4B of Notes maturing in fiscal 2026
> plus $8.0B of commercial paper) implies roughly 12x…

The computation is correct, well-sourced (it decomposes the $20.4B into notes plus commercial
paper rather than quoting a single line) and windowed. It is also **not information anybody
needed**: 12x coverage at Apple is a foregone conclusion.

**The cost is a slot, and it is real.** `reconciliation` is specified as sparse — "emit an
entry ONLY where a number and the filing genuinely diverge or strongly corroborate" — and
capped at 6. AAPL's 08-19 brief used 5 of 6; this one uses **6 of 6**, with the required
coverage entry among them. So the requirement did not merely add a sentence, it pushed the
list to its ceiling and may have displaced a better entry. The other five (value, growth,
governance, quality, narrative_tone) are all substantive, so nothing visibly bad was crowded
out — but this is n=1 and the mechanism is clear.

This is the **over-application** failure mode the 08-19 note went hunting for, and it is now
present in a mild form: the clause fires universally rather than selectively. It is a
tradeoff, not a defect — a correct low-value entry is much cheaper than the silent failure it
replaced — but it should be a deliberate choice, not a surprise. The obvious refinement is to
keep the computation REQUIRED while saying that comfortably-covered names need only a clause
in an existing entry or the thesis, not a dedicated reconciliation row. **Deliberately not
made here:** it would be a third prompt edit validated by nothing, on a sample of four, with a
real risk of reverting the 3/3 result this note exists to record.

Everything else on AAPL is clean: `unverified_count` 0, falsifiers 4 (down from 6 at cap on
08-19) and all four tied to the brief's own findings, zero derived figures in quote-verified
fields.

## D7 (closed `red_flags` categories): re-certified at 12/12

The 08-19 note left D7 at 2/2 and called n=2 far too small to replace the 24%-of-214
baseline. All six red flags in the before-briefs and all six in the after-briefs map to a
category in the closed enumeration:

| ticker | before | after |
|---|---|---|
| CCL | heavy dilution | heavy dilution |
| BA | liquidity stress / heavy dilution / material litigation | heavy dilution / material litigation / liquidity stress |
| LUMN | covenant breach / material litigation | covenant breach / material litigation |

**12 of 12**, or 14 of 14 counting the 08-19 sample. Not one general bearish consideration
leaked in — no valuation, demand or competitive worry appeared in either list, which is the
specific failure the closed enumeration exists to prevent. Incidentally the after-briefs began
*naming* the category inline ("Heavy dilution: …", "Material litigation/settlement exposure:
…"); nothing in the change asked for that.

## Quote verification: two false negatives, measured and NOT fixed

The before-run CCL brief reported `unverified_count: 2`. Both are **false negatives** — the
filing text is present and the model's transcription is what a careful human would write:

| quote | longest prefix matched | why it failed |
|---|---|---|
| `…record net yields in the second half of 2026.` | **171 of 172** | the filing is dialogue: `…second half of 2026," Weinstein said.` The model closed the sentence with a period where the filing has a comma inside a closing quote. |
| `We achieved a net debt to adjusted EBITDA ratio of 3.1x—…` | 42 of 114 | the filing reads `net debt to adjusted EBITDA 1 ratio of 3.1x-…` — a superscript **footnote marker bled inline** by the HTML-to-text extraction. |

The second is the class `assess.py`'s `_FOLD` comment already scopes out and
`2026-08-04-deep-brief-assessment.md` D1 already recorded: extraction artifacts, not
fabrication. The first is narrower and *would* be safely recoverable — trimming trailing
terminal punctuation from the needle only weakens the substring requirement and cannot bridge
two non-adjacent spans, so it cannot manufacture a match.

**Deliberately not done.** `_FOLD` was adopted on a measured 73% recovery rate; this is n=1.
Changing the fabrication guard on a single observation is the move `CLAUDE.md`'s 2026-07-26
postmortem exists to prevent. Recorded here so a later session with a recurrence has the rule
ready rather than re-deriving it.

**The guard is not over-firing.** The after-run BA brief reports `unverified_count: 2` and
both are **genuine stitches** — 196 of 260 chars match before diverging, and 111 of 497 —
the model joining non-adjacent sentences, which is exactly what per-segment substring
matching exists to reject. Note the counts moved in opposite directions across the pair
(CCL 2→0, BA 0→2): that is run-to-run variance in the model's quoting, **not** an effect of
this change, and neither direction should be read as one.

## Also measured: the debt note does not always carry the ladder

BA is the first observed filer whose selected debt note contains **no principal-maturity
schedule**. The 20-filing probe behind `2026-08-20-debt-liquidity-notes-design.md` measured
*selection* (does a debt-titled note exist and get picked) and did not measure *contents*
(does the picked note contain a ladder). Selection is 1/1 correct on BA; contents are absent.
1 of 3 here, so the rate is unknown and the sample is far too small to put a number on.
`docs/RESEARCH.md` now says so. The practical consequence is already handled: the fallback
language makes the model reach for balance-sheet short-term debt instead, which is what BA's
after-brief did.

## Verdict

1. **The arithmetic clause was broken and is now fixed** — 1 of 4 → 3 of 3, on a controlled
   prompt-only A/B against identical filing inputs, with shown working in every case.
2. **The 12-vs-18-month window defect from 2026-08-20 is closed** — all three after-briefs
   name the window they used.
3. **`what_would_change_my_mind` saturation is fixed** — 6/6/6 → 4/4/4, with the padding
   visibly gone from the tail.
4. **D6 reuse is unchanged and stays as recorded** — ~1 per 3 briefs, no action taken.
5. **The safety invariant held** — zero derived figures in any quote-verified field.

6. **The clause fires universally, not selectively** — the AAPL control computed coverage it
   did not need and pushed `reconciliation` to its cap. Accepted as a tradeoff, recorded
   above, not refined.

**Limits.** n=3 leveraged names plus one cash-rich control, **one run per side**. The
population was chosen as the one the clause is *for*, so this is a strong test of the clause
and a weak sample of briefs in general. Run-to-run variance is real and was observed in this
very session — `unverified_count` moved 2→0 on CCL and 0→2 on BA across identical inputs — so
a 0/3 → 3/3 swing is large enough to be credible as an effect while the point estimate is not
reliable. `what_would_change_my_mind` landing on exactly 4 in all four briefs is a small
sample sitting suspiciously flat: watch whether it is drifting toward a new implicit target
rather than tracking materiality. Nothing here is pinned by a test — prompt clauses in this
repo are guarded by live runs and this note, not by unit tests, so this document is the only
thing standing between the clause and a silent regression.
