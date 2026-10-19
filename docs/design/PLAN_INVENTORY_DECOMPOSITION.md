# PLAN — inventory context line, pre-clamp stance, textsim retirement

**Status:** design-time (2026-08-20), revised after three adversarial reviews.
Records intent at time of writing; file/line references are not maintained. Read the
code for behaviour.

Three changes, **shipped as three separate PRs** in the order §3 → §2 → §1. They
share no code; the repo's idiom (`git log`: #186, #183) is one feature per PR with
its tests and its audit doc. Bundling would put the change needing the most evidence
scrutiny in the same review as two small ones, and a revert of §1 for a sign bug
would also revert §3.

None of them adds a scoring leg. Motivating run: `/deep LULU` and `/deep HDSN`,
2026-08-20 (briefs under `/opt/shortlist/research/`).

---

## §0 Validation performed before this spec

Every number below was measured. Reproduce with `SEC_IDENTITY` set, from the repo
root so `.env` loads.

### 0.1 The two extractors sign the same concept oppositely — load-bearing

`CLAUDE.md` warns that `providers/_edgar_facts.py` (harness) and
`providers/_xbrl_facts.py` (backtest) are separate extractors. They also apply
**opposite sign conventions**:

| Source | HDSN FY2025 `IncreaseDecreaseInInventories` |
|---|---|
| SEC companyfacts (raw XBRL, `_xbrl_facts` path) | **+40,913,000** — positive = balance build |
| edgartools statement DataFrame (`_edgar_facts` path) | **-40,913,000** — `preferred_sign: -1.0`, already the cash-flow contribution |

Balance-sheet cross-check: `InventoryNet` 96,247,000 (FY24) → 135,923,000 (FY25), a
+39,676,000 build.

**The 1,237,000 residual reconciles to the inventory WRITE-DOWN**, not to an
acquisition. An earlier draft of this spec guessed the Refrigerants Inc. acquisition;
the sign is backwards — acquired inventory makes the balance-sheet build LARGER than
the cash-flow line, and here it is smaller. `us-gaap_InventoryWriteDown` is a non-cash
LCNRV charge that cuts carrying value without touching the working-capital line:

```
FY2025:  BS change +39.676 = CF +40.913 - writedown 1.726 + acquired 0.489
FY2024:  BS change -58.203 = CF -60.248 - writedown 3.028 + acquired 5.073
```

The identity closes both years with the right sign, and FY2024's +5.07M of acquired
inventory is consistent with the $20.67M USA Refrigerants deal. Note the write-downs
are RECURRING (2.52M, -2.26M, 3.03M, 1.73M across 2018/2023/2024/2025) — management
has already marked this inventory down more than once, which bears directly on §1.1.

**Consequence:** on the harness path a build arrives NEGATIVE. Implementing against
the companyfacts convention would ship a flag that fires on inventory *liquidations*.

### 0.2 Inventory dominates the swing — but "cash generation improved" is FALSE

The arithmetic of the swing is solid. The inventory line moved -101.16M
(+60.248M → -40.913M), which is **107% of the OCF delta** (+91.811M → -3.162M
= -94.97M). "The FY24→FY25 collapse is dominated by an inventory cycle" is defensible.

**What does NOT follow — and what an earlier draft of this spec wrongly claimed — is
that underlying cash generation improved.** That conclusion survives exactly one cut
of working capital, the one that strips the inventory outflow while keeping the
payables inflow:

| Basis | FY2024 | FY2025 | change |
|---|---|---|---|
| FCF (OCF + capex) | +86.51M | -8.21M | — |
| FCF ex-**inventory only** | +26.26M | **+32.70M** | **+24% "improved"** |
| FCF ex-(inventory + AR + AP) | +26.66M | **+16.30M** | **-39% declined** |
| FCF ex-**all** working capital | +28.77M | **+19.32M** | **-33% declined** |

FY2025 was part-financed by a **+20.2M accounts-payable inflow** against **-12.7M** in
FY2024. Stripping inventory while keeping payables is what manufactures the
improvement. A multi-year view kills it independently: FCF ex-inventory by fiscal year
2018-2025 runs -8.09, -10.65, -8.50, +43.73, **+112.23**, +61.78, +26.26, +32.70 — the
FY2024 comparator is the trough of a four-year ~70% decline, and "improved" is true
only against the single worst available base year.

