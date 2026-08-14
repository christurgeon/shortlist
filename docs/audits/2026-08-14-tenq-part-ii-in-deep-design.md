# 10-Q Part II in `/deep` — design + 25-filing probe evidence (2026-08-14)

**Verdict: ship Item 1A as a DIFF against the 10-K; DEFER Item 1 (legal proceedings).**

Closes the "10-Q contributes only MD&A" half of `TODO.md` §2a. Evidence below is from two
live probes over real filings, not from reasoning about what filings usually contain.

## Probe 1 — is the content there? (15 tickers, latest 10-Q each)

`get_item_with_part(part, item, markdown=True)` on the parsed `TenQ`.

| ticker | Part I Item 2 (MD&A) | Part II Item 1 (legal) | Part II Item 1A |
|---|---:|---:|---:|
| AAPL | 21,464 | 5,545 | 19,108 |
| JPM | **601,221** | 252 | 402 |
| XOM | 69,820 | 245 | 0 |
| MRK | 89,659 | 229 | 0 |
| TSLA | 49,879 | 229 | 328 |
| KO | 77,141 | 14,304 | 641 |
| NVDA | 28,905 | 217 | 43,097 |
| WFC | 17,666 | 207 | 205 |
| LMT | 47,408 | 761 | 791 |
| PFE | 200,198 | 6,737 | 388 |
| BA | 46,149 | 258 | 204 |
| GILD | 32,963 | 269 | 84,281 |
| F | 90,640 | 3,728 | 0 |
| INTC | **0** | **71,869** | 3,632 |
| DIS | 66,071 | 313 | 17,592 |

### Finding 1 — `TenQ` has no `risk_factors` attribute
Confirmed on 10/10 names in probe 2 (`rf_attr_chars == 0` for every one). The TODO's
claim was correct: `_section(obj, "risk_factors")` returns `""` for any 10-Q, so Part II
items are reachable only through `get_item_with_part`.

### Finding 2 — raw Item 1A is bimodal, and the tail is enormous
Range 204 → 84,281 chars. Four of fifteen restate **every** risk factor quarterly
(GILD 84K, NVDA 43K, AAPL 19K, DIS 17.6K) — text the 10-K's Item 1A already carries in
the same prompt. Three disclose nothing at all (XOM, MRK, F → 0).

### Finding 3 — Item 1 legal proceedings is a POINTER, not content
Ten of fifteen are 200–800 chars of cross-reference: JPM *"Refer to … Note 24"*, XOM
*"Refer to … Note 7"*, MRK *"incorporated herein by reference to Note 8"*. The legal
substance lives in the **notes**, which `/deep` does not extract — that is `TODO.md` §2b
(legal contingencies is item 6 on its own ordered list). Only AAPL (5.5K), KO (14.3K),
PFE (6.7K) and F (3.7K) disclose inline.

**Consequence:** an Item 1 extractor buys ~250 chars of "see Note 24" for most names, and
gives the model text that can be quoted as verified evidence while saying nothing. The
useful build is a notes extractor, not an item extractor. **Deferred to §2b.**

### Finding 4 — the extractor over-captures, and it already bites shipped code
Two failures, neither introduced by this work:

- **INTC Part II Item 1 → 71,869 chars of "Note 14: Contingencies"** — not Item 1 at all.
  A length threshold alone would have admitted 71K chars of note text under a
  "10-Q legal proceedings" label, misattributing any verified quote. A second reason to
  defer legal.
- **JPM Part I Item 2 → 601,221 chars** (over-capture) and **INTC → 0**. This is the
  *shipped* `_tenq_mda` path. Recorded in `TODO.md` §2a.

  **CORRECTION (same day, wider probe of 35 large caps).** An earlier revision of this
  bullet said JPM's brief "has been fed the first 40K chars of a 601K over-capture",
  implying the model saw the wrong content. That is **wrong** and was retracted after
  measurement:

  | symptom | incidence | fractions | consequence |
  |---|---|---|---|
  | 0-char extraction | **1 / 35** (INTC) | — | **the real defect** — no quarterly MD&A in the brief |
  | over-capture (span >= 50% of doc) | **3 / 35** | JPM 0.846, MCD 0.644, PFE 0.566 | **benign** — see below |
  | normal | 31 / 35 | median 0.230, p90 0.397 | — |

  All three over-capturing spans **start at a genuine MD&A heading**, so the prefix that
  survives `max_chars.tenq_mda` (40,000) is genuine MD&A prose — the model sees correct
  content. The cause is the mirror image of INTC's: a *neighbouring* item's heading goes
  undetected and this span swallows it, rather than this span's heading going undetected
  and the previous item swallowing it (on INTC the preceding Part I Item 1 span is 135,783
  chars). The clean gap between p90 0.397 and 0.566 is why the observability threshold sits
  at 0.50. **Do not change extraction behaviour for over-capture.**

  **Two measured non-fixes**, each pinned by a regression test in
  `tests/research/test_filings.py`:
  - `tenq["Item 2"]` is **not** a fallback for the INTC gap — it returns 2,459 chars of
    *Part II* Item 2 (unregistered sales / share repurchases), i.e. wrong content that
    would be silently labelled MD&A inside the grounding haystack.
  - `tenq.items` is **not** a usable guard — XOM lists an unqualified `'Item 2'`, TSLA
    lists 3 entries, MCD exactly one, yet `get_item_with_part("Part I","Item 2")` returns
    69,820 / 49,879 / 122,045 chars for them respectively. Any guard keyed on `items`
    reports phantom failures.

  **Shipped instead:** `_tenq_mda` now logs both symptoms to stderr (the 0-char abstention
  and an over-capture note carrying the fraction) and returns the text unchanged.
  *Recovering* the missing INTC span by slicing the containing Part I Item 1 blob at an
  MD&A heading is **deferred pending a wider probe** — it is fitted to n=1 and its failure
  mode injects wrong text into the haystack.

