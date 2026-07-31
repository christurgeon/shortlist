# EDGAR diluted-shares/EPS concept-first matching — full-universe live verification

**Date:** 2026-07-31 · **Change:** `fix/edgar-diluted-shares` (`db79d2f`) — row selection for
`diluted_shares`/`diluted_eps` in `src/shortlist/providers/_edgar_facts.py` now matches the
raw us-gaap `concept` column first (value-aware: a concept row wins only if it yields a
complete series), falling back to the existing label scan. Design: `docs/PLAN_EDGAR_DILUTED_SHARES.md`
(revision 4, signed off, amended `491b6a1` after this verification). Committed evidence of
record per `CLAUDE.md`'s measure-first doctrine — this is the tracked surface, not a commit
message or a gitignored `docs/superpowers/specs/` artifact.

**Verdict: GO**, on the revised (21-ticker) expected-change set. The original 16-ticker
blast-radius estimate (7 shares + 9 EPS) was incomplete; the live before/after found 5 more
issuers changing (HON, MRK, XOM, JNJ, QCOM), all genuine improvements, none a code defect.
Clause 3 was re-run against the corrected 21-ticker set and passes: no ticker outside it moved.

## Method

Keyless (EDGAR only, no FMP quota). `set -a && . ./.env && set +a` for `SEC_IDENTITY`.
Universe enumerated from the real production accumulation store, exactly as
`docs/PLAN_EDGAR_DILUTED_SHARES.md` §Task 2 specifies:

```python
tickers = sorted({os.path.basename(os.path.dirname(p))
                  for p in glob.glob("/opt/shortlist/state/snapshots/*/*.json.gz")})
```

→ 42 tickers, matching the plan's own §Evidence table.

- **After:** `extract_financials` run on `fix/edgar-diluted-shares` @ `db79d2f`.
- **Before:** the same script run in a temporary worktree at `29f170f` (the signed-off-plan
  commit, immediately pre-Task-1) — `git worktree add /tmp/edgar-before 29f170f`, removed
  afterward.
