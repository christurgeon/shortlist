# Live verification: materiality bar / closed `red_flags` / no-reuse / do-the-arithmetic

**Date:** 2026-08-19
**Scope:** re-measures the four prompt-only clauses shipped 2026-08-18
(`docs/audits/2026-08-18-deep-prompt-materiality-and-arithmetic.md`) against real filings.
That note shipped the change and stated the baselines; it explicitly did not claim a new
measurement. This note is the measurement.
**n = 3** (AAPL, JPM, INTC), one fresh generation each, no repeats. Same three names as the
2026-08-18 live run in `docs/audits/2026-08-17-moat-management-evidence-design.md` — a paired
before/after, not a fresh sample, but still a small one on three large caps only.
**Verdict: HOLDS, with one confirmed miss and one clause not exercised.** See §Verdict.

## Command

```
set -a && . ./.env && set +a
uv run shortlist --tickers AAPL,JPM,INTC --research 3 --json
```

This produced AAPL and JPM briefs (both `passed`, non-gated). **INTC gates on
`negative_fcf`**, and the CLI's `--research N` only enriches `passed` cards
(`research/__init__.py:enrich`, `require_passed=True` default) — this is the same reason the
2026-08-18 run needed the bot's `/deep` path for INTC. No source was edited to get it: a
scratch script (`/tmp/.../scratchpad/intc_deep.py`) called `screen.run_harness` +
`research.enrich(..., require_passed=False, top_n=1)` directly, the same library call
`telegram.py:334` makes for an operator-named `/deep` ticker. All three runs logged
`attempt 1/3 outcome=ok stop=end_turn` in stderr — no `from_cache`, confirming
`PROMPT_FINGERPRINT` busted the cache as designed.

| ticker | duration | cost | stop_reason |
|---|---|---|---|
| AAPL | 183s | $0.5265 | end_turn |
| JPM | 187s | $0.6206 | end_turn |
| INTC | 198s | $0.4752 | end_turn |

No truncation on either stress filer. JPM's 10-Q MD&A over-capture (601,221 of 713,381 chars)
and INTC's empty 10-Q MD&A both fired as documented — pre-existing, unrelated to this change.

Fresh brief JSON records (read for every count below, not inferred):
- `research/AAPL/0000320193-25-000079+0000320193-26-000020+0000320193-26-000011+0000320193-26-000018-p74d7fc10-c19355a53-2026-08-19.json`
- `research/JPM/0001628280-26-008131+0001628280-26-054343+0000019617-26-000241+0001628280-26-048078-p74d7fc10-c7da4ef98-2026-08-19.json`
- `research/INTC/0000050863-26-000011+0000050863-26-000157+0000050863-26-000077+0000050863-26-000083+0000050863-26-000155-p39beaa39-c1b845c4d-2026-08-19.json`

## D3 — materiality bar vs. the caps

| list | cap | AAPL | JPM | INTC | at cap? |
|---|---|---|---|---|---|
| `risks` | 12 | 10 | 8 | 9 | 0/3 |
| `red_flags` | 12 | 0 | 0 | 2 | 0/3 (control, was 0/35 pre-change too) |
| `added_risks` | 8 | 2 | 0 | 3 | 0/3 (control, was 0/35 pre-change too) |
| `reconciliation` | 6 | 5 | 6 | 4 | 1/3 |
| `moat.sources` | 6 | 5 | 6 | 4 | 1/3 |
| `management_findings` | 6 | 6 | 5 | 5 | 1/3 |
| `what_would_change_my_mind` | 6 | 6 | 6 | 5 | 2/3 |