**This is exactly the failure mode `CLAUDE.md` warns about** — a story assembled by
choosing which lines to strip. It is recorded here rather than deleted because it
directly determines the design in §1: a single-line "FCF ex-inventory" adjustment is
not a neutral decomposition, it is a thumb on the scale.

**The motivating premise was also wrong.** This spec originally claimed the brief
"could not see the composition". It could, and did — the shipped HDSN Bear line reads:

> "...operating cash flow turned sharply negative in FY2025 **on an inventory build**,
> cash has fallen by more than 60% in 18 months..."

The real defect is narrower: that Bear line coexists with a red-flag bullet calling the
same facts "a genuine cash-burn/liquidity stress trend", with nothing to reconcile
them. And the brief's other cash concerns are inventory-independent and survive intact
— cash 70.1M → 39.5M, 20.0M of buybacks in a negative-FCF year, gross margin 50.1% →
38.6% → 27.7% → 25.2% across FY22-25.

**The one durable, line-selection-independent signal** found in this exercise is days
inventory outstanding (`inventory / (COGS/365)`):

| | FY2022 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|---|
| DIO (days) | 326.9 | 317.6 | **205.0** | 268.9 |

FY2024's 205 days is the outlier, not FY2025's 269. That supports "restocking toward a
historical norm" without depending on which working-capital lines anyone chooses to
strip — which is why §1 ships this and not the FCF adjustment.

**LULU control:** OCF 1,602.477M - capex 680.802M = FCF **921.68M**, matching the
shipped brief's "$922M" — the reconstruction is validated against known-good output.

### 0.3 Both concepts are present on the harness path

Verified live against the latest 10-K of each filer:

| Concept | Statement | Column type | HDSN | LULU |
|---|---|---|---|---|
| `us-gaap_IncreaseDecreaseInInventories` | cash flow | FY duration | 1 row | 1 row |
| `us-gaap_InventoryNet` | balance sheet | instant | present | present |

**No aggregate working-capital tag is usable.** HDSN never reports
`IncreaseDecreaseInOperatingCapital`. LULU reports it only for fiscal 2011–2013 — it
is dead in current filings. Summing itemised components is not viable either: HDSN
uses `IncreaseDecreaseInAccountsPayableAndAccruedLiabilities` while LULU splits into
`IncreaseDecreaseInAccountsPayable` + `IncreaseDecreaseInAccruedLiabilities` + eight
more, an open-ended per-filer set with double-count risk against the aggregate.
**Hence: inventory only, not working capital** (§1).

Also observed: edgartools labels HDSN's OCF row with
`parent_abstract_concept = IncreaseDecreaseInOperatingCapitalAbstract`, which is
wrong. Match on the raw `concept` column only.

### 0.4 `filing_text_change` cannot fire — for two independent reasons

**Primary, and already known to the repo:** `m.filing_text_similarity` has **no
writer anywhere in `src/`**. Only the dataclass declaration (`models.py:196`), the
flag rule (`scoring.py:751`) and JSON forwarding (`screen.py:288`) reference it.
`tests/test_flag_producers.py` exists solely because of this flag, carrying a
`strict=True` xfail, and `TODO.md` §2a records it. Research runs *after*
`check_flags`, so this is structural, not an oversight.

**Second, and new:** even given a producer, the metric cannot reach the threshold.
Measured with the shipped `research/textsim.py` against `max_similarity: 0.7`:

| Comparison | Cosine |
|---|---|
| LULU FY26 risk+MD&A vs its own prior year | 0.9966 |
| HDSN FY25 risk+MD&A vs its own prior year | 0.9972 |
| **HDSN vs LULU** — unrelated companies, unrelated industries | **0.8973** |

Replacing a filing's entire risk+MD&A text with a different company's, in a
different industry, yields 0.897 — far above 0.7. The cause is mechanical:
`textsim.py:27-30` deliberately retains stopwords, so on a 6.7k–16.8k-token document
the vector is dominated by shared English function words.

