# Evidence discipline for moat sources and management findings (2026-08-17)

Closes the `TODO.md` §2a bullet "Evidence discipline is asymmetric". Scope is the one the
TODO pre-registered: *moat sources and the management conclusion each require a quoted span,
run through the existing `_verify_grounding`* — not a full
`claim/evidence_ids/source_type/source_date/url` ledger.

## The defect, measured

`/deep` briefs carried two tiers of rigor with nothing marking the boundary. `risks`,
`red_flags`, `added_risks` and `reconciliation` are quote-verified against filing text
(`assess.py:_verify_grounding`) and render `_(unverified)_` when a quote fails to locate.
`moat.sources` was `list[str]`; `business_model_summary` and `management_capital_allocation`
were bare `str`. No evidence field was even requested of the model.

Measured over the 8 briefs then on disk under `research/` (reproduced twice, independently):

| | |
|---|---|
| moat sources total | 38 |
| …asserting a specific figure with no grounding anywhere in the brief | **17 (45%)** |
| `management_capital_allocation` length | 900–1,157 chars |
| …numeric tokens per brief | **14–27, every brief** |

Examples: NVDA "7.5M+ developers", "$76.7B cumulative R&D"; WDC "approximately 4,500 active
patents"; DGX "18 aircraft, >83,000 daily courier stops"; AAPL "$89.3 billion of common
stock"; FCN "$858.7M in FY2025 (5.3M shares at an average $163.07)".

So the highest number-density prose in the brief sat under the weakest standard. Corroborated
by `docs/audits/2026-08-04-deep-brief-assessment.md:89-91`, where AAPL's moat trajectory was
asserted "widening" off a mix-shift observation "and the brief does not distinguish them".

## What shipped

- `Moat.sources` → `list[Finding]`; new `QualitativeAssessment.management_findings:
  list[Finding]`. Both verified by `_verify_grounding`, both rendered by `_findings_md`,
  inheriting blockquotes and the `verified against <segment>` provenance.
- **`management_capital_allocation` was re-scoped, not duplicated.** The prompt now assigns it
  the *judgment* and forbids enumerating figures there; every checkable fact moves to
  `management_findings`. Shipping the list beside unchanged prose would have recreated the
  measured "one fact, three slots" pathology (`2026-08-04` audit `:346-352`, 62 instances
  across 31/35 briefs).
- **A third render state.** An empty quote in those two lists is a legal answer, rendered
  `_(unquoted — inference or from a section not provided)_`, counted in a new
  `inference_count` with its own footer line.

### Three decisions that look optional and are not

**1. `unverified_count` must not absorb declared inferences.** That number renders as "could
not be verified against the filing text" — the reader's *fabrication* signal, certified
trustworthy across all 35 files by the `2026-08-04` audit (`:663`). Merging populations would
destroy it. `inference_count` is separate, following the `silent_count` precedent
(`assess.py:243-250`).

**2. A declared inference must never reach `_locate`.** `_norm("")` returns `""`, and
`"" in hay` is `True` for **every** segment. `_locate` returns `None` today *only* because of
the `_MIN_EVIDENCE_CHARS = 12` early return (`assess.py:217-218`) — a guard written for a
different purpose and, until this change, pinned by no test. If it ever moves, every empty
quote would verify against the first segment and render as grounded. So the empty case is
branched on the *normalized* string before `_locate` is called (whitespace-only is an
inference, not a fabrication), and `test_empty_evidence_never_locates` now pins the guard.
Mirror-image of `models.py:139`, which drops empty texts so a label can never match "".

**3. The parse must tolerate the legacy bare-string shape.** `_finding_from` raises
`AttributeError` on a `str` (`models.py:260`) and `_findings` raises `ValueError` on any
non-dict — but `assess.py`'s parse-retry catches only `ValueError`/`JSONDecodeError`. A model
drifting back to `sources: ["brand"]` would therefore escape the retry and drop the **entire
brief**. `_evidence_pairs` accepts both shapes and skips anything else, following the
`_added_risks` convention that an advisory list must never sink a valid brief. `_findings_md`
coerces at render time for the same reason.

Note the inverse hazard, which is why prompt and parser had to ship in one commit: the old
`sources=[str(s) for s in ...]` silently stringified a dict into the literal markdown bullet
`- {'claim': 'brand', 'evidence': 'q'}`.

## Cost

`_PROMPT_MODULES` includes `assess` and `models` (`cachekey.py:58-60`), so
`PROMPT_FINGERPRINT` moves and every cached brief key is invalidated at once. Correct
behaviour — an edited prompt must not serve stale briefs — and bounded in practice by
`research.cache.max_age_days: 1`, which already expires briefs daily.