- Outputs diffed **programmatically** (parsed-JSON dict equality per ticker), not by eye.
- Script (adds `net_income[:3]` to the brief's version, needed for the Part B cross-check):

```python
import glob, json, os, sys
sys.path.insert(0, "src")
from edgar import Company, set_identity
set_identity(os.environ["SEC_IDENTITY"])
from shortlist.providers._edgar_facts import extract_financials

tickers = sorted({os.path.basename(os.path.dirname(p))
                  for p in glob.glob("/opt/shortlist/state/snapshots/*/*.json.gz")})
out = {}
for tk in tickers:
    try:
        f = Company(tk).get_financials()
        try: sh = f.get_shares_outstanding_diluted()
        except Exception: sh = None
        ef = extract_financials(f.income_statement().to_dataframe(),
                                f.cashflow_statement().to_dataframe(),
                                f.balance_sheet().to_dataframe(), shares_diluted=sh)
        out[tk] = {"shares": ef.diluted_shares[:3], "eps": ef.diluted_eps[:3],
                   "net_income": ef.net_income[:3] if ef.net_income else []}
    except Exception as e:
        out[tk] = {"error": f"{type(e).__name__}: {e}"}
print(json.dumps(out, indent=1))
```

Repro:

```bash
cd /home/chris/shortlist
set -a && . ./.env && set +a
uv run --extra edgar python <script> > after.json      # on fix/edgar-diluted-shares

git worktree add /tmp/edgar-before 29f170f
cd /tmp/edgar-before
set -a && . /home/chris/shortlist/.env && set +a
uv run --extra edgar python <script> > before.json
cd /home/chris/shortlist
git worktree remove /tmp/edgar-before
```

CI: `uv run ruff check src tests` clean; `uv run pytest -q` → **2235 passed, 6 skipped, 19
deselected** (`tests/test_edgar_leverage_live.py`, `-m live`, not run — hits SEC).

## Root-cause split

- **Root cause A (7) — label mismatch, shares. FIXED.** `us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding`
  present with complete values; the label scan missed it (`'Diluted'` with no "shares" —
  COST/MSFT/ORCL/PEP/QCOM; `'Assuming dilution (in shares)'` — IBM; no "diluted" —
  VZ). All 7 go `shares=[]` → three real values.
- **Root cause B (9) — concept genuinely absent. OUT OF SCOPE, deferred.** CMCSA CVX GOOGL
  HON LMT MO MRK PG XOM. No `WeightedAverageNumberOfDilutedSharesOutstanding` tag at any
  label for these issuers; `diluted_shares` correctly stays `[]` before and after (byte
  identical on the shares field — confirmed in the table below). Note HON/MRK/XOM are *also*
  in the new EPS finding below (§ [R4]) — disjoint fields, same issuers.
- **Root cause C (9) — label mismatch, EPS computed fallback. FIXED.** `_row_diluted_eps`
  required `"per share"`; COST/MSFT/ORCL/PEP label theirs `'Diluted'` and miss. On a miss the
  old code computed `net_income[i] / shares_diluted_scalar` (a single TODAY's-count scalar
  divided into every year) — detectable as a long float. COST, DIS, IBM, MCD, MSFT, ORCL, PEP,
  UNH, VZ all flip to clean 2-dp as-reported values.
- **Root cause D/E (5) — [R4], found only by the live go/no-go, NOT in the original plan.**
  See below.

## Full 42-ticker before/after

`shares`/`eps` are `[FY0, FY1, FY2]`, newest-first, truncated to 3. `class`: `A` = shares
recovery, `C` = EPS computed→as-reported, `D` = EPS silently-empty→as-reported (new, [R4]),
`E` = EPS continuing-ops→total-ops row-pick correction (new, [R4]), `-` = unchanged.

| ticker | class | before shares | after shares | before eps | after eps |
|---|---|---|---|---|---|
| AAPL | - | [1.500e10,1.541e10,1.581e10] | same | [7.46,6.08,6.13] | same |
| ABBV | - | [1.773e9]×3 | same | [2.36,2.39,2.72] | same |
| ADBE | - | [4.27e8,4.497e8,4.591e8] | same | [16.7,12.36,11.82] | same |
| AMD | - | [1.636e9,1.637e9,1.625e9] | same | [2.65,1.0,0.53] | same |
| AMZN | - | [1.0827e10,1.0721e10,1.0492e10] | same | [7.17,5.53,2.9] | same |
| BAC | - | [7.6809e9,7.9358e9,8.0805e9] | same | [3.81,3.19,3.05] | same |
| CAT | - | [4.723e8,4.894e8,5.136e8] | same | [18.81,22.05,20.12] | same |
| CMCSA | - (B) | [] | [] | [5.39,4.14,3.71] | same |
| **COST** | **A+C** | `[]` | `[444803000, 444759000, 444452000]` | `18.2081, 16.5624, 14.1456` | `18.21, 16.56, 14.16` |
| CRM | - | [9.56e8,9.74e8,9.84e8] | same | [7.8,6.36,4.2] | same |
| CSCO | - | [3.998e9,4.062e9,4.105e9] | same | [2.55,2.54,3.07] | same |
| CVX | - (B) | [] | [] | [6.63,9.72,11.36] | same |
| **DIS** | **C** | [1.811e9,1.831e9,1.83e9] | same | `6.8493, 2.7454, 1.2998` | `6.85, 2.72, 1.29` |
| GOOGL | - (B) | [] | [] | [10.81,8.04,5.8] | same |
| GS | - | [3.176e8,3.336e8,3.458e8] | same | [51.32,40.54,22.87] | same |
| HD | - | [9.95e8,9.93e8,1.002e9] | same | [14.23,14.91,15.11] | same |
| **HON** | **D (B)** | [] | [] | `[]` | **`7.36, 8.71, 8.47`** |
| **IBM** | **A+C** | `[]` | `[948675228, 937161224, 922073828]` | `11.1661, 6.3489, 7.9079` | `11.17, 6.43, 8.14` |
| **JNJ** | **E** | [2.4294e9,2.4294e9,2.5604e9] | same | `11.03, 5.79, 5.20` | **`11.03, 5.79, 13.72`** |
| JPM | - | [2.7815e9,2.879e9,2.9431e9] | same | [20.02,19.75,16.23] | same |
| KO | - | [4.313e9,4.32e9,4.339e9] | same | [3.04,2.46,2.47] | same |
| LLY | - | [8.993e8,9.041e8,9.033e8] | same | [22.95,11.71,5.8] | same |
| LMT | - (B) | [] | [] | [21.49,22.31,27.55] | same |
| **MCD** | **C** | [716.4,721.9,732.3] | same | `11952819.65, 11478224.46, 11821608.04` | `11.95, 11.39, 11.56` |
| META | - | [2.574e9,2.614e9,2.629e9] | same | [23.49,23.86,14.87] | same |
| MO | - (B) | [] | [] | [4.12,6.54,4.57] | same |
| **MRK** | **D (B)** | [] | [] | `[]` | **`7.28, 6.74, 0.14`** |
| **MSFT** | **A+C** | `[]` | `[7453000000, 7465000000, 7469000000]` | `17.9457, 13.6632, 11.8256` | `17.95, 13.64, 11.8` |
| NFLX | - | [4.3439e9,4.3926e9,4.495e9] | same | [2.53,1.98,1.2] | same |
| NKE | - | [1.481e9,1.4876e9,1.5297e9] | same | [2.1,2.16,3.73] | same |
| NVDA | - | [2.4514e10,2.4804e10,2.494e10] | same | [4.9,2.94,1.19] | same |
| **ORCL** | **A+C** | `[]` | `[2914000000, 2866000000, 2823000000]` | `5.8638, 4.2701, 3.5920` | `5.83, 4.34, 3.71` |
| **PEP** | **A+C** | `[]` | `[1373000000, 1378000000, 1383000000]` | `6.0015, 6.9760, 6.6089` | `6.00, 6.95, 6.56` |
| PG | - (B) | [] | [] | [6.51,6.02,5.9] | same |
| **QCOM** | **A+E** | `[]` | `[1105000000, 1130000000, 1126000000]` | `5.01, 8.94, 6.52` | **`5.01, 8.97, 6.42`** |
| TXN | - | [9.13e8,9.19e8,9.16e8] | same | [5.45,5.2,7.07] | same |
| **UNH** | **C** | [9.11e8,9.29e8,9.38e8] | same | `13.2338, 15.8123, 24.5675` | `13.23, 15.51, 23.86` |
| V | - | [2.1e9,2.242e9,2.34e9] | same | [82.67,79.53,54.65] | same |
| **VZ** | **A+C** | `[]` | `[4231000000, 4223000000, 4215000000]` | `4.0591, 4.1376, 2.7450` | `4.06, 4.14, 2.75` |
| WFC | - | [3.2423e9,3.4676e9,3.7204e9] | same | [6.26,5.37,4.83] | same |
| WMT | - | [8.022e9,8.081e9,8.108e9] | same | [2.73,2.41,1.91] | same |
| **XOM** | **D (B)** | [] | [] | `[]` | **`6.70, 7.84, 8.89`** |

32 tickers byte-identical. 10 on the original plan's list (7 A, 9 C — COST/IBM/MSFT/ORCL/PEP/VZ
overlap A+C; DIS/MCD/UNH are C-only; QCOM is A+E). 5 changed outside the original plan (HON,
MRK, XOM = D; JNJ, QCOM = E — QCOM appears in both A and E).

