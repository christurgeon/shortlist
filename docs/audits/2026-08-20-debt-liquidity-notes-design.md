# Debt & liquidity notes in `/deep` — design + 20-filing probe evidence

**Date:** 2026-08-20 · **Status:** design, implemented in the same PR · **Scope:**
`shortlist.research` only. No scoring, gate, flag or composite semantics change.
Origin: `TODO.md` §2b item (2), which this closes.

> **Doc location.** The brainstorming skill defaults specs to
> `docs/superpowers/specs/`, which is gitignored here and has already lost two
> enablement artifacts (`CLAUDE.md`). Tracked `docs/audits/` wins, matching the
> 8-K and 10-Q Part II precedents.

---

## 1. The problem this closes

`SYSTEM_PROMPT` has shipped an arithmetic instruction since 2026-08-18 asking the
model to compute **refinancing coverage** — "debt maturing within twelve months
against cash plus operating cash flow" — and **cash runway** — "cash and
equivalents plus undrawn facilities against the current burn rate".

The 2026-08-19 live verification measured that instruction as **UNTESTED for a
structural reason, not a sampling one**: 0 of 3 briefs computed anything, because
two of its three asks were unanswerable. The maturity ladder lives in a statement
note, and `assess.py:324-331` sends Item 1, Item 7, Item 1A and the 10-Q MD&A
only. Nothing supplied the input.

`docs/audits/2026-08-19-deep-prompt-live-verification.md` names this as the
concrete blocker. Cash was added as a rendered column on 2026-08-19; the maturity
ladder is the remaining half.

## 2. Route

Rejected, with reasons:

- **XBRL companyfacts numbers.** Probed 12 large caps.
  `LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths` exists for
  10/12, but the *latest* value is stale for two: **UAL's most recent is FY2013,
  filed 2014-02-20**, and LLY stopped tagging after FY2024. A naive `max(filed)`
  read drops a 12-year-old number into a 2026 brief. Worse, it cannot ground a
  quote — a computed number is prompt-only (the `text_similarity` rule), so the
  model could never cite it. And undrawn revolver capacity
  (`LineOfCreditFacilityRemainingBorrowingCapacity`) exists for **1 of 12**, so
  the cash-runway half stays blocked forever.
- **Parsing the ladder into numbers in code.** New numeric extraction carries
  transcription risk — the exact class PR #145 measured and declined when it
  rejected splitting `extract_financials`/`panel_to_metrics`.

**Chosen: note text via edgartools.** `TenK.notes` is an XBRL-derived *structured
index* (`edgar.xbrl.notes.Notes`), not a text blob, so notes are individually
addressable by title. This avoids the `_tenq_mda` item-boundary fault class
entirely — there is no heading detection and no span slicing. The text is filing
text, so unlike `text_similarity` it enters the grounding haystack and the model
can quote it.

## 3. Probe evidence

`docs/audits/scripts/probe_debt_notes.py`, 20 tickers chosen for spread
(mega-cap tech, two banks, energy, pharma, retail, an airline, a REIT, a utility,
a heavy borrower, and a near-debt-free name as the true-negative control).

### 3.1 Selection — 20/20 on the 10-K, 15/20 on the 10-Q

Title regex:

```
debt|borrow|credit facilit|credit agreement|financing arrangement
|notes payable|long[- ]term obligation
```

Two rules are traceable to a named filing and must not be "simplified" away:

- **`long[- ]term obligation` exists for AMT alone.** American Tower titles its
  debt note `LONG-TERM OBLIGATIONS` — no `debt` or `borrow` token anywhere in the
  title. Without this alternative AMT matched **0 of 26 notes** despite carrying
  ~$40B of debt. The alternative is deliberately narrow: bare `obligation` would
  also match AMT's own `ASSET RETIREMENT OBLIGATIONS`, which is not debt.
- **An exclusion filter is required, not optional.** Duke Energy files
  `Investments in Debt and Equity Securities` — an **asset** note that matches
  `debt`. Unfiltered it was selected and consumed 10,127 chars of budget while
  being irrelevant to refinancing. Excluded by
  `investment|marketable securit|available[- ]for[- ]sale`; no true positive in
  the probe contains any of those tokens.

**Multiple matches are normal, not an error.** NKE files `SHORT-TERM BORROWINGS
AND CREDIT LINES` *and* `LONG-TERM DEBT`; Realty Income files `Credit Facilities
and Commercial Paper` *and* `Notes Payable`; both pairs are relevant. The cap is
per form, not per filer.

**The 10-Q gap is real and is not a parse failure.** JPM, XOM, LLY, T and CVS
file no debt note in their latest 10-Q at all (their note counts are 26/9/12/12/10
and none matches). A quarterly debt note is a legitimate subset of the annual
disclosure, so the 10-K is the backbone and the 10-Q is additive-when-present.
This is why a missing 10-Q note is silently omitted rather than reported as a
data gap — unlike `tenq_accession and not tenq_mda`, which *is* a parse failure.

### 3.2 Payload — the arithmetic inputs are there

| Signal | 10-K | 10-Q |
|---|---|---|
| maturity language | **20/20** | 14/15 |
| facility / undrawn / revolver language | 17/20 | 9/15 |
| covenant language | 13/20 | 7/15 |

The one 10-Q miss is KO's 310-char stub. Note that facility language reaching
17/20 on the text route materially **upgrades** the cash-runway ask relative to
the XBRL route's 1/12 — that half is largely unblocked too, though undrawn
*amounts* are still often prose rather than a clean figure.

### 3.3 Size and the caps

