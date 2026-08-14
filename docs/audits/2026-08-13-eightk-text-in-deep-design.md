# Reading 8-K substance into `/deep` — design + evidence (2026-08-13)

**Status: SHIPPED and merged to `main` 2026-08-14** (`8fe6d91`), at **trimmed** scope (§0).
Approved in outline by the owner 2026-08-13. Every extraction rule below is traceable to a
named, measured filing in §2; nothing here rests on a single example.

**Read §6 before §3.** The design in §3 passed CI and was still broken — two defects (§6.2)
made it deliver SEC cover pages instead of the event. §3 is preserved as-designed, with the
superseded rows marked; §6 is what actually shipped.

**Tracked, not `docs/superpowers/specs/`** (gitignored at `.gitignore:37`; `CLAUDE.md` records
two enablement artifacts that already evaporated from there). This adds **no scoring leg, no
gate and no flag** — it is `/deep` prompt + report only — but it is measured work, and the
measurement is the reusable half.

Closes the "8-Ks are detected, never read" bullet of `TODO.md` §2a.

---

## 0. Decisions already made

| decision | choice | why |
|---|---|---|
| **Item scope** | Prioritised set, budgeted: `4.02 > 2.02 > 2.01 > 1.01 > 5.02` | 4.02 outranks 2.02 because a non-reliance restatement unconditionally stops a thesis. Everything else keeps today's bare label. |
| **Grounding** | Haystack **with per-document provenance** | An 8-K *is* filing text — verbatim-quotable, unlike the computed proxy/similarity lines that are correctly prompt-only. But "verified" must not silently widen from "the 10-K" to "a furnished press release", so the reader is told which document a quote came from. |
| **Budget** | **Trimmed: ~10K normalized chars total**, 6K/filing, max 3 filings, ON by default | The owner's YAGNI call, and the right one. An earlier 30K budget aimed at *capturing the release*; the correct goal is *capturing what is new*. The 10-K/10-Q already supply the financial statements — the 8-K's unique contribution is the latest results, the outlook, and the occasional bombshell. |
| **Not web search** | Rejected | Non-deterministic across runs, frequently a summary-of-a-summary, and **unquotable against a primary document** — which would forfeit the quote-verification discipline the whole research layer is built on. |

**Cost accepted:** ~8% longer research prompts (the 30K variant was ~25%, which pushed toward
the 900s timeout a heavy filer has already hit — see `config.yaml research.timeout_s`).

---

## 1. What is wrong today

`assess.py:296-309` renders recent filings as `form + (items …) + filed` off the edgartools
filings index. The substantive text is **never fetched**. A brief therefore knows that an Item
2.02 earnings release or an Item 4.02 non-reliance restatement exists, and knows nothing about
what it said.

The existing 8-K machinery is the **wrong handle** and was not reused: `data/efts.py` →
`edgar/eightk.py` has no production caller (both sit in the uncalled-library tier), and EFTS
returns item codes, not document text.

---

## 2. The evidence

Probe: `docs/audits/scripts/probe_8k.py`, 10 US large caps × their 6 most recent 8-Ks
(60 filings, 26 with an EX-99 exhibit), run 2026-08-12/13. Keyless, reproducible, free.

### 2.1 What held up

| assumption | result |
|---|---|
| `items` is populated and parseable | **60/60**, uniform `str`, comma-separated `d.dd` |
| Priority items are common enough to matter | **46/60** rows hit; **10/10** tickers had ≥1 in their last six |
| EX-99 is the earnings-release convention | holds — and scoping to `EX-99*` dodges XOM's charter exhibits and NKE's **304,310-char** EX-10.1 agreement |

### 2.2 What broke — five measured failures

**F1 — selecting off `filing_events` starves the feature.** `EdgarSource._index_limit = 40`
truncates a *mixed-form* index before the 90-day filter. Measured on JPM: 35 of 40 rows are
`SCHEDULE 13G/A`, so the window collapses to **2026-07-16 → 2026-08-11, 26 days**. Only 2 8-Ks
survive, neither a priority item, and JPM's **Item 2.02 earnings release (2026-07-14) falls two
days outside**. The feature would emit nothing for JPM. Not an edge case — Form 144 crowds the
same index (CVX 26/40, WDC 23/40, MSFT 18/40).