Scope of that claim, stated precisely: **the cross-company floor measured here is
0.897 on one pair of filers (n=2).** It is strong evidence that the threshold is
unreachable for full-length 10-K risk+MD&A text, not a proof over all filings. A
shorter filing with a smaller token count could behave differently and has not been
measured.

Consequence: fixing the metric would NOT make the flag live — it would still need a
collection-time producer. Any claim that it "becomes live again once the metric is
fixed" is false.

---

## §1 Inventory context line for `/deep` — NO FLAG

### 1.1 The flag is CUT. It encoded the §0.2 error.

An earlier draft specified an advisory flag `fcf_inventory_build`, firing when a
negative FCF was "fully explained" by an inventory build (`fcf_ex_inventory > 0`).

**That is a machine-encoded version of the mistake §0.2 documents.** Inventory-only is
the single cut that flatters; HDSN fires it (+32.70M) while the wider cut says
underlying generation *declined* (+16.30M). Shipping it would stamp "the burn is
explained" on exactly the names where the claim is least safe, at scale, in a
machine-readable field that `/explain`, the bot theme and any downstream filter key on.

Cutting the flag also removes the whole `KNOWN_FLAGS` / glossary / `theme.py` /
cardinality-test surface, and the `financial_series`-is-scorer-inert problem with it.
Nothing in `scoring.py`, `bot/glossary.py`, `bot/report/theme.py` or
`tests/test_scoring_names.py` is touched by this change any more.

**Scope is therefore: a prompt-only `/deep` context line, and nothing else.** No
scoring change, no flag, no gate change, no `StockMetrics` scalar read by the scorer.

### 1.2 What the line reports

Descriptive only. It reports the *level and trend* of inventory and lets the model
reconcile it against the MD&A. It deliberately does **not** compute an
"FCF ex-inventory" figure — per §0.2 that single-line adjustment is not neutral.

1. **Inventory balance**, current and prior (`InventoryNet`).
2. **Days inventory outstanding**, current and prior — `inventory / (COGS/365)`, where
   `COGS = revenue - gross_profit`, both already in `financial_series`. This is the
   §0.2 signal that does not depend on line selection.
3. **Inventory growth vs revenue growth** — the divergence ChatGPT's LULU question
   turns on, and cheap since both are already present.
4. **The cash-flow inventory line** (`IncreaseDecreaseInInventories`), reported as its
   own number and explicitly NOT netted against FCF.

Worked example (HDSN FY2025, real figures):

> Inventory: balance $96.2M → $135.9M (+41%) while revenue grew 4%. Days inventory
> outstanding 205 → 269 (FY2022-23 ran 327 / 318). The cash-flow statement's inventory
> line was -$40.9M for the year. Computed from the statements, NOT a filing quote, and
> inventory only — other working-capital lines are not included, so this is not a
> free-cash-flow bridge. Reconcile against the MD&A: a build can be restocking, buying
> ahead of price, or product that is not selling.

**For an international filer the two inventory numbers do not reconcile and the line
must say so.** LULU's balance-sheet inventory grew +258.7M while its cash-flow line
shows +188.7M — a 70.0M gap that is FX translation (`EffectOfExchangeRateOnCash`
+91.2M in FY2026; no acquisition, zero write-down). Rendering both in one sentence
without that caveat presents a ~37% discrepancy the reader cannot resolve. The same
applies to write-downs (§0.1): a filer with a large `InventoryWriteDown` has a third
reconciling item. When `|BS change - CF line|` exceeds a configured fraction of the
balance, the line says the two are not the same quantity rather than implying they are.

### 1.3 Files

Substantially smaller than the flag design:

| File | Change |
|---|---|
| `providers/_gaap_tags.py` | `INVENTORY_BALANCE_TAG`, `INVENTORY_CHANGE_TAG` |
| `providers/_edgar_facts.py` | two lists on `EdgarFinancials`, `_end_map`-aligned; `_xbrl_facts.py` **untouched** |
| `data/models.py` | `Statements.inventory` / `.inventory_change`; **add both to `_NON_SIGNAL_FIELDS`** |
| `data/sources/edgar.py` | pass both through the explicit `Statements(...)` kwargs |
| `data/bridge.py` | add both to `_financial_series` `cols` |
| `models.py` | refresh the stale `financial_series` key list |
| `research/inventory.py` | **new** — `context_line(m, cfg)` |
| `research/assess.py` | `_quant_context` gains a 7th param, defaulted `None` |
| `research/cachekey.py` | `_PROMPT_MODULES += ("inventory",)`; append the rendered line to `_aux_lines` |
| `config.yaml` | `research.inventory` block, `enabled` knob |

