# ROIC on the FMP-gated path — design (2026-08-10)

**Status:** approved in outline by the owner 2026-08-10. Scope and evidence method are the
owner's calls (§0).

**Tracked, not `docs/superpowers/specs/`** (gitignored, 0 tracked files): this changes the live
**`moat`** sub-score, and `CLAUDE.md` requires tracked reasoning for anything that moves live
scoring. (An earlier draft said "quality/moat". `roic` is **not** a quality leg — `_quality_legs`
at `scoring.py:488` is roe / net_margin / interest_coverage / debt_to_equity. Moat only.)

---

## 0. The two decisions already made

| decision | choice | why |
|---|---|---|
| **Scope** | Fix the extraction cause, then retire the proxy — not a bare keep-or-drop | The proxy exists *because* real ROIC is unavailable on the gated path. That unavailability turned out to be a fixable extraction gap, not a data limit. |
| **Evidence** | **Agreement** against FMP's real ROIC, not forward-return rank IC | "Is this number right?" is answerable at n=591 paired observations on disk. "Does it predict returns?" is not answerable at ~42 large caps — `CLAUDE.md`'s design premise says so, and a null there would mean *no power*, not *no problem*. |

---

## 1. What is wrong today

`scoring.py:79` and `:523` both read `m.roic_5y_avg if m.roic_5y_avg is not None else m.roic`
as one of three legs of **`moat`** — weight **0.18**, tied for the highest of the seven
sub-scores. Durable high ROIC is the leg's whole thesis.

FMP supplies real ROIC (`returnOnInvestedCapitalTTM`). FMP's free tier gates many symbols
per-symbol (402 "Special Endpoint"), and when it does, the chain falls back to Finnhub — which
publishes **no ROIC**. `data/sources/finnhub.py:193` substitutes Return on *Investment*:

```python
roe=_pct(m.get("roeTTM")), roic=_pct(m.get("roiTTM")),
```

### 1.1 The proxy overstates ROIC, measured

The store retains per-source `raw` payloads, so both numbers survive for names where **both**
sources answered. That makes the substitution error directly measurable with no fetching:

| | |
|---|---|
| paired observations | **591** across 21 tickers |
| proxy **overstates** true ROIC | **541 / 591 (92%)** |
| median relative error | **+26.6%** |
| median absolute error | **+3.92 pp** |
| worst | GOOGL **+164%**, AMZN **+97%**, NVDA **+67%**, AAPL +39%, META +38% |

**Direction matters and it is the opposite of the intuition.** An earlier session hypothesised
ROI would *understate* ROIC (a bigger denominator including idle cash) and so penalise
cash-rich compounders. It **overstates**, and overstates most for the mega-cap tech
compounders — it *flatters* the names that already score well, inflating the joint
highest-weighted sub-score for them. Recorded because the wrong intuition is the natural one.

### 1.2 A second defect, arguably worse: no durability

**All 831** finnhub-only snapshots carry `roic_5y_avg = None`. The leg prefers a multi-year
average *because moat is about durability* — a single quarter's return says nothing about a
moat. On the fallback path the leg silently degrades to a point-in-time figure, so even a
*correct* ROIC there would be a materially weaker signal than on the FMP path. The proxy is
wrong on **both** axes: wrong metric, and no time dimension.

### 1.3 Why the honest fix was blocked

ROIC is computable from data already in the snapshot:

```
ROIC = NOPAT / invested capital = operating_income x (1 - t) / (total_debt + total_equity)
```

Tested against FMP's real ROIC on 186 comparable observations:

| estimator | median \|relative error\| vs FMP ROIC |
|---|---|
| computed from stored statements | **16.9%** |
| Finnhub `roiTTM` proxy | **29.2%** |

Computed wins on 13 of 16 tickers. The three losses are AMD and DIS (close calls) and **JPM —
a bank, where ROIC is structurally undefined and the scorer is already supposed to abstain**
(§4.3).

But on the actual failing path only **21 of 831** snapshots are computable, because
`Statements.total_equity` is missing in **97%** of them (and 68% of FMP-led ones).