## Probe 2 — does diffing fix Finding 2? (10 tickers)

`riskdiff.added_risk_blocks(part_ii_item_1a, tenk_item_1a, cfg)`:

| ticker | raw Item 1A | 10-K Item 1A | **after diff** |
|---|---:|---:|---:|
| NVDA | 43,097 | 114,773 | **2,949** |
| GILD | 84,281 | 83,472 | **1,602** |
| AAPL | 19,108 | 68,163 | **2,324** |
| DIS | 17,592 | 62,771 | **1,629** |
| INTC | 3,632 | 104,871 | 1,406 |
| LMT | 791 | 78,572 | 768 |
| KO | 641 | 92,467 | **0** |
| JPM | 402 | 112,862 | 207 |
| TSLA | 328 | 0 | **0** |
| BA | 204 | 57,151 | 181 |

Every name collapses under 3K, and the surviving blocks are decision-relevant: AAPL's
newly-added AI/compute-capacity risk, DIS's third-party IP-infringement litigation risk,
NVDA's new blocks, GILD's manufacturing-certification risk. Names with nothing new go to
exactly zero, so their prompt is byte-identical.

### Finding 5 — do NOT filter on "no material changes" boilerplate
NVDA's section **opens** with *"Other than the risk factors listed below, there have been
no material changes…"* and then lists 2,949 chars of genuinely new risk factors. A regex
on that sentence — the obvious first design — would drop exactly the content worth having.
The diff already collapses a true boilerplate filer (BA, JPM) to ~200 chars, so no
special-casing is needed.

## Shipped shape

- `research/filings.py:_tenq_added_risks` — extract Part II Item 1A, diff against the
  **uncapped** 10-K Item 1A (`cap_bundle` runs later; a fuller baseline can only reduce
  false "new" blocks), never raises.
- `FilingBundle.tenq_added_risks` + a `segments()` entry labelled `"10-Q Part II Item 1A"`.
  It is filing text, so unlike the reverse-DCF/proxy context lines it **does** enter the
  grounding haystack — as its own segment, so `_verify_grounding` attributes a verified
  quote to the 10-Q rather than silently widening "10-K".
- Prompt section after the 10-Q MD&A, before the 8-K substance.
- `config.yaml: research.tenq_risk_update` — its own block, deliberately not
  `research.risk_diff`, so tuning the quarterly update cannot move the YoY 10-K diff.
- **No cache-key change needed**: the 10-Q accession already rides in
  `FilingBundle.cache_key`, so a new 10-Q busts the brief.

### Measured cost (production path, after `cap_bundle`)

| ticker | prompt with feature | without | delta |
|---|---:|---:|---:|
| NVDA | 177,547 | 174,524 | +3,023 (**+1.73%**) |
| BA | 182,285 | 182,030 | +255 (+0.14%) |
| KO | 214,449 | 214,449 | +0 (0.00%) |

For comparison the 8-K feature measured ~8%. Note in passing that KO's capped prompt
(214K) already exceeds the 178K "heavy filer" reference in `config.yaml`'s `timeout_s`
comment — pre-existing, and unaffected by this change (delta 0).

## What this does NOT do

The Lazy-Prices YoY **similarity** still has no 10-Q arm: `_filing_sections`
(`filings.py`) feeds `filing_text_change(form="10-Q")`, which is a separate
point-in-time function with no production caller, and it still reads
`_section(obj, "risk_factors")` — always `""` on a 10-Q. Only the risk **diff** half of
that TODO bullet is shipped.