**`_NON_SIGNAL_FIELDS` is the silent hazard.** `statements` is in `KEY_OBJECTS`, so two
un-excluded fields move the coverage denominator 55 → 57 and shift `coverage()` for
every snapshot ever taken (measured: mock GEV 0.855 → 0.825). That moves
`accumulate.py`'s `THIN_MARK` CAPTURED/THIN classification —
`data/models.py:154-169` records a prior coverage move flipping 16% of 1,432
snapshots. The precedent (`cash_and_equivalents`, `total_assets`, `dividends_paid`) is
to exclude.

`_merge_statements` derives `list_fields` from `fields(merged)`, and `to_dict` /
`from_dict` are generic, so the year-joined backfill and old persisted snapshots need
no change.

### 1.4 Extraction rules

- Match the raw `concept` column exactly via `_rows_by_concept`, which already does the
  `dimension != True` filtering and min-`level` selection. Never `standard_concept`:
  HDSN's `IncreaseDecreaseInInventories` row carries `standard_concept = NaN`, and
  `InventoryWriteDown` is bucketed as `RestructuringExpenseBenefit` on both filers.
- `inventory` is a balance-sheet **instant**; `inventory_change` is a cash-flow **FY
  duration**. Align by ISO end date via `_end_map` (the `ebitda` / `accruals`
  precedent), NOT positionally.
- Return `None` when `len(rows) != 1`. Never sum. Recorded honestly: through
  `_rows_by_concept` both filers yield exactly one row for both concepts, and LULU's
  duplicate `InventoryWriteDown` / `NetIncomeLoss` rows are all `dimension == True` and
  already dropped by that filter — so the multi-row case is **not demonstrated to be
  reachable**. The rule is defensive design for a signed quantity, not a fix for an
  observed bug.
- Every field `Optional`; absence is normal and must never raise. `_series` is
  all-or-nothing, so a concept present for only some periods yields `[]`.

### 1.5 Prompt-only, never in the haystack

`research/inventory.py` copies the `research/gov_contracts.py` contract: pure, no I/O,
`enabled`-gated, `getattr`-guarded, returns `None` to abstain.

Verified by construction: the haystack is `FilingBundle.haystack()` / `.segments()`,
while prompt-only lines are assembled in `_quant_context`, which never touches
`FilingBundle`. The line must not be added to `FilingText`/`FilingBundle` or to
`research/notes.py` (which *does* enter the haystack).

Renders whenever the data is present, regardless of FCF sign.

### 1.6 What this does NOT claim

The `negative_fcf` gate is untouched and HDSN stays clamped to Avoid. This change makes
no argument that the gate is wrong — per §0.2 the evidence does not support one.
`CLAUDE.md`'s rule is "the guard wins until you can state precisely why it's wrong";
that burden is not discharged here and is not attempted.

The inventory-build excusal arm remains a *testable* proposition — among negative-FCF
names whose burn is dominated by an inventory build, is forward realized FCF
distinguishable? — and is logged to `TODO.md` with that measurement stated, not shipped.

## §2 Record and render the pre-clamp stance

### 2.1 What the 2026-08-20 briefs actually contain

Measured from the shipped JSON, not inferred from the code path:

| | stance | conviction | confidence | `stance_clamped` | `conviction_capped` |
|---|---|---|---|---|---|
| LULU | HOLD | MEDIUM | **1.0** | false | false |
| HDSN | AVOID | LOW | **1.0** | **true** | **false** |

This refutes two plausible readings, including the one that first motivated this
section:

- **HDSN's "conviction Low" is not a thin-data artifact.** Confidence is 1.0 and
  `conviction_capped` is false — no guard altered it. The model returned LOW itself.