Whitespace collapse alone is worth doing before any cap: it cuts UAL 20,836 →
8,259 (0.40) and JPM 20,910 → 13,312 (0.64). Normalized 10-K sizes span 731
(PLTR) to 39,573 (AMT), median ~6K.

`max_chars_per_note: 16000` is set by measurement, not taste. The 12-month ladder
sits within the first 10,000 chars in **8 of 9** over-cap notes; the exception is
**DUK at 13,022 (0.38 of the note)**. A 10,000 cap would silently drop the ladder
for a utility — a whole sector of heavy borrowers — while appearing to work. At
16,000 the ladder survives in **9 of 9**.

Per-form totals (`max_chars_10k: 16000`, `max_chars_10q: 8000`) rather than one
shared pool: a shared pool would let a heavy borrower's annual notes crowd the
*fresher* quarterly note out entirely, and freshness is the reason the 10-Q is in
scope at all.

**Measured prompt cost** (real `fetch_bundle` → `cap_bundle` → `_build_user_prompt`,
notes on vs off):

| Ticker | prompt without | with | delta | notes |
|---|---:|---:|---:|---|
| AAPL | 121,494 | 126,716 | +5,222 (4.3%) | 2 |
| JPM | 142,580 | 155,939 | +13,359 (9.4%) | 1 |
| AMT | 212,551 | 236,679 | +24,128 (11.4%) | 2 |
| DUK | 216,495 | 240,631 | +24,136 (11.1%) | 2 |

AMT and DUK are the ceiling case and land exactly on the designed 16K + 8K budget.
A light filer pays ~4%.

### 3.4 Truncation cuts at the last whitespace

A prefix cut must never sever a number mid-digits (`4,100` → `4,1`), because the
tables are the payload. Two candidate cut points were measured on the 9 over-cap
notes:

- **row-aligned** (last `\n`) wastes up to **3,071 chars** — GS's note has very
  long lines, so 31% of its cap is thrown away;
- **token-aligned** (last whitespace) wastes **1–11 chars** across all 9.

Token alignment is chosen. It cannot sever a number, and a table row cut at a
column boundary leaves a well-formed prefix.

A truncated note carries an explicit `[…truncated…]` marker. This is
load-bearing: `SYSTEM_PROMPT` instructs the model to *name a missing input rather
than estimate it*, which it can only do if a cut ladder is distinguishable from a
complete one.

This is **not** the 8-K `_ELISION` case and does not inherit its safety argument.
An elision *splices* two non-adjacent spans, so a quote crossing it asserts a
contiguity the filing never had and must fail verification. Truncation only drops a
suffix — nothing is spliced — so a quote containing the mark is legitimately a
substring of what the model was shown and correctly verifies. The guarantee here is
the weaker, sufficient one: text past the cut is **absent from the haystack**, so a
model that reconstructs a severed figure fails verification.
(`tests/research/test_debt_notes_wiring.py::test_content_past_the_truncation_cut_cannot_be_quoted`.)

## 4. Design

**New module `research/notes.py`** — a leaf mirroring `eightk.py`'s contract:
never raises (any fetch/parse failure degrades that form, or the whole ticker, via
`log_abstain`), no throttle of its own, a merged config block, ships **ON**, and
byte-identical to today when `enabled: false` or the block is absent.

**Model** `DebtNote(form, accession, title, text, truncated)` in `research/models.py`.

**`FilingBundle.debt_notes: list[DebtNote]`**, default empty. Populated in
`fetch_bundle` from the 10-K object and the **already-parsed** 10-Q object — the
one that today feeds `_tenq_mda` and `_tenq_added_risks` — so the marginal fetch
cost is zero.

**Grounding.** Each note is its own segment in `FilingBundle.segments()`, labelled
`10-K note: LONG-TERM OBLIGATIONS`. It is filing text, so it belongs in the
haystack; its own segment so a verified quote attributes to the note rather than
to the 10-K at large.

**Cache key.** No new accession — the notes come from the 10-K/10-Q already in
`cache_key`. `_config_fingerprint` hashes the whole `research` block minus
`_CONFIG_SKIP = ("output_root", "cache")`, so `research.notes` is folded in
automatically. **`notes` MUST be added to `cachekey._PROMPT_MODULES`** next to
`eightk`, or editing the extractor serves stale briefs.

**Prompt.** A new labelled section in `assess.py` beside the 10-Q MD&A. No
`SYSTEM_PROMPT` change: the arithmetic clause already asks for refinancing
coverage and already says to name a missing input rather than estimate it.

## 5. Explicit non-goals

- **No scoring field, flag or gate.** `check_flags` runs inside `score()` during
  `run_harness` and research runs after it (`screen.py:188`), so a research-layer
  producer is structurally too late — the same reason `filing_text_change` cannot
  fire. This is research-layer context only.
- **No numeric parsing of the ladder.** §2.
- **Not the other five note families** in `TODO.md` §2b (segments, SBC,
  concentrations, goodwill, legal contingencies). Debt & liquidity is item (2) and
  the only one with a shipped prompt instruction waiting on it.

## 6. Known limits, recorded rather than papered over

- **DUK's 10-Q note (13,894 chars) is capped at 8,000** and its ladder position in
  the *quarterly* note was not measured. The 10-K carries the authoritative ladder.
- **Undrawn facility *amounts* are often prose, not a figure.** Facility language
  is present 17/20, but the model may still have to name an undisclosed input.
- **Selection is title-only.** A filer that buries debt in a generically titled
  note is a silent miss, as AMT was before this probe. The probe script is
  committed so the rule can be re-measured on a wider set.
- **n=20, large- and mid-cap, one filing each.** Small caps and non-calendar
  filers are unmeasured.
