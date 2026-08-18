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
  and the financial statements are **not** sent, and that is exactly where buyback figures
  like "5.3M shares at an average $163.07" live. The label was worded "inference **or from a
  section not provided**" precisely so it is not a lie in that case. **Unmeasured:** what
  share of `management_findings` can verify at all. It needs a live EDGAR probe, which no
  environment with `SEC_IDENTITY` was available for at build time. Run it before drawing any
  conclusion from the inference/quote ratio.
- **None of this is visible on the Telegram surface.** `/deep` delivers `art.png/html/text`
  built from the viewmodel (`telegram.py:339-341`); the markdown brief is never sent, and
  `viewmodel.py:126-127` reduces even *risks* to `_claim(x)`, stripping evidence. This is a
  **pre-existing** gap that applies equally to risks, not one introduced here, and fixing it
  is its own design question (message length, `Detail` levels). Recorded in `TODO.md`.
- The rule split — strict "omit the item" for risks, permissive empty for these two lists —
  is a real bleed risk in both directions. It is mitigated by stating the permissive rule as a
  closed set named twice (positively, then negatively) and by an explicit "a quote that is not
  an exact contiguous span is WORSE than empty". **Whether the model honours it is unmeasured**
  until briefs are regenerated; check the first few for empty evidence appearing on `risks`,
  which would be the dangerous direction.

## Not bundled

`docs/audits/2026-08-04-deep-brief-assessment.md:594-606` `worth-building` items #1
(materiality bar — 7 of 8 local briefs return exactly 12/12 risks against a cap that reads as
a target), #2 (close the `red_flags` enumeration, forbid cross-section quote reuse) and #4
("do the arithmetic") are prompt-only, each backed by a committed measurement, and remain
unbuilt. They are the cheapest next PR on this surface.
