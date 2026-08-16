# 10-Q MD&A recovery heuristic — KILLED on evidence (2026-08-14)

**Verdict: do NOT recover a missing 10-Q MD&A by slicing the containing Part I Item 1 at an
MD&A heading.** The rule works on 2 of 6 measured failures, produces *table-of-contents junk*
on 1, and has no input at all on 3. Ship honest degradation instead.

This is a KILL, not a deferral. Do not re-propose the heuristic without a counter-argument to
the BLK case below.

## Why the question came up

`_tenq_mda` extracts a 10-Q's MD&A via `get_item_with_part("Part I", "Item 2")`. On INTC that
returns **0 chars** — edgartools fails to detect the Item 2 heading, so the preceding Part I
Item 1 span (135,783 chars) absorbs the MD&A, and the brief silently carries no quarterly MD&A.
INTC's real MD&A *is* sitting inside that blob at offset 88,236, which makes "slice it back out
at the heading" look obvious and cheap.

**It was fitted to n=1.** The only reason it looked like a rule is that INTC was the sole
failure in a 35-name sample.

## Probe

Both committed universes — `universe_largecap.txt` + `universe_smallmid.txt`, 238 unique
tickers (the files overlap by 35) — latest 10-Q each. Reproduce with
`docs/audits/scripts/probe_10q_mda_extraction.py` (keyless beyond `SEC_IDENTITY`; resumable, ~25 min).

| | count | rate |
|---|---:|---:|
| usable | 228 of 238 | — |
| **MD&A extracted as 0 chars** | **5** | **2.19%** |
| span >= 50% of whole document (over-capture) | 11 | 4.82% |

Zero-char by size: 2 of 78 large, 3 of 150 small/mid — it is not a small-cap artefact.

### The 0.50 over-capture threshold is empirically clean
Largest non-over-capture fraction **0.480**; smallest over-capture **0.502**; median 0.253,
p90 0.394. The threshold shipped in `_note_over_capture` sits in a real gap, not on a guess.
Over-capturing names: HON 0.896, JPM 0.846, AXP 0.647, MCD 0.644, CVBF 0.596, FFIN 0.588,
PFE 0.566, INDB 0.547, SCL 0.535, SAFT 0.517, PM 0.502.

## The decisive result: four shapes, one rule cannot cover them

For every zero-char failure we recorded the containing Part I Item 1 blob, every
`management's discussion and analysis` hit in it, and the context around each hit.

| ticker | containing Item 1 | heading hits | what recovery would do |
|---|---:|---|---|
| INTC | 135,783 | 2; real heading is the **last** (0.65) | ✅ recovers ~47K of real MD&A |
| MTRN | 167,227 | 1 genuine heading (0.657) | ✅ recovers ~57K |
| **BLK** | **1,455** | 1 — a **table-of-contents row** | ❌ **slices 318 chars of TOC** |
| C | **0** | none | ❌ no input |
| MHO | **0** | none | ❌ no input |
| MED | **0** | none | ❌ no input |

### BLK is why this is a KILL
BLK's containing Item 1 is 1,455 chars — itself a fragment — and its single heading "hit" is a
row of the filing's own table of contents:

```
| | | 17 | Item 2. | Management's Discussion and Analysis of Financial Condition and
Results of Operations | 37 | | 19 | Item 3. | Quantitative and Qualitative Discl...
```

"Prefer the last heading match" — the rule INTC suggests, because INTC's first hit is a
glossary entry 1,151 chars before the real heading — would slice the 318 chars after that TOC
row and hand them to the model **as MD&A**.

That text enters `FilingBundle.segments()` as filing text, so `_verify_grounding` would mark a
quote from it **verified against a real filing segment**. The failure mode is not "we recovered
nothing"; it is a fabricated finding carrying a verified mark. The current behaviour — an empty
section — is strictly safer, and BLK is a large cap, not an exotic filer.

For C, MHO and MED the question does not even arise: there is no containing blob to slice.

## What ships instead

Honest degradation, no heuristic:

1. **Already shipped** (`86066ef`): `_tenq_mda` announces the 0-char gap and the over-capture on
   stderr instead of returning `""` silently, and both measured non-fixes are pinned as tests —
   `tenq["Item 2"]` (returns *Part II* Item 2 on INTC: share repurchases, wrong content) and
   `tenq.items` membership (XOM/TSLA/MCD list misleading entries yet extract fine).