- It is equally wrong to argue LOW *must* imply `confidence < 0.45`. The gate clamp
  caps conviction to MEDIUM, and capping a LOW leaves it LOW, so `conviction_capped`
  stays false. Neither cap fired.

The "one label doing three jobs" framing was therefore overstated: on these two
names both `confidence` and the conviction caps were inert.

### 2.2 The defect that survives

`apply_guards` (`assess.py:686`) assigns `call.stance` **in place**, and `orig_conv`
is a local that is discarded. `clamp_note` records which *gates* tripped, never what
the model said before they did. So **the model's pre-clamp stance is destroyed and is
unrecoverable from the brief.**

HDSN renders "Avoid · Auto-downgraded: tripped negative_fcf gate" with the pre-clamp
*rationale* quoted, but a reader cannot tell whether the model itself said HOLD or
BUY. That is the difference between "the model agreed and the gate confirmed it" and
"the gate overruled the model" — the most decision-relevant fact about a clamped
call, and today it is thrown away.

### 2.3 Change

This **does** require a schema change. `ScreeningCall` is serialized via `asdict`
into the brief JSON, so it is a persisted-format change.

- `research/models.py` — `ScreeningCall` gains `model_stance: Optional[str]` and
  `model_conviction: Optional[str]`, Python-owned. `_screening_call` never parses
  them (the `decided_without` precedent); old JSON reads back as `None` → lines
  omitted.
- `research/assess.py:apply_guards` — assign both before the overwrites.
- Four render sites, which must agree — an earlier draft said two:
  `research/report.py:_call_md`; `bot/report/viewmodel.py:call_one_liner`;
  `bot/report/sections.py` **HTML pill and text digest**; (`bot/report/png.py`
  renders the label only and is unaffected).

Deliberately not added: any LLM-supplied probability or payoff estimate. An invented
number carries no information.

Target render for HDSN, every value real (§2.1):

```
- **Call:** Avoid  ·  the negative_fcf gate overrode the model's Hold
- **Conviction:** Low (the model's own; no cap applied)
- **Data confidence:** 1.00
- _Model's pre-clamp view: ...balanced rather than clearly favorable._
```

Lines are omitted when nothing was clamped or capped.

---

## §3 Retire the textsim render and disable the flag

Per §0.4 the flag has never been able to fire and the rendered line is false on every
brief.

- `research/report.py` — drop the "Filing-text change (Lazy Prices)" section.
- `text_similarity` **stays** in the brief JSON (`report.write` uses `asdict` on
  `QualitativeAssessment`, independent of `to_markdown`). A future cross-sectional
  implementation will want the history.
- `config.yaml` — set **`enabled: false`** on the `filing_text_change` block, not
  comment it out. `_flag_block` honours `enabled: false`, and this keeps the
  threshold, its rationale and the kill-reason in one place; commenting out would
  delete the only written record of `max_similarity: 0.7` and desynchronize the
  shipped config from two tests that hardcode it. Record the measured 0.897 floor
  and the missing producer inline.
- `scoring.py` `KNOWN_FLAGS`, the glossary entry and `theme.FLAG_DESCRIPTIONS`
  **must all stay** — `test_scoring_names.py` binds them by set equality and
  cardinality; removing any breaks CI three ways.
- **Both** user-facing descriptions must stop claiming the flag works.
  `bot/glossary.py` currently says "Fires on LOW text similarity", and
  `bot/report/theme.py` carries the same claim in the flags legend. `/explain` is
  user-facing; leaving those unchanged tells a user the screener watches for filing
  rewrites when it does not. Both gain a DORMANT note.
- `research/textsim.py` — docstring records the measured 0.897 cross-company floor.
- `tests/test_flag_producers.py` — update the xfail reason, which currently implies
  an XPASS means the flag is live.
- `TODO.md` §2a — update rather than leave it describing a flag that is now also
  config-disabled.

Not attempted: stopword stripping, IDF weighting, a percentile reference
distribution, or the missing producer. Those are a real feature with their own
evidence requirement; logged to `TODO.md`.

---

## §4 Verification

Gate order is `uv run ruff check src tests` then `uv run pytest` — the exact CI
commands, lint first.

### New tests

