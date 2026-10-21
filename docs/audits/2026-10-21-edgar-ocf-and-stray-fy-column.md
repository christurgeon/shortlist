# 2026-10-21 — EDGAR operating cash flow, revenue and net income read the wrong row

**Kind:** evidence. **Fix:** `providers/_edgar_facts.py` reads the raw us-gaap total first for
operating cash flow and revenue, adds raw fallbacks for net income, and drops a trailing
period column that is empty in every row. edgartools 5.61.1.

## Symptom

EDGAR-only harness snapshots carried a small or negative operating cash flow for large,
cash-generative companies. Production snapshots of **Coca-Cola (KO), Altria (MO), Disney (DIS)
and Verizon (VZ)** dated 2026-10-20 had negative EDGAR free cash flow (KO -1.06B, MO -8M,
DIS -7.88B, VZ -19.27B), which the `negative_fcf` gate reads as cash burn. On IPAR the gate
also clamped the `/deep` screening call from HOLD to AVOID, dropped the value sub-score to 0,
and gave the reverse-DCF line a wrong base.

| Ticker | EDGAR OCF before | SEC companyfacts | Row picked before |
|---|---|---|---|
| KO | 1.05B | 7.41B | other operating activities |
| MO | 0.21B | 9.29B | equity-method dividends |
| DIS | 0.15B | 18.10B | equity-method dividends |
| VZ | -2.26B | 37.14B | other operating activities |
| IPAR | 6.7M | 214.9M | noncash lease expense |

## Causes, verified live

1. **The `standard_concept` bucket is on more than one row.** edgartools puts
   `NetCashFromOperatingActivities` on reconciling lines as well as on the subtotal, and
   `_row_by_standard_concept` keeps the least-nested match. When the filer nests the subtotal
   deeper than a reconciling line, the reconciling line wins. On IPAR the lease line is level 3
   and the subtotal is level 4, under the working-capital abstract. Revenue has the same defect:
   the `Revenue` bucket is on premiums (PGR), contract revenue (UNH, CVX, HP, WMT) or a trading
   line (JPM, WFC), all shallower than the total. GS's net revenue carries no bucket at all.
   In every mismatched case, a non-dimensional row with the raw total tag was present.
2. **An empty trailing period column.** IPAR's income statement has a `2018-12-31 (FY)` column
   with no value in any row. It is not the cause suggested at first sight: no other ticker in
   the run has one. `_series` is all-or-nothing, so revenue, net income, diluted EPS, operating
   income and interest expense were all empty. The `companyconcept` fallback then put diluted
   shares on the 2018 end at 1000x scale (31,522,371,000).
3. **No `NetIncomeLoss` row.** BSX, CAT and V tag no `NetIncomeLoss` on the income statement.
   BSX and CAT tag the parent's share as `NetIncomeLossAvailableToCommonStockholdersBasic`, and
   all three tag `ProfitLoss`. This is a separate cause from 2: none of the three has an empty
   column.

## The rules chosen

- Raw rows are non-dimensional and at minimum level. The first raw row with a complete series
  wins. If the filer tags a raw concept but no row is complete, the series is empty. The bucket
  is used only when the filer tags none of the raw concepts, because its min-level pick is the
  path that read the wrong line above.
- Operating cash flow (and FCF): `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations`,
  then `NetCashProvidedByUsedInOperatingActivities`. Capex is continuing-only, so FCF must use
  continuing OCF. AMD, HON and MIDD tag both. **Accruals** reads the total, because net income
  includes discontinued operations.
- Revenue: `us-gaap_Revenues`, then `us-gaap_RevenuesNetOfInterestExpense`.
- Net income: `us-gaap_NetIncomeLoss`, then the two buckets, then
  `NetIncomeLossAvailableToCommonStockholdersBasic` (BSX, CAT), then `ProfitLoss` (V), which
  includes any noncontrolling interest. On BSX the two differ by up to 1.4% (FY2023 1,570M
  against 1,592M).
- Period columns: drop **only trailing (oldest)** columns that are empty in every row. An empty
  newest or middle column is kept, so the series abstain. Dropping an empty newest column would
  move index 0 to the prior year, and inventory and the financing legs pair on index 0.
  Dropping a middle column would skip a year that a CAGR treats as consecutive.

## Breadth: 84 tickers, EDGAR only, newest fiscal year against companyfacts

"Before" is `origin/main` at `7e70a3e`; both runs were on 2026-10-21. A value is a MISMATCH when it is
2% or more off. A SIGN-FLIP is EDGAR FCF with a different sign from companyfacts OCF minus capex.
The OCF reference is the continuing-operations tag where the filer reports it, else the total.

| Field | Before | After |
|---|---|---|
| operating_cash_flow MISMATCH | 10 of 83 | 0 |
| revenue MISMATCH | 5 of 82 | 0 |
| revenue MISSING | 2 | 0 |
| net_income MISSING | 4 of 83 | 0 |
| FCF SIGN-FLIP | 5 of 77 | 0 |

- OCF fixed: AMD, DIS, EWSB, HON, IPAR, JPM, KO, MO, VZ, WFC. AMD (7.709B total, 6.493B
  continuing) and HON (6.408B, 6.075B) had discontinued operations in FCF before the change.
  MIDD was already on its continuing-operations row before, and is unchanged.
- Revenue fixed: CVX, JPM, PGR, UNH, WFC. Revenue now present: GS, IPAR.
- Net income now present: BSX, CAT, IPAR, V.
- FCF sign fixed: DIS, EWSB, IPAR, KO, MO.

**What the check cannot see.** The references read the same raw tags the extractor now prefers.
For revenue, `Revenues` is first in both, so "0 mismatches" is close to automatic and would not
detect a wrong `Revenues` row. The same holds for OCF and the continuing-operations tag. A pass
confirms row selection and scale, not the choice of concept. Known undetected case: **RBKB**
(a bank) tags neither revenue total, so the bucket gives 2.98M of contract revenue (service
charges) as revenue. The reference falls through to the same tag and marks it ok.

**Accepted basis changes.** Revenue now reads the filer's total line. CVX moves 2.5% to 4.8%
because its total includes other income. PGR moves because its total includes investment income and realized gains.
HP and WMT move by less than 2%.

Prior years: ten values (BAC, CRWD, HON, MELI, MIDD and WDC revenue or net income) differ from
the first-filed 10-K. All ten match the latest-filed 10-K, so they are recast comparatives.
This was the same before the fix. Every other changed value matches the latest-filed 10-K
within 2% in all three years.

Reproduce with `docs/audits/scripts/probe_edgar_statements_vs_companyfacts.py` on
the output of `shortlist-harness --sources edgar --out DIR`.

## Not changed

- **Capex** stays on the `CapitalExpenses` bucket. No capex mismatch was measured. LLY and VZ
  tag only the "other" PP&E variants, which the bucket covers and a raw-first rule would not.
- **`providers/_xbrl_facts.py` (the backtest extractor)** reads companyfacts by fiscal end
  with raw-tag priority lists. Neither the bucket defect nor the column defect can occur
  there. Its net-income list is `NetIncomeLoss` only, and BSX and CAT have no recent
  `NetIncomeLoss` in companyfacts, so the backtest has the same net-income gap for them. It
  is not changed here: the `/deep` ROIC-persistence section must stay on the basis its study
  measured, so that needs its own decision.
- The `standard_concept` alias lists are unchanged (register: "Five obvious refactors").
  The fix reads the raw tag before the bucket. It does not edit the bucket names.
