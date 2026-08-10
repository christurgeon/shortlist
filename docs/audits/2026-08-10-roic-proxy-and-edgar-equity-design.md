# ROIC on the FMP-gated path — design (2026-08-10)

**Status:** approved in outline by the owner 2026-08-10. Scope and evidence method are the
owner's calls (§0).

**Tracked, not `docs/superpowers/specs/`** (gitignored, 0 tracked files): this changes live
`quality`/`moat` sub-scores, and `CLAUDE.md` requires tracked reasoning for anything that moves
live scoring.

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
`Statements.total_equity` is missing in **97%** of them (and 68% of FMP-led ones). Root cause,
verified: **`_edgar_facts.py` never assigns `total_equity` at all.** It is declared at line 63
and there is no `fin.total_equity = ...` anywhere in the file. `CLAUDE.md` lists it under
"extracted but consumed nowhere" — it is in fact *neither* extracted nor consumed.

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

Priority: **`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` first,
then `StockholdersEquity`.** Invested capital should reflect all capital funding operations,
including the non-controlling interest, and that ordering also makes UNH (which has only the
NCI-inclusive tag) resolve to a real value rather than `None`. Where a filer reports only the
parent-only tag, that is the best available and is used.

### 3.4 Unit

`_edgar_facts.py`: a `_row_by_exact_concept(df, concepts: tuple[str, ...])` helper returning the
first exactly-matching non-abstract row in priority order, then
`fin.total_equity = _series(...)` alongside the existing `total_debt`/`total_assets`
assignments, using the same `bal_inst` instant-column alignment.

It is a **shared leaf** (`EdgarSource` + the `--source xbrl` backtest both consume it), so the
fix benefits the backtest too — and any change must be checked against both consumers.

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

It is still the right call, because the failure is silent and the guard is nearly free: an
interior gap would pair 2024 debt against 2023 operating income and report a plausible wrong
number, with nothing to catch it. Index each series by its fiscal year via
`Statements.fiscal_years`, compute ROIC only for years where **all three** resolve, and require
≥2 such years. A test must cover an interior-gap series — synthetic, since the store has none.

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

Pre-registered here, before measurement, so it cannot be reinterpreted afterwards:

**Metric:** median |relative error| against FMP's `returnOnInvestedCapitalTTM`, on snapshots
where both the computed value and FMP's value exist.

**Bar:** the computed estimator must beat the proxy's **29.2%** median error on the same
paired set. The provisional read is **16.9%**, but that was computed with a point-in-time
denominator over 186 observations and must be re-derived by the shipped code path.

**If it does not clear the bar**, the finding is that `roic` should be `None` on the gated path —
the moat leg abstains and redistributes weight, which is the scorer's documented behaviour and
an honest outcome, not a regression. **Committing to that in advance is what stops this becoming
a search for a flattering number.**

**Sizing obligation either way:** dropping or adding `roic` changes each card's scored weight.
Confirm whether any name crosses `validity.min_scored_weight` (0.25) and flips
`scored`/`passed`, since **an unscored name cannot pass or rank**. Report the count; do not
discover it in production.

---

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