**Root cause — and it is a coherent design, not an oversight.** An earlier draft of this spec
claimed `_edgar_facts.py` "declares `total_equity` at line 63 and never assigns it". **That was
wrong**: line 63 is `total_debt`, and `EdgarFinancials` has **no `total_equity` field at all**
(`grep total_equity src/shortlist/providers/_edgar_facts.py` → nothing). The declared-but-unused
field is `Statements.total_equity` (`data/models.py:74`) — a different class in a different
module. The draft conflated them.

What is actually true, per `docs/STATEMENTS_MERGE.md:22/39/167` and the comment at
`data/sources/edgar.py:239`: EDGAR deliberately does **not** extract equity, and **the merge
layer backfills it from FMP by fiscal year**. That design is sound on its own terms. It fails
here for a reason it did not anticipate: **on the FMP-gated path there is no FMP payload to
backfill from**, so no source supplies equity at all.

The related "extracted but consumed nowhere" line lives in **`TODO.md:523`**, not `CLAUDE.md`
(zero hits there — it was removed in `7c4334d`'s 1484→320-line shrink). `ASSESSMENT_GAPS.md:434`
separately calls `total_equity` "consciously omitted", but that is scoped to the **research
brief's** financial-series table, not to extraction; its stated reason — equity "goes negative
on buyback compounders" — is precisely the case §5.4's `invested_capital <= 0` guard handles.

So this work **extends** a documented design rather than overriding a guard: teach EDGAR to
supply equity so the gated path has a source, instead of relying on an FMP backfill that by
definition cannot fire when FMP is gated.

---

## 2. Yield — why this is worth building

Once `total_equity` lands, a snapshot is computable if it also has `operating_income` and
`total_debt` (already extracted). Measured over the failing path:

| | count | share |
|---|---|---|
| finnhub-only-fundamentals snapshots | 831 | — |
| **computable after the `total_equity` fix** | **466** | **56%** |
| still blocked by `operating_income` | 340 | 41% |
| no statements at all | 25 | 3% |

**19 tickers gain a real ROIC**: HD, QCOM, TXN, MCD, ORCL, CMCSA, LMT, CAT, PG, V, MO, CRM,
ABBV, UNH, PEP, KO, JPM, WMT, COST.

**This is the load-bearing scoping fact: Stage 2 is justified by Stage 1a alone.** It does not
depend on Stage 1b finding anything fixable, which is what makes 1b safe to run as an honest
diagnostic that may conclude "structural, no fix".

**Caveat — 466 is a PROJECTION, not a measurement.** It uses `total_debt` presence as a stand-in
for equity extractability, and the two use different helpers with different strictness:
`_sum_concepts` accepts ≥1 of 3 debt components per column, while `_series` requires one row
complete across **every** instant column (a 3-instant frame with one blank equity cell yields
`[]`). So the realised count can be lower. Task 1's live check must report the **actual** count
of snapshots gaining a usable equity series, and §2's table is superseded by that number.

---

## 3. Stage 1a — extract `total_equity` (fully diagnosed, one sharp landmine)

### 3.1 It is a concept FAMILY, and `standard_concept` cannot express it

Live-probed 2026-08-10 (raw `concept` column, `us-gaap_` prefix stripped):

| ticker | equity concepts present |
|---|---|
| UNH | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` **only** |
| CSCO | `StockholdersEquity` |
| KO | **both** |
| JPM | `StockholdersEquity` |

No single concept works. `standard_concept` is worse than useless: CSCO/KO expose
`AllEquityBalance`, UNH exposes `AllEquityBalanceIncludingMinorityInterest` — inconsistent
across filers. This independently confirms the **raw-`concept`-first** rule that `CLAUDE.md`
and `docs/audits/2026-07-31-edgar-concept-match.md` already require, and that `_row_net_income`
(`_edgar_facts.py:153`) implements as the pattern to copy.

### 3.2 THE LANDMINE — substring matching reads total assets as equity

**All four issuers also carry `us-gaap_LiabilitiesAndStockholdersEquity`**, which contains
`StockholdersEquity` as a substring and equals **total assets**. A substring or
`.str.contains` match silently returns a value ~3x too large for a normal operating company and
~10x for a bank, making ROIC 3–10x **too small** — a plausible-looking number, wrong in a
direction that would mark good businesses down.

Therefore, non-negotiably:

1. **Exact** concept equality, never substring/`contains`.
2. **Exclude `*Abstract` rows** (`StockholdersEquityAbstract`,
   `LiabilitiesAndStockholdersEquityAbstract`) — presentation headers with no values.
3. **Explicitly exclude `us-gaap_LiabilitiesAndStockholdersEquity`** and assert against it in a
   test, so a future refactor toward substring matching fails loudly.

### 3.3 Priority order, not a sum

`total_debt` legitimately **sums** components (`_sum_concepts` over long-term + current
portion + short-term). Equity must **pick one**: KO reports both variants and summing them
double-counts the whole balance-sheet equity.

Priority: **`StockholdersEquity` (parent-only) first, then
`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`.**

**This ordering was reversed in an earlier draft, and the reversal was wrong.** FMP supplies
`totalStockholdersEquity` (`fmp.py:137`), which is **parent-only**. EDGAR outranks FMP in
`_merge_statements`, so preferring the NCI-inclusive tag would (a) make the same
`Statements.total_equity` field mean different things depending on which source won, and (b)
**pre-empt the FMP backfill on the ~173 EDGAR-spine snapshots that currently carry FMP equity**,
silently changing existing merged values. Matching FMP's definition keeps one meaning for one
field and makes the change purely additive.

Falling back to the NCI-inclusive tag still lets **UNH** resolve, since that is the only equity
tag UNH reports. Where the two differ the NCI portion is typically small; where a filer reports
only the inclusive tag it is the best available.

### 3.4 Documentation this change obliges

`total_equity`'s absence from `EdgarFinancials` is *documented* in three places, so adding it
means correcting them — leaving them stale is how the next reader inherits a false model:

- `data/sources/edgar.py:239` — the comment *"gross_profit/total_equity aren't in
  EdgarFinancials"* becomes half wrong (`gross_profit` still is not).
- `docs/STATEMENTS_MERGE.md:22/39/167` — the merge table and the FMP-backfill narrative.
  EDGAR outranks FMP, so post-change EDGAR **supplies** equity rather than receiving it for the
  EDGAR-spine snapshots. §3.3's parent-only priority is what keeps that a like-for-like swap
  rather than a redefinition.
- `TODO.md:523` — the "extracted but consumed nowhere" parked observation. `total_equity` gains
  both an extractor and a consumer; `operating_margin`/`current_ratio` remain genuinely unused
  and stay.

### 3.5 Unit

`_edgar_facts.py`: a `_row_by_exact_concept(df, concepts: tuple[str, ...])` helper returning the
first exactly-matching non-abstract row in priority order, then
`fin.total_equity = _series(...)` alongside the existing `total_debt`/`total_assets`
assignments, using the same `bal_inst` instant-column alignment.

**Correction to an earlier draft: `_edgar_facts.py` is NOT shared with the XBRL backtest.** Its
only non-test importer is `data/sources/edgar.py:191`. The XBRL backtest uses the sibling
`providers/_xbrl_facts.py`, which has its **own** `total_equity` (`:215`, `:261`) and its own
`_roic_series` (`:286`). The genuinely shared leaf is `_gaap_tags.py`. `CLAUDE.md` describes
`_edgar_facts.py` as shared with the backtest; that is imprecise and this design does not rely
on it.

The practical consequence is *reassuring*: adding `total_equity` **cannot perturb any backtest
path or golden value**. It also means the fix does not benefit `--source xbrl` — that path
already computes its own ROIC, with a **per-year effective tax rate** clamped to [0, 0.5] and a
0.21 statutory fallback (`_xbrl_facts.py:286-307`), where §5.2 below uses a flat 0.25. The two
axes will therefore differ by roughly `(1-0.25)/(1-0.21) = 0.949`, ~5%. Noted so nobody reads
the gap as a bug; unifying them is out of scope.

---

## 4. Stage 1b — DIAGNOSE `operating_income`, do not assume

`operating_income` is missing in 41% of the failing path. An earlier reading of this design
attributed that to `standard_concept` drift. **That is not established and is partly wrong.**
The store splits it into three distinct populations:

| pattern | tickers | reading |
|---|---|---|
| always missing, **banks** | BAC, GS, WFC (SIC 6021/6211) | **Not a bug.** JPM's raw income-statement concepts are `[]` — banks report net interest income, not operating income. |
| always missing, **non-financials that do report it** | CVX, XOM (2911), HON (3724), IBM (3570), JNJ / LLY / MRK (2834) | **Real gap, mechanism unknown.** Three pharma at the same SIC is a striking pattern. |
| **inconsistent within one ticker** | DIS 23/34 days, JPM 27/34, NKE 23/34 | **Most concerning.** Same filing, different result across capture days ⇒ non-determinism or edgartools drift across the window, not a tagging quirk. |

**Deliverable is an audit note, not a fix.** Determine for one representative of each bucket
what the income-statement `concept` column actually contains. Only then decide whether a family
extension is warranted. A legitimate outcome is **"structural for banks, no fix"** — and
because §2 justifies Stage 2 without this tranche, that outcome costs nothing.

Guessing here is precisely the read-past-the-evidence pattern `CLAUDE.md`'s 2026-07-26
postmortem records as costing four retracted conclusions.

---

## 5. Stage 2 — compute ROIC, retire the proxy

### 5.1 Where it belongs

`bridge.py:snapshot_to_metrics` is the adapter that turns a `TickerSnapshot` into
`StockMetrics`, and it already derives fields from `statements`. The computation goes **there**,
not in `finnhub.py` — it is not a Finnhub concern, it is a "derive ROIC when the fundamentals
sources did not supply one" concern, and doing it in the adapter means it works no matter which
source chain was gated.

`finnhub.py:193` changes to `roic=None`, with a comment pointing at this document. Finnhub
stops claiming to supply a metric it does not publish.

### 5.2 The computation

```
invested_capital = total_debt[i] + total_equity[i]
nopat            = operating_income[i] * (1 - TAX_RATE)
roic[i]          = nopat / invested_capital        # requires invested_capital > 0
```

**Precedence: never override a real ROIC.** Compute only when `m.roic is None` after the merge,
so an FMP-supplied value always wins. This makes Stage 2 a strict improvement — it can only
fill gaps, never overwrite better data.

**Multi-year average → the durability dimension (§1.2).** Compute per fiscal year and average,
populating `roic_5y_avg` when **≥2** years resolve. Depth is bounded by `total_debt` (~2–3 years
from EDGAR, vs FMP's 5), so this is a 2–3 year average, not 5 — it must be named honestly in the
field's documentation, since the config threshold band was calibrated against FMP's 5-year
figure.

**The three series MUST be paired by FISCAL YEAR, never by list position.** This is the exact
bug class `CLAUDE.md` records for the statements merge — *"backfill from lower sources is
re-indexed by fiscal year, never by list position (a positional backfill pairs mismatched years
silently)"*. The series here have **different lengths**: UNH carries `fiscal_years [2025, 2024,
2023]` with `operating_income` and `total_equity` at 3 entries but `total_debt` at only **2**.
**Measured honestly: positional zipping would work today.** Across 1403 stored snapshots with a
`fiscal_years` list, **0** have an interior `None` gap in these three series — every one is a
newest-first prefix. So this is a **defensive** requirement, not a live bug, and the spec says so
rather than implying a defect that isn't there.

**And a correction: true fiscal-year pairing is NOT implementable here, so do not claim it.**
`Statements` carries **one** year spine. The balance-sheet series (`total_debt`, `total_equity`)
have no year labels of their own — `edgar.py:217` derives `fiscal_years` from the *income
statement's* `fiscal_period_end`, and `_instant_columns`' dates are discarded by `_series`.
Indexing all three by the position of `fiscal_years[i]` is therefore **a positional zip with a
year label attached, plus a skip for missing values** — which is the honest description of what
ships.

That is correct today (0 interior gaps across all 1684 stored snapshots, and the measured
`fy=3 / oi=3 / debt=2` shape is a clean newest-first prefix). Keep the None-skip, because it is
free and it degrades safely. But **name the helper and its test for what they do** — skip years
where any input is missing — rather than for a year-pairing guarantee the data model cannot
provide. Genuine per-series year labels would be a separate change to `Statements`.

**Tax rate:** flat **`TAX_RATE = 0.25`**, config-exposed. We have no tax-expense field, and
deriving an effective rate from `net_income`/`operating_income`/`interest_expense` is noisy
(non-operating items). 25% ≈ 21% US federal + state, the standard screening convention. Part of
the 16.9% residual error is this assumption, and that is acceptable against a 29.2% alternative.

### 5.3 Financials must keep abstaining

ROIC is structurally meaningless for banks/insurers/REITs, and `sectors.py:resolve_bucket`
already masks the ROIC leg for those buckets. Two names in §2's gain list are in scope of that
mask — **UNH (SIC 6324, insurer)** and **JPM (6021, bank)**.

**Stage 2 must not defeat the mask.** The sector abstention runs in `scoring.py` on the leg, not
on the metric, so supplying a computed `roic` for UNH/JPM is harmless *provided* the mask still
fires. **This needs a test**, not an assumption: assert that a financial-bucket ticker with a
computed `roic` still abstains on the ROIC leg. JPM being one of the three cases where the
proxy beat the computed value (§1.3) is a symptom of exactly this — the number is meaningless
there either way.

### 5.4 Guard against the failure mode this replaces

`invested_capital <= 0` ⇒ `None`, never a negative or explosive ROIC. Thin-equity buyback
compounders can drive equity negative; `total_debt + total_equity` can then approach zero and
produce an absurd ratio. Abstain rather than emit a number — the same abstain-don't-guess
discipline the 13F share-count work used.

---

## 6. Verdict and its bar

Pre-registered here, before measurement, so it cannot be reinterpreted afterwards. **An earlier
draft of this section was rejected in review for validating the wrong quantity on the wrong
population; what follows is the corrected bar.**

### 6.1 Measure the quantity the scorer actually reads

`scoring.py:79` and `:523` read **`roic_5y_avg` if present, else `roic`**. §5.2 populates
`roic_5y_avg` whenever ≥2 years resolve — which is the *normal* case on the target path (the
measured `fy=3 / oi=3 / debt=2` shape appears in 791 of 1684 snapshots). **So the leg will read
the 2–3 year average almost always, and a bar on spot `roic` alone would leave the shipped
quantity unvalidated.**

The bar therefore covers **both**:

| quantity | compared against |
|---|---|
| computed spot `roic` | FMP `returnOnInvestedCapitalTTM` |
| computed `roic_5y_avg` (2–3 yr) | FMP `roic_5y_avg` (5 yr) |

The second comparison is not apples-to-apples by construction — a 2–3 year window against a
5-year one — and that is exactly why it must be measured rather than waved through as "a
documented approximation". Review measured a **+11.0% median level shift** (p10 −13.1%, p90
+32.1%) on the 420 snapshots where both exist: a systematic *upward* shift into a band
calibrated on the 5-year figure. **Report it; do not assume it cancels.**

### 6.2 Re-derive BOTH estimators on the SAME post-fix population

The proxy's 29.2% is a **stored constant measured on today's population**. Stage 1a changes that
population: review found ~**343** additional rows become computable once equity lands (EDGAR
spine, `operating_income` present, equity absent, FMP truth present). Comparing a fresh computed
number against an old-population constant **is not a bar**.

So Task 4 must recompute **proxy and computed side by side on the identical post-fix rows**, and
report **per statements-provenance**, because pooling hides the path being fixed — review
measured `['fmp']` at 8.2% (n=249) against `['edgar','fmp']` at 17.3% (n=173), and the new rows
are a third configuration (EDGAR-extracted equity) likely at or above 17.3%. A pooled headline
would be dominated by FMP-led rows this change does not touch.

### 6.3 Report signed bias, not only magnitude

The entire case against the proxy in §1.1 is **direction** — 92% overstate, median +26.6%.
Validating the replacement on magnitude alone would trade a known bias for an unstated one.
Review measured the computed estimator overstating in **267/422 (63%), median signed +6.3%** —
same direction, ~4× smaller. **Both signed and absolute medians go in the results.**

### 6.4 The bar

**PASS requires all three:**

1. computed median |relative error| **< the proxy's, re-derived on the same rows** (§6.2);
2. computed median **signed** error strictly smaller in magnitude than the proxy's `+26.6%`;
3. the `roic_5y_avg` level shift (§6.1) **reported**, with an explicit judgement on whether it
   is small enough to leave `thresholds.roic` untouched.

**If (1) or (2) fails**, the finding is that `roic` should stay `None` on the gated path — see
the corrected description of that outcome below. **If only (3) is troubling**, the options are to
ship spot `roic` without the multi-year average, or to re-tune the band with its own measured
case; picking either is a decision, not a default.

### 6.5 What "leave it None" actually does

**A dropped LEG does not redistribute composite weight.** `_eval_subscore` (`scoring.py:421`)
returns `mean(... for lg in present)` — the mean spans fewer legs. Only a whole *sub-score*
abstention redistributes. So an unknown-bucket name losing `roic` gets **`moat` as a 1–2 leg
average (gross margin ± stability) still carrying the full 0.18** — a reweighting of what moat
means, not a clean withdrawal. Honest, but describe it correctly.

### 6.6 Sizing obligation, with the expected answer stated up front

Confirm whether any name crosses `validity.min_scored_weight` and flips `scored`/`passed`, since
**an unscored name cannot pass or rank**. The expected answer is **near-zero flips**, and the
reasoning should be published alongside the count: known buckets need ≥2 of 3 moat legs
(`min_valid_leg_fraction: 0.5`) but have all three masked anyway, and unknown buckets need only
`unknown_min_present_legs: 1`, so `moat` will not abstain — it just loses the ROIC dimension.
Report the count regardless; a prediction is not a measurement.

### 6.7 Retract the exploratory numbers or state their filter

§1.3's "16.9% on 186 comparable observations, 13 of 16 tickers" came from an ad-hoc exploratory
script. Review could not reproduce it, getting **n=422 / 24 tickers, proxy 28.9%, computed
9.7%** under the natural filter. The 28.9% replicates §1.1's 29.2% closely enough; the rest does
not. **Task 4 supersedes those figures.** Until it runs, §1.3's table should be read as
directional only — the shipped measurement is the one that counts.

## 7. Testing

**Stage 1a** — exact-match returns the right row when only the NCI-inclusive tag exists (UNH
shape); when only the parent-only tag exists (CSCO shape); priority order when **both** exist
(KO shape, and the result must equal the NCI-inclusive value, not their sum);
**`LiabilitiesAndStockholdersEquity` is never matched** even though it contains the substring;
`*Abstract` rows are skipped; a missing family ⇒ `None`, not 0.0. Fixture DataFrames follow the
existing `_edgar_facts` test shapes.

**Stage 2** — computed only when `m.roic is None` (an FMP value always wins);
`invested_capital <= 0` ⇒ `None`; `roic_5y_avg` populated at ≥2 resolvable years and `None` at
1; a financial-bucket ticker with a computed `roic` still abstains on the ROIC leg (§5.3); and
the whole path **byte-identical** for a snapshot whose fundamentals already carry a real ROIC.

**Regression** — `--source xbrl` backtest still runs, since `_edgar_facts.py` is shared.

Verification per `docs/audits/2026-08-06-discovery-breadth-plan.md` §7, lint a hard gate:

```bash
uv run ruff check src tests
uv run pytest -q          # 2522 passed / 6 skipped at 0f47068
```

---

## 8. Out of scope

- **Fixing `operating_income`** — Stage 1b diagnoses only; any fix is its own change once the
  mechanism is known.
- **A 5-year ROIC average from EDGAR.** `total_debt` depth caps this at 2–3 years. Extending
  balance-sheet history is separate work.
- **An effective tax rate per issuer.** Flat 25%, stated (§5.2).
- **Forward-return validation.** Deliberately excluded per §0 — untestable at this universe
  size, and a null result would be misread as vindication.
- **Touching the `thresholds.roic` band.** It was calibrated against FMP's 5-year average; a
  2–3 year EDGAR average feeding the same band is a known, documented approximation. Re-tuning
  needs its own measured case.