- **Sign convention, both directions** — a build (negative on the harness path) and a
  liquidation (positive), from fixture DataFrames. This is the §0.1 hazard; it fails
  loudly if the convention flips.
- **Date-keyed alignment** — a filer whose cash-flow series is shorter than
  `fiscal_period_end` still pairs the right year's inventory against the right year's
  revenue.
- **Row-count abstention** — zero rows and duplicate rows both yield `None`.
- **Coverage unchanged** — `coverage()` for a fixture snapshot is identical before and
  after the new `Statements` fields (the `_NON_SIGNAL_FIELDS` guard).
- **Scoring untouched** — `composite`, `scored`, `passed` and `flags` are identical
  with and without the new fields. With the flag cut this is the whole scoring story.
- **Context-line abstention** on missing data; correct rendering with it.
- **Reconciliation caveat fires** — a filer whose balance-sheet change and cash-flow
  line diverge beyond the threshold (the LULU FX shape, +258.7M vs +188.7M) renders the
  "not the same quantity" caveat; a filer where they agree does not.
- **DIO arithmetic** — HDSN FY2024 205 days, FY2025 269 days from real inputs.
- **Haystack exclusion** — copy `tests/research/test_assess.py::test_reverse_dcf_line_excluded_from_haystack`.

### Existing tests that must be updated

Named, because each is an exact-string or cardinality assertion:

- `tests/test_scoring_names.py` — `len(KNOWN_FLAGS)` 14 → 15.
- `tests/test_filing_text_change_flag.py` — `test_metric_does_not_change_composite`
  and `test_disabled_config_is_byte_identical_card` both read the real `config.yaml`
  and assert the flag fires; rewrite to inject the block explicitly.
- `tests/research/test_report.py::test_brief_renders_the_similarity_line` — invert.
- `tests/test_screening_call_report.py` — `test_clean_call_badge_and_block`,
  `test_clamped_demotes_rationale`, `test_conviction_capped_note_when_not_clamped`.
- `tests/test_screening_call_viewmodel.py::test_call_one_liner` and
  `tests/test_screening_call_integration.py` — both assert the same exact one-liner.

### Live verification

`/deep HDSN` and `/deep LULU` re-run with `--refresh`, confirming the rendered
figures match §0.2 exactly, plus `/deep FISV` as the third brief. Per
`deep-live-verification-is-flaky`, the nested `claude` CLI fails unpredictably —
budget retries.

### Acceptance criteria

With the flag cut there is no firing rate to calibrate and nothing that moves a score,
so the evidence bar is reconciliation, not rank IC. The audit doc records:

- the balance-sheet / cash-flow / write-down identity closing (§0.1) on n >= 10 filers,
  including at least three international filers where FX is a reconciling item;
- the rendered line for HDSN and LULU checked against the filed statements by hand;
- an explicit statement that the line is descriptive and that no conclusion about
  HDSN's cash generation is asserted by it (§0.2).

Live verification: `/deep HDSN` and `/deep LULU` re-run with `--refresh`, plus
`/deep FISV` as the third brief. Per `deep-live-verification-is-flaky` the nested
`claude` CLI fails unpredictably — budget retries.

Evidence lands in `docs/audits/2026-08-21-inventory-context-line.md` — the **tracked**
audit tree, not `docs/superpowers/specs/`, which is gitignored and has already lost two
enablement artifacts.

## §5 Out of scope

- The refrigerant supply/demand and inventory-repricing model (quantity × type ×
  acquisition cost × forward price). Not in XBRL; needs commodity price feeds.
- Any advisory flag. Cut deliberately in §1.1 — the "inventory fully explains the
  burn" condition encodes the §0.2 error.
- Wiring `_xbrl_facts.py` / the backtest. **Not needed for this change** — a scope
  boundary, not a standing exemption. Required if the inventory split is ever proposed
  as a gate arm or a scoring leg.
- The `negative_fcf` inventory-build excusal arm (§1.6) — logged to `TODO.md` with its
  measurement stated.
- Brand/alt-data signals (search trends, app ranks, social engagement). No reliable
  free source, and as non-filing text they could not be grounded.
- Any change to `negative_fcf`, its clamp, or any weight.