Output length is bounded **nowhere**: `claude_cli.run` passes no max-output-tokens flag, and
`assess.py` returns `None` on `stop_reason == "max_tokens"` **without retrying**, dropping a
brief already paid for. Adding quotes to two lists costs ~400–1,200 output tokens on exactly
the verbose filers (JPM, INTC) that already stress that path, so both lists ship capped:
`research.max_moat_sources: 6`, `research.max_management_findings: 6`.

## Known limitations — do not mistake these for bugs

- **A filing fact from an unsent section lands in the inference bucket.** The haystack is
  Item 1 + Item 7 + Item 1A + 10-Q MD&A + 8-K. Item 5 (issuer purchases of equity securities)
  and the financial statements are **not** sent. The label was worded "inference **or from a
  section not provided**" precisely so it is not a lie in that case — and the live run below
  confirms that wording was load-bearing, not pedantry. **Measured n=1** (see §Live
  verification): the concern was overstated for buybacks, which Item 7 MD&A does carry.
- **None of this is visible on the Telegram surface.** `/deep` delivers `art.png/html/text`
  built from the viewmodel (`telegram.py:339-341`); the markdown brief is never sent, and
  `viewmodel.py:126-127` reduces even *risks* to `_claim(x)`, stripping evidence. This is a
  **pre-existing** gap that applies equally to risks, not one introduced here, and fixing it
  is its own design question (message length, `Detail` levels). Recorded in `TODO.md`.
- The rule split — strict "omit the item" for risks, permissive empty for these two lists —
  is a real bleed risk in both directions. It is mitigated by stating the permissive rule as a
  closed set named twice (positively, then negatively) and by an explicit "a quote that is not
  an exact contiguous span is WORSE than empty". **Held at n=1** (below). One brief is not a
  verdict: keep checking new briefs for empty evidence on `risks`, the dangerous direction.

## Live verification (AAPL, 2026-08-18, n=1)

First brief ever generated with this prompt. `uv run shortlist --tickers AAPL --research 1`,
`stop=end_turn`, 173s, $0.5459. AAPL was chosen because a pre-change brief of the same company
was on disk, making this a before/after rather than a smoke test.

| list | n | verified | declared inference | fabricated |
|---|---|---|---|---|
| `moat.sources` | 5 | 4 | 1 | 0 |
| `management_findings` | 6 | 4 | 2 | 0 |
| `risks` | 12 | 12 | 0 | 0 |
| `red_flags` | 5 | 5 | 0 | 0 |
| `added_risks` | 3 | 3 | 0 | 0 |

`unverified_count: 0`, `inference_count: 3`, `silent_count: 1`.

**The three risks this design was built against, all resolved:**

1. **No truncation.** `stop=end_turn`, not `max_tokens` — the caps held on a large filer.
2. **No rule bleed.** All 20 strict-list items carry quotes; zero empty `evidence` leaked into
   `risks`/`red_flags`/`added_risks`.
3. **`management_findings` verifies.** 4 of 6, including the $89.3B buyback and $15.4B
   dividends that the *old* brief asserted as bare prose. One verified against the **10-Q
   MD&A** with correct segment provenance.

**The label wording was load-bearing.** Two of the three declared inferences — the 533x CEO
pay ratio and the insider-alignment read — come from `research/proxy.py` and the Form-4
context lines, which are **prompt-only and deliberately outside the grounding haystack**. The
model correctly declined to fabricate a quote for them. "Analyst inference" alone would have
been a false label there; "or from a section not provided" is accurate.

**The management re-scoping worked as designed.** Same company, same section:

| | old brief | new brief |
|---|---|---|
| `management_capital_allocation` | 1,157 chars | 575 chars |
| numeric tokens in that prose | **26** | **0** |

The old version asserted "$63.8 billion remaining under existing programs as of March 2026"
with no grounding available anywhere. The new prose is judgment only; every figure moved into
`management_findings`, quoted.

**Do not over-read n=1.** AAPL is a well-structured filer with a quotable Item 1. The failure
modes to keep watching are a filer whose Item 1 states no moat language at all (does the
inference list inflate?) and a verbose filer near the output ceiling (JPM, INTC).

## Not bundled

`docs/audits/2026-08-04-deep-brief-assessment.md:594-606` `worth-building` items #1
(materiality bar — 7 of 8 local briefs return exactly 12/12 risks against a cap that reads as
a target), #2 (close the `red_flags` enumeration, forbid cross-section quote reuse) and #4
("do the arithmetic") are prompt-only, each backed by a committed measurement, and remain
unbuilt. They are the cheapest next PR on this surface.