## Go/no-go — all four clauses (revised 21-ticker expected-change set)

**Clause 1 — 7 root-cause-A tickers `shares=[]` → three real values.** **PASS.** COST, IBM,
MSFT, ORCL, PEP, QCOM, VZ.

**Clause 2 — 9 computed-EPS tickers, long-float → 2-dp as-reported.** **PASS.** COST, DIS,
IBM, MCD, MSFT, ORCL, PEP, UNH, VZ.

**Clause 3 (revised) — every ticker outside the 21 (7 A ∪ 9 C ∪ 5 [R4]) byte-identical.**
**PASS.** All 5 [R4] findings are accounted for by name (HON, MRK, XOM, JNJ, QCOM); no
sixth ticker moved.

**Clause 4 — hand-check the recovered VALUES via `net_income / diluted_eps ≈ diluted_shares`.**
**PASS**, cleanly, all 7 root-cause-A tickers × 3 years (21 data points), max deviation 0.58%:

| ticker | FY | net_income | diluted_eps | implied shares (NI/EPS) | reported diluted_shares | deviation |
|---|---|---|---|---|---|---|
| COST | 0 | 8,099,000,000 | 18.21 | 444,755,629 | 444,803,000 | −0.01% |
| COST | 1 | 7,367,000,000 | 16.56 | 444,867,150 | 444,759,000 | +0.02% |
| COST | 2 | 6,292,000,000 | 14.16 | 444,350,282 | 444,452,000 | −0.02% |
| IBM | 0 | 10,593,000,000 | 11.17 | 948,343,778 | 948,675,228 | −0.03% |
| IBM | 1 | 6,023,000,000 | 6.43 | 936,702,955 | 937,161,224 | −0.05% |
| IBM | 2 | 7,502,000,000 | 8.14 | 921,621,622 | 922,073,828 | −0.05% |
| MSFT | 0 | 133,749,000,000 | 17.95 | 7,451,197,772 | 7,453,000,000 | −0.02% |
| MSFT | 1 | 101,832,000,000 | 13.64 | 7,465,689,150 | 7,465,000,000 | +0.01% |
| MSFT | 2 | 88,136,000,000 | 11.80 | 7,469,152,542 | 7,469,000,000 | +0.00% |
| ORCL | 0 | 17,087,000,000 | 5.83 | 2,930,874,786 | 2,914,000,000 | +0.58% |
| ORCL | 1 | 12,443,000,000 | 4.34 | 2,867,050,691 | 2,866,000,000 | +0.04% |
| ORCL | 2 | 10,467,000,000 | 3.71 | 2,821,293,801 | 2,823,000,000 | −0.06% |
| PEP | 0 | 8,240,000,000 | 6.00 | 1,373,333,333 | 1,373,000,000 | +0.02% |
| PEP | 1 | 9,578,000,000 | 6.95 | 1,378,129,496 | 1,378,000,000 | +0.01% |
| PEP | 2 | 9,074,000,000 | 6.56 | 1,383,231,707 | 1,383,000,000 | +0.02% |
| QCOM | 0 | 5,541,000,000 | 5.01 | 1,105,988,024 | 1,105,000,000 | +0.09% |
| QCOM | 1 | 10,142,000,000 | 8.97 | 1,130,657,748 | 1,130,000,000 | +0.06% |
| QCOM | 2 | 7,232,000,000 | 6.42 | 1,126,479,751 | 1,126,000,000 | +0.04% |
| VZ | 0 | 17,174,000,000 | 4.06 | 4,230,049,261 | 4,231,000,000 | −0.02% |
| VZ | 1 | 17,506,000,000 | 4.14 | 4,228,502,415 | 4,223,000,000 | +0.13% |
| VZ | 2 | 11,614,000,000 | 2.75 | 4,223,272,727 | 4,215,000,000 | +0.20% |