**F2 — "EX-99 replaces body" breaks on multi-item filings.** NKE 2026-06-23 is items
`2.02,5.02,7.01`: EX-99.1 is 5,114 chars but the officer-change narrative is in the
**17,522-char body**.

**F3 — empty exhibits exist.** JPM 2026-06-25 (Item 5.02) has `EX-99.1` with **len 0**. A
non-value-aware rule emits nothing for it. Same lesson `_edgar_facts.py` already encodes.

**F4 — a plain prefix loses guidance ~9% of the time.** Of 23 releases containing
outlook/guidance language, 2 sit past an 8K prefix: **JPM at 0.45**, **CVX at 0.41** of the
document. Both are the same structural pattern — an "Outlook" section placed *after* the
financial tables. 10-K risk factors are ordered worst-first (which is what justifies a prefix
slice in `cap_sections`); earnings releases are not.

**F5 — the original 12K budget was mis-calibrated**, and the 30K correction over-shot in the
other direction. EX-99 normalized length ranges **1,938 – 47,906 chars**, median ~26K.

### 2.3 A free win

Whitespace collapse recovers **47–85%** of exhibit bytes (normalized/raw ratio 0.15–0.53 across
26 exhibits; TSLA's are already tight at 1.0). Safe: `_norm` already collapses whitespace on
both sides at verification, so this only saves tokens.

### 2.4 Selected raw observations

| ticker | filed | items | body | EX-99 (raw) | note |
|---|---|---|---|---|---|
| AAPL | 2026-07-30 | 2.02,9.01 | 4,607 | EX-99.1 25,330 | body is pure cover boilerplate |
| JPM | 2026-07-14 | 2.02,9.01 | 7,693 | EX-99.1 38,553; **EX-99.2 115,377** | 99.2 is the financial supplement, not the release |
| JPM | 2026-06-25 | 5.02 | 9,778 | **EX-99.1 = 0** | F3 |
| XOM | 2026-07-01 | 1.01,2.01,3.01,3.03,5.02,5.03,9.01 | 13,546 | none (EX-3/EX-4 only) | body-only path |
| NKE | 2026-06-23 | 2.02,5.02,7.01,9.01 | 17,522 | EX-99.1 5,114 | F2 |
| CVX | 2026-04-09 | 2.02 | 14,987 | **none** | 2.02 with no exhibit at all |
| LLY | 2026-04-30 | 2.02,9.01 | 5,113 | **EX-99** (not .1) 48,737 | bare-`EX-99` naming |
| DIS | 2026-08-05 | 2.02,9.01 | 4,213 | EX-99.1 110,070 | largest in sample |

---

## 3. Design

### 3.1 Selection — independent of `filing_events`

A dedicated `Company(ticker).get_filings(form="8-K")` call in the research layer (needed anyway
to obtain a `Filing` object). Exact form `8-K`; filter to `lookback_days`; rank by item priority,
ties by recency; take at most `max_filings`. **F1 fix — and it removes a dependency rather than
adding one.** `m.filing_events` keeps its existing bare-label role, unchanged.

### 3.2 Extraction — each rule traceable to a measured filing

| rule | driven by |
|---|---|
| Collapse whitespace on ingest | §2.3 |
| Accept `EX-99*` attachments with **non-empty** text only | F3 (JPM 2026-06-25) |
| Prefer the **lowest-numbered** EX-99 (99.1 before 99.2) | JPM 2026-07-14; also sorts LLY's bare `EX-99` first |
| Include the **body** unless the filing's priority items are exactly `{2.02}` | F2 (NKE 2026-06-23) |
| No usable EX-99 → body only | CVX 2026-04-09, XOM 2026-07-01 |
| Prefix, plus a `guidance_window_chars` splice around a late outlook hit, marked `[…]` | F4 (JPM 0.45, CVX 0.41) |
| ~~Budget walked in priority order: each filing takes `min(per_filing_cap, remaining)`; stop at exhaustion~~ **SUPERSEDED by §6.2 (B2)** — it starved the material filing. Equal shares, then redistribution. | deterministic, no rebalancing pass |

**§3.2 is the as-designed rule set and two rows of it were WRONG — read §6.2 before trusting
this table.** The prefix row is also incomplete: it must be preceded by cover-page stripping
(§6.2 B1), or the prefix is spent on SEC letterhead for every body-bearing item type.

The `[…]` elision is safe by construction: a quote spanning it fails the `_norm` substring check
and is correctly marked unverified. The splice cannot weaken the guard.

**The splice is the one heuristic in this design and the weakest part** — fitted to n=2, with a
quiet failure mode (a false positive on "we expect no material impact" spends the window on
boilerplate). It is kept because both misses were the same *structural* convention rather than a
coincidence of two documents. Note it is a **trade, not a free add**: the window displaces prefix
characters within a fixed per-filing cap. Drop it before dropping anything else.

### 3.3 Never raises

Any fetch/parse failure degrades that filing to today's bare label via `log_abstain` — the
contract `_prior_year_sections` already uses. A dead SEC endpoint must not cost a brief.
No new throttle: these go through edgartools like the rest of the research layer
(`CLAUDE.md`: never give an EDGAR caller its own `SecThrottle`).

### 3.4 Provenance

`EightKText(accession, filed, items, label, text)`; `FilingBundle.eightks: list`. Add
`FilingBundle.segments() -> list[(label, text)]` and make `haystack()` the join of those
segments, so every existing caller is unchanged. `_verify_grounding` walks segments and sets
`verified` **plus** a new `Finding.source` / `Conflict.source` to the first matching label:
`"10-K"`, `"10-Q MD&A"`, `"newly disclosed risks"`, `"8-K 2026-07-30 (Item 2.02, EX-99.1)"`.

### 3.5 Prompt & report

Prompt gains a `=== RECENT 8-K — <date>, Item(s) … ===` block after the 10-Q MD&A, plus one
instruction: 8-K text **is** quotable filing evidence, but it is a current report — and a 2.02
exhibit is *furnished*, not filed — so it is not audited annual-report text. The
proxy/insider/similarity "never quote" guards are untouched.

`report.py:_findings_md` appends `— verified against 8-K 2026-07-30 (Item 2.02)` only when the
source is not the 10-K, so **output stays byte-identical when no 8-K text is present**.

### 3.6 Config

```yaml
research:
  eightk:
    enabled: true
    lookback_days: 120
    max_filings: 3
    max_chars_total: 10000      # normalized chars, post-whitespace-collapse
    max_chars_per_filing: 6000
    guidance_window_chars: 1500
    items: ["4.02", "2.02", "2.01", "1.01", "5.02"]
```

The `research` block already joins the prompt/config fingerprint, so retuning any cap busts the
brief cache automatically.

### 3.7 Cache

`cachekey.py:229` already digests `(form, items, filed)` per filing event, so a new 8-K already
busts the key. **One-line change:** add `accession` to that tuple so a same-day 8-K/A cannot
collide with its original.

---

## 4. Tests

Fixtures are drawn from the measured filings in §2.4, not invented shapes:

- selection ignores `filing_events` entirely (JPM shape: a priority 8-K outside the 40-row index is still found)
- priority ordering and recency tie-break
- budget walk is deterministic and never exceeds `max_chars_total`
- empty exhibit → falls back to body (JPM 2026-06-25)
- multi-item filing → body included (NKE 2026-06-23)
- pure 2.02 → body skipped (AAPL 2026-07-30)
- bare `EX-99` accepted (LLY 2026-04-30); `EX-99.1` preferred over `EX-99.2` (JPM 2026-07-14)
- no exhibit → body only (CVX 2026-04-09)
- guidance splice fires at the measured offsets; absent a hit, output equals the plain prefix
- provenance: a quote present **only** in the 8-K verifies **with** the 8-K label; a fabricated or stitched quote still fails
- byte-identical prompt when the block is absent/disabled — extend the existing positive-control test
- `pytest.mark.live` pin on the exhibit shape (edgartools attachment API)

---

## 5. Where this is still blind

- **No Item 4.02 appeared in the 60-filing sample.** The top of the priority table is unexercised
  by real data. It will be tested against a constructed fixture only; the first real restatement
  it meets is its first real test. Expected — restatements are rare, which is *why* they rank
  first — but not verified, and not to be claimed as verified.
- **US large caps only.** Small caps, recent IPOs and REITs/insurers untested. The extraction
  rules are SEC-wide filing conventions (the 9.01 exhibit pointer, EX-99.1 as the release,
  boilerplate cover pages) so they should carry; the **size calibration** will not, and it fails
  safe — smaller filers file shorter releases, so the cap simply will not bind.
- **Latency is unverified at the new budget.** WDC's 178K-char prompt measured ~490s against a
  900s ceiling. Re-measure on a heavy filer before calling this done.
- **Foreign issuers are out of scope** — briefs are 10-K-only with an ADR-aware skip, so the
  6-K analogue never arises.

---

## 6. Post-build value test — two bugs found, then SHIP (2026-08-14)

The build passed CI, rendered correct labels and produced readable briefs. It was still
**broken**, and only a test that read the *content* rather than the pass/fail found it.

### 6.1 The first three runs said "kill"

| run | card | 8-K chars | verified quotes | from an 8-K |
|---|---|---|---|---|
| WDC (stub card) | mock | 10,000 | 23 | **0** |
| WDC (real card) | real | 10,000 | 21 | **0** |
| NKE (real card) | real | 10,000 | 29 | **0** |

50 quotes, none from an 8-K — against a pre-registered rule that said kill on zero.

### 6.2 Why it was zero — two defects, both in this design

**B1 — cover pages were never stripped from filing bodies.** NKE's 2026-08-10 Item 5.02
body is 4,023 normalized chars and the CFO appointment sits at **char 2,672**. The prefix
slice therefore delivered SEC letterhead ("One Bowerman Drive, Beaverton") and dropped the
event. §3.2's prefix rule was justified by 10-K risk factors being ordered worst-first; an
8-K body is the **opposite**, and that was never checked. Fix: `_strip_cover` cuts at the
first `Item d.dd` heading, falling back to the untouched body when absent or implausibly
early.

**B2 — the budget walk starved the tail.** Priority-ordered greedy allocation let two
routine 2.02 releases take 6,000 + 3,400 of 10,000, leaving **600** for the 5.02 — and with
only three slots it silently dropped a fourth filing entirely. Item priority ranks which
filings are worth READING; it must not also decide which one gets read. Fix: `_allocate`
grants equal shares, then redistributes unused share in priority order.

Both were invisible to the test suite: every test passed, every label was correct.

### 6.3 After the fix

Extraction, verified with no model call:

| filing | before | after |
|---|---|---|
| 2026-08-10 (5.02) | 600 (letterhead) | **1,994, from "Item 5.02. Departure of Directors…"** |
| 2026-06-23 (2.02+5.02) | 3,400 | 2,733, from "Item 2.02. Results of Operations" |
| 2026-06-30 (2.02) | 6,000 | 4,673, from the release headline |

Total **9,400 of 10,000** — stripping the cover page made the feature *cheaper*, not dearer.

Re-run on NKE: **3 verified 8-K citations, 0 unverified.**

- red flag, from the Item 5.02 body: *"On August 4, 2026, Johanna Nielsen informed NIKE,
  Inc. of her intent to resign as Vice President, Chief Accounting Officer and Corporate
  Controller"* — an accounting-officer resignation, absent from the 10-K entirely.
- red flag + reconciliation, from the EX-99.1: *"Net income was $1.1 billion, up 407
  percent… including a $0.52 benefit related to the expected recovery of…"* — i.e. the
  headline beat was largely one-time.

**Verdict: SHIP.** Both surfaced facts are the "otherwise confidently wrong" case the
feature was justified on.

### 6.4 Measured cost

| | |
|---|---|
| prompt growth | +10,100 chars, **5.6–7.7%** (fixed, not a multiplier — smallest on the heaviest filers) |
| brief latency | 124–187s against a 900s ceiling (recorded pre-feature WDC baseline ~490s) |
| brief cost | ~$0.71–0.91 |
| extra network | ~13–19s for the whole bundle including 8-Ks |

### 6.5 Method note

Three measurement instruments gave the wrong answer before the right one landed: a citation
tally that omitted `reconciliation`; a token matcher that missed paraphrase; and a plausible
read of the narrative prose ("a CFO transition") that traced back to the **10-K**, not the
8-K, when checked. Verify the mechanism, not the pattern.
