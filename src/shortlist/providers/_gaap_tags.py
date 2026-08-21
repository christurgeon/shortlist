"""Single source of truth for us-gaap concept/tag NAMES shared by both EDGAR
extraction paths, so a bare tag string never has to be typed in more than one
place (a duplicated literal is how the three call sites drift — see
docs/PLAN_EDGAR_ROOT_CAUSE_B.md fix-wave item 5).

Dependency-free leaf shared by all extraction/fetch call sites so they can
never drift:
- providers/_edgar_facts.py (edgartools statement DataFrames; matches on the
  raw `concept` column)
- providers/_xbrl_facts.py (SEC companyfacts; summed per fiscal end via
  sum_family — a filer may tag several distinct members in the same year, e.g.
  common + preferred repurchases, which must be ADDED, not overridden)
- data/sources/edgar.py (the companyconcept fallback's network seam — the tag
  also appears literally in the request URL)

The cash-flow financing FAMILIES below (PREDICTIVE_SIGNALS §5) are ANNUAL
flows reported as POSITIVE magnitudes (PaymentsOf*/RepaymentsOf* are positive
outflows), verified live (AAPL/MSFT/LMT). Dividends and repurchases are summed
across common+preferred members; debt repayment/issuance are netted
(repayments - issuance) downstream.
"""
from __future__ import annotations

# Weighted-average diluted share count (root cause B, PLAN_EDGAR_ROOT_CAUSE_B.md).
# Bare tag name (no "us-gaap_"/"us-gaap:" prefix) -- callers prefix as their own
# convention requires.
DILUTED_SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

# Inventory BALANCE (balance-sheet instant). Deliberately the balance only: the
# cash-flow companion `IncreaseDecreaseInInventories` is NOT extracted, because the
# two source paths sign it oppositely (raw XBRL: + == build; edgartools applies
# preferred_sign -1 so + == cash inflow) and nothing downstream needs the cash line.
# docs/PLAN_INVENTORY_DECOMPOSITION.md §0.1.
INVENTORY_BALANCE_TAG = "InventoryNet"

DIVIDEND_TAGS = ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
                 "PaymentsOfDividendsPreferredStockAndPreferenceStock",
                 "PaymentsOfDividendsMinorityInterest")
REPURCHASE_TAGS = ("PaymentsForRepurchaseOfCommonStock",
                   "PaymentsForRepurchaseOfEquity",
                   "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock",
                   "PaymentsForRepurchaseOfRedeemablePreferredStock")
DEBT_REPAYMENT_TAGS = ("RepaymentsOfLongTermDebt", "RepaymentsOfDebt",
                       "RepaymentsOfDebtMaturingInMoreThanThreeMonths",
                       "RepaymentsOfLongTermDebtAndCapitalSecurities",
                       "RepaymentsOfSeniorDebt", "RepaymentsOfNotesPayable")
DEBT_ISSUANCE_TAGS = ("ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt",
                      "ProceedsFromIssuanceOfSeniorLongTermDebt",
                      "ProceedsFromLongTermLinesOfCredit", "ProceedsFromNotesPayable")