**All 21 deviations are ≤0.58%** — an order of magnitude below the 5% "needs a look" bar, well
within normal EPS-rounding (2-dp EPS against a 9-10 digit share count) plus consolidated-vs
common-shareholder NCI/preferred-dividend noise. **The 7 recovered `diluted_shares` values are
correct, not `iloc[0]`-style mispicks.** Result stated plainly: this is a strong, clean pass —
every recovered share count reconciles with independently-sourced net income and the
independently-picked EPS to well under a percent.

## [R4] The five findings outside the original plan

Found only by the live 42-ticker go/no-go — no unit test could have caught this, because it's
about which real-world filings exist, not about picker logic. Two distinct classes:

**Class D — HON, MRK, XOM: EPS was silently `[]` in production, now recovered.**
`diluted_shares` concept is absent for these three (root cause B — unaffected, unchanged), and
`get_shares_outstanding_diluted()` also returns `None` for them, so the old code's computed
fallback (`if not eps and fin.net_income and shares_diluted`) never fired — `diluted_eps` was
an empty list before this branch, not merely wrong. Concept-first matching finds
`us-gaap_EarningsPerShareDiluted` directly and recovers the correct as-reported value. Strictly
better; nothing to reconcile against because there was no prior value.

**Class E — JNJ, QCOM: a live, pre-existing wrong-row EPS bug, now fixed as a side effect.**
Both issuers carry three separate EPS-tagged rows in their filed income statement — a
continuing-operations figure, a discontinued-operations figure, and the total:

```
JNJ   IncomeLossFromContinuingOperationsPerDilutedShare        = [11.03, 5.79,  5.20]
      DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerDilutedShare
                                                                = [ 0.00, 0.00,  8.52]
      EarningsPerShareDiluted (TOTAL, correct)                 = [11.03, 5.79, 13.72]

QCOM  IncomeLossFromContinuingOperationsPerDilutedShare        = [ 5.01, 8.94,  6.52]
      DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerDilutedShare
                                                                = [ 0.00, 0.03, -0.10]
      EarningsPerShareDiluted (TOTAL, correct)                 = [ 5.01, 8.97,  6.42]
```

The old label-based `_row_diluted_eps` picked the continuing-operations row for both. Because
each issuer had zero discontinued-ops impact in some (but not all) of the 3 reported years —
JNJ's FY2024/25, QCOM's FY2025 — the continuing-ops and total rows are **byte-identical in
those years**, so a spot-check on any single year, or the plan's own 2-of-3-years review, would
not have surfaced the defect. Only the year with a real discontinued-ops swing (JNJ's FY2023
Kenvue spin-off gain; QCOM's FY2024/23) exposes the mismatch.

**JNJ is not just "one wrong number" — it is a sign-flipped scored growth input, live in
production before this branch.** FY2023 net income $35.2B ÷ 2,560.4M diluted shares = **$13.75**
per share (matches the concept-matched $13.72 to rounding), not the stored $5.20 — the stored
value is 2.6× too low because it is continuing-operations-only, missing the one-time Kenvue
separation gain booked as discontinued operations. The series therefore **mixes two different
measures across fiscal years** (continuing-ops for FY2023, total for FY2024/25), which is worse
than being uniformly wrong in one direction:

```
eps_cagr_ps, stored (production, pre-fix) : +0.4564   (+45.6%/yr)
eps_cagr_ps, true (post-fix)              : -0.1044   (-10.4%/yr)
```

The sign inverts. `eps_cagr_ps` feeds `quality.dilution`'s growth leg (currently OFF — see
below) and the research QUANT CONTEXT; the corruption was live for as long as the label
matcher has existed, independent of this branch or the signed-off plan. This branch corrects
it as a side effect of matching on the exact-equality `concept` column: `EarningsPerShareDiluted`
can never be confused with `IncomeLossFromContinuingOperationsPerDilutedShare` because they are
different raw us-gaap tags, whereas both satisfy the old label regex ("Diluted" + "per share").

**Adjudicated (plan owner, `docs/PLAN_EDGAR_DILUTED_SHARES.md` §[R4], commit `491b6a1`): not a
Task 1 code defect.** The concept-first picker behaves correctly and identically on all five —
it always selects the single row whose `concept` exactly equals `us-gaap_EarningsPerShareDiluted`,
which is by construction the total. The defect is in the **plan's blast-radius measurement**:
root cause C was detected with a `>2 decimal places` heuristic that finds *computed* EPS (the
`net_income / scalar` signature) but is **structurally blind to wrong-row EPS** — a
continuing-operations figure is a clean, plausible 2-dp number, indistinguishable from a total
by that test alone. Only the `net_income / diluted_shares` arithmetic cross-check (Part B,
above) or a raw-row inspection surfaces this class. Record this methodological lesson so it is
not relearned: **a "looks like a real number" heuristic cannot detect a wrong-row pick; only an
independent cross-check against a value derived from a *different* filed tag can.**

## Files touched

`docs/audits/2026-07-31-edgar-concept-match.md` (this file, new), `CLAUDE.md`, `TODO.md`, plus
one permitted `src/` edit: `src/shortlist/providers/_edgar_facts.py:7-10`, the module docstring
correction — the "ABSOLUTE USD ... No scaling here or downstream" claim was falsified by MCD's
`diluted_shares` series (`[716.4, 721.9, 732.3]`, millions, filer-presentation-scaled). No
extraction logic changed; Task 1's `_rows_by_concept`/`_series_by_concept_or_label` and the
`extract_financials` call sites are untouched by Task 2.