2. **This change**: when the 10-Q MD&A is unavailable, the brief says so rather than silently
   omitting the section, so the model and the reader can tell "we could not read it" from
   "nothing happened". Prompt-only — a computed status line, never the grounding haystack.

## The 8 `CompanyNotFoundError` tickers — RESOLVED 2026-08-15: our universe files are stale

10 of 238 tickers errored, 8 of them `CompanyNotFoundError` from `Company(ticker)`. **Not**
counted as extraction failures anywhere above.

The throttling hypothesis in the first revision of this section was **wrong**. Re-resolved one at
a time with pauses: all 8 fail deterministically across repeated attempts while AAPL/MSFT resolve
in the same loop. It is not transient, and it is not an edgartools bug — all 8 are genuinely
absent from SEC's own `company_tickers.json` / `company_tickers_exchange.json`, which edgartools
faithfully reflects. **The stale data is ours**: `universe_largecap.txt` / `universe_smallmid.txt`
carry symbols the issuers no longer use.

| ticker in our file | what actually happened | current symbol |
|---|---|---|
| MMC | renamed (CIK 62709 unchanged) | **MRSH** |
| CSWI | renamed (CIK 1624794) | **CSW** |
| UCBI | renamed (CIK 857855) | **UCB** |
| LANC | issuer renamed Lancaster Colony -> Marzetti Co (CIK 57515) | **MZTI** |
| AMED | stopped filing; last 10-Q 2025-07-30 | delisted/acquired |
| CIVI | stopped filing; last 10-Q 2025-11-06 | delisted/acquired |
| SCS, TOWN | absent from SEC's current listed map; **not individually confirmed** | unknown |

### Production consequence (verified, not inferred)

`EdgarSource.fetch("MMC")` returns 4 recorded errors with `statements=None` and `insider=None`,
while `fetch("MRSH")` returns full statements and insider data from the same CIK. So the failure
is **honest** — it surfaces in `errors`/coverage rather than silently — but EDGAR supplies 100%
of production statements, so a renamed ticker loses its entire fundamental picture. The
user-facing message is also actively unhelpful: edgartools suggests `'MMCP' (Mag Mile Capital)`
for MMC.

### A worse defect found while fixing this: ticker `B` was REASSIGNED

Re-checking the 2 non-`CompanyNotFoundError` rows turned up the dangerous shape. `B` **resolves
fine** — to the wrong company. Barnes Group (CIK 9984) stopped filing 2024-10-29 and holds no
ticker; the symbol now belongs to **BARRICK MINING CORP** (CIK 756894), a Canadian 6-K filer.

A not-found error is loud. A reassignment is **silent**: the backtest would have scored Barrick's
data in a slot meant for a US industrial. This one was caught only because Barrick files no 10-Q,
so it showed up as "no 10-Q" in the probe.

**A resolution check cannot find reassignments that land on a valid 10-Q filer.** Only pinning
CIKs would — the universes key on tickers, which are not stable identifiers. This is the same
landmine `CLAUDE.md` records for the retired scout (`Company("BBBY")` today resolves to
*Overstock*), now confirmed live inside a committed universe file. Converting the universes to
ticker+CIK pairs is the real fix and is **not** done here.

(The other row, MO, resolves fine with a current 10-Q — its probe `AttributeError` was transient.)

### Consequences worth acting on, NOT yet acted on

1. **The committed universes silently drop 8 of 238 names** (3.4%). That is a breadth loss against
   the ~30-name cross-sectional IC floor, and it grows over time. Editing the files is *not* a
   free fix: they are reproducibility artifacts for committed cross-universe verdicts, so any
   correction changes what a re-run measures and must be recorded, not slipped in.
2. **A clearer not-found message for `/screen` and `/deep`** — "not in SEC's current ticker map;
   it may have been renamed or delisted" beats a nonsense nearest-neighbour suggestion.
3. Automatic old->new resolution needs a historical map (`edgar/symbology.py`'s Wayback +
   `dei:TradingSymbol` route, currently uncalled). Real work; not obviously worth it for
   user-typed tickers.