**`risks` moved off the spike.** Pre-change reference was 33/35 briefs at the cap of 12
(AAPL and JPM both 12/12 per the task's stated pairing). All three names here land clearly
under cap (8–10), and the two controls (`red_flags`, `added_risks`) stayed at their
already-not-saturating baseline — consistent with the bar working rather than a general
list-shortening side effect. `reconciliation` and `moat.sources` also moved off their
pre-change spikes (25/35 and implicitly high before). `what_would_change_my_mind` is the one
list where the effect is weak in this sample: 2 of 3 briefs still land exactly at 6, versus a
34/35-at-cap baseline — n=3 is too small to call this resolved or not.

## Over-application — did anything material get dropped?

This is the failure mode the task is hunting, and the read has to be qualitative, not just a
count. Read all three `risks` lists in full (see JSON above); none reads as truncated:

- **AAPL (10 risks):** macro demand, tariffs, memory/semiconductor shortage, supplier
  concentration, minority hardware share, developer-support knock-on, cyberattack exposure,
  talent competition, expanding regulatory regimes (antitrust/privacy/AI/trade), AI-compute
  scarcity. Broad coverage across demand, supply chain, competitive, regulatory and technical
  axes — no obvious category (e.g. litigation, currency) missing that AAPL's Item 1A typically
  headlines.
- **JPM (8 risks):** supervision/regulation, litigation/enforcement, rate and credit-spread
  volatility, credit/market concentration, consumer credit deterioration, geopolitical
  uncertainty, private-credit contagion, an **active OCC consent order** on trade-surveillance
  data completeness. This is the name the task specifically flags — "a bank with real
  credit/rate/regulatory exposure returning 3 risks is a regression" — and at 8 risks covering
  credit, rate, regulatory, litigation and a live enforcement action, it does not read as that
  failure.
- **INTC (9 risks + 2 red flags):** share loss in x86, missed AI-GPU transition, unproven
  foundry strategy, fixed-cost operating leverage, an uninsured Israeli fab, Taiwan supplier
  dependency, export-control exposure, government-stake dilution/voting effects, TSMC
  dependency beyond 18A. Plus red flags for the dilutive government warrant and delayed/
  cancelled capacity projects. This is the distressed name in the set and it reads as
  appropriately alarming, not softened.

**No genuinely material risk category was observed missing** in this n=3 read. That is a
judgment call from reading the three lists against general knowledge of each filer, not a
line-by-line diff against the raw Item 1A text — a stronger check would re-run the pre-change
prompt on the same three filings and diff the two risk sets directly, which this task's budget
did not include.

## D7 — closed `red_flags` category match

Only INTC produced red flags in this sample (AAPL and JPM both returned empty arrays).

| ticker | red flag | matched category |
|---|---|---|
| INTC | "U.S. government equity transaction is dilutive, and an outstanding warrant could trigger further significant dilution" | heavy dilution |
| INTC | "Financial constraints have already forced Intel to delay or cancel multiple manufacturing facility projects, evidencing liquidity/capital-allocation stress" | covenant breach or liquidity stress |

**2/2 (100%) matched a closed category**, against a 24%-of-214 baseline. n=2 is far too small
to replace the baseline measurement, but the direction is right and the two hits are clean,
not borderline.

**JPM's empty list is a judgment call, checked, not a rubber stamp.** JPM's own text surfaces
two candidate distress signals — the active OCC consent order and a "shrinking CET1 buffer" —
neither routed to `red_flags`. Read against the closed enumeration: a consent order is
regulatory/administrative, not the enumerated "material litigation" (a court proceeding) or
"material weakness" (an internal-controls-over-financial-reporting term of art), and JPM's
CET1 is thinning from an intentional buyback/dividend program at a still-strong level, not a
covenant breach or liquidity stress at one of the best-capitalized US banks. Both landed
correctly per the design intent — one in `risks` (the consent order), one in
`management_capital_allocation` prose and the reconciliation `value` entry (CET1) — rather
than an empty list from suppression. This is a boundary case that a domain expert could argue
either way on the consent order; it is not a clear miss.

## D6 — no cross-section quote reuse

Scanned every `evidence`/`filing_says` field ≥40 chars across `risks`, `red_flags`,
`added_risks`, and `reconciliation` (the four lists the rule names) for exact or
substring-contained duplicates across different sections.

| ticker | reuse instances |
|---|---|
| AAPL | **1** |
| JPM | 0 |
| INTC | 0 |

**AAPL has one confirmed violation of the stated rule.** The identical 240-char quote —
"The Company is experiencing a period of supply constraints and increasing costs for
components driven by factors such as industry supply-demand imbalances for components,
including advanced semiconductors, storage (NAND) and memory (DRAM)." — appears verbatim as
the `evidence` for a `risks` item and as the `filing_says` for the `risk`-signal
`reconciliation` entry. The prompt states "Each quote may support only ONE item across
'risks', 'red_flags', 'added_risks' and 'reconciliation'"; this is exactly that pattern
(reconciliation reusing a risks/red_flags quote was called the dominant pre-change pattern).
**1 instance in 3 briefs is a large improvement on 62 instances across 31/35 briefs**, but the
rule did not fully eliminate reuse at n=3 — report it as reduced, not as a solved defect.

## Arithmetic ("do the arithmetic")

Grepped every brief (markdown and JSON) for computation vocabulary the prompt names — cash
runway, refinancing coverage, "months of cash/liquidity", "÷"/"divided by" — and for shown
working (`$X + $Y vs $Z = N.Nx` style).

**No brief computed cash runway or refinancing coverage**, not even INTC — the one name in the
set with negative FCF, cash burn, and delayed capex, the natural candidate for a runway
calculation. JPM does show normalization language ("normaliz", "ex-Visa", "ex-one-off" — 4/2/1
hits), e.g. "normalized (ex-Visa/equity gains) profitability of ~23% ROTCE" and "the core,
ex-Markets NII line grew only 4%, closer to the multi-year trend." **Checked against the
matching `filing_says` quotes and both figures (23% ROTCE ex-Visa/equity, NII-ex-Markets +4%)
are non-GAAP measures JPM itself disclosed in its 8-K** ("net income of $16.9 billion and an
ROTCE of 23%, excluding gains related to Visa..."; "NII excluding Markets was $23.7 billion,
up 4%"), not a figure the model derived from raw inputs. So the one apparent instance of
"arithmetic" in this sample is the model correctly weighing a disclosed non-GAAP figure, not
new computation with shown work.

**Verdict on this clause: not exercised in this sample.** Absence of evidence, not evidence of
absence — n=3, and none of the three names presents an acute liquidity/refinancing question
(AAPL and JPM are both cash-rich; INTC's burn is real but the model chose to discuss it via
the disclosed non-GAAP/dilution angle instead of a runway calculation). This needs a filer
where refinancing coverage is genuinely the live question to test properly.

**The safety constraint held.** Checked every reconciliation `filing_says`/risk `evidence`
against its paired `tension`/`claim`: in every case the quote is literal filing text and the
derived comparison (e.g. "23% vs the 29% headline") lives only in `tension`/`claim`/thesis
fields, never inside a quote. No leak of a computed figure into a verified-evidence field was
found.

## Grounding did not regress (2026-08-17 cut)

| ticker | `unverified_count` | `silent_count` | `inference_count` | moat.sources (v/inf) | mgmt_findings (v/inf) |
|---|---|---|---|---|---|
| AAPL | 0 | 1 | 2 | 4v / 1 inf | 5v / 1 inf |
| JPM | 0 | 2 | 1 | 5v / 1 inf | 5v / 0 inf |
| INTC | 0 | 0 | 4 | 2v / 2 inf | 3v / 2 inf |

`unverified_count: 0` on all three — no fabrication signal fired. Every `moat.sources` and
`management_findings` entry with empty evidence is counted in `inference_count`, not
`unverified_count`, consistent with the 2026-08-17 design (declared inference ≠ fabrication).
Zero fabricated (non-empty, unverified) entries in either list on any ticker.

**`management_capital_allocation` re-scoping still holds.** Regex-scanned each prose block for
numeric tokens: AAPL 2 hits ("2025", "4" — a fiscal-year reference and a stray "4" inside
"Form 4"), JPM 1 hit ("1" — not a standalone figure), INTC 0. No dollar amount or percentage
appears in any of the three, matching the 0-numeric-token result from the 2026-08-17
verification (baseline was 14–27 numeric tokens per brief before that cut).

## Why the arithmetic clause went unexercised: it was STRUCTURAL, not sampling

The verdict below reads 0/3 arithmetic as "needs a filer with a live liquidity question".
Checked afterwards, and that is only half true — **two of the three things the clause asks for
were unanswerable from what the prompt carried**, so the model declining was the *correct*
behaviour under the clause's own "name the missing input rather than estimating it" rule:

| ask | inputs needed | in the prompt before 2026-08-19? |
|---|---|---|
| normalized earnings ex-one-offs | net income + one-off discussion | **yes** — NI in the series, one-offs in MD&A text |
| cash runway | cash & equivalents, undrawn facilities, burn | **no** — cash was not rendered; undrawn facilities live in the liquidity note |
| refinancing coverage | maturities <12mo, cash, OCF | **no** — no maturity ladder; cash not rendered |

`_render_series` printed revenue / gross profit / net income / OCF / FCF / total debt / EPS /
share count — **debt without cash**, so not even net debt was derivable. `cash_and_equivalents`
existed on `Statements` and on `StockMetrics` the whole time; `bridge._financial_series` simply
never forwarded it.

**Fixed in the same commit as this note:** cash is now a rendered column
(`bridge._financial_series` → `_render_series` → `cachekey.context_digest`, all three kept in
sync by a new mutation-tested guard, `test_render_series_columns_and_the_cache_key_digest_stay_in_sync`).
Coverage is 41 of 42 store tickers, effectively the same as `total_debt` (42/42), which was
already rendered. That makes cash runway partially computable and refinancing coverage still
blocked on the **debt-maturity ladder**, which is `TODO.md` §2b item 2 (debt & liquidity notes)
— so this live run has produced a concrete, ranked argument for that extractor rather than a
general one.

**Re-test before believing the clause works.** This note's 0/3 is now measuring a prompt that
no longer exists; the next run is the first real test of the arithmetic instruction.

## Verdict

**HOLDS**, with two caveats to carry forward, not two failures:

1. **D3 (materiality bar) works as designed** on `risks`, `reconciliation`, `moat.sources` and
   `management_findings` — all moved cleanly off their pre-change cap-spikes with no observed
   loss of material content in this n=3 read. `what_would_change_my_mind` is unresolved (2/3
   still at cap).
2. **D7 (closed `red_flags`) works as designed** — 2/2 category matches, and JPM's empty list
   is a defensible boundary call, not evidence of suppression, on inspection of the actual
   filing content.
3. **D6 (no quote reuse) reduced but did not eliminate reuse** — 1 confirmed violation in 3
   briefs (AAPL, `risks`+`reconciliation`), down from 62/35. Report as improved, not solved.
4. **"Do the arithmetic" is unexercised** in this sample — no brief computed cash runway or
   refinancing coverage; the one normalization-flavored example found (JPM ROTCE ex-Visa) was
   the model citing a filing-disclosed non-GAAP figure, not deriving one. Needs a filer with a
   live liquidity/refinancing question to test.
5. **No over-application regression found**: no risk list read as truncated against what each
   filer plausibly discloses, and the specifically-warned-about failure (a bank returning 2–3
   risks, or a red-flags list emptied of a genuine covenant/liquidity item) did not appear.

**n=3 on three large caps, one run each, is a weak sample** — enough to catch a gross
regression (there isn't one) and to confirm the grounding/safety invariants from the
2026-08-17 cut still hold, not enough to certify D3/D6/D7 at the rigor of the original 35-brief
corpus. The honest next step is a repeat of the original punctuation-insensitive D7 keyword
scan and D6 substring scan over a larger fresh corpus once enough new-prompt briefs accumulate
on disk, plus a filer with genuine refinancing/liquidity stakes to test the arithmetic clause.
