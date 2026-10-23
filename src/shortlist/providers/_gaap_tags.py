"""Single source of truth for us-gaap tag names. A duplicated literal lets call
sites drift. Dependency-free leaf shared by:
- providers/_edgar_facts.py (edgartools DataFrames; matches the raw `concept` column)
- providers/_xbrl_facts.py (SEC companyfacts; family members are summed per fiscal end)
- data/sources/edgar.py (the companyconcept fallback; the tag is also in the request URL)

The financing families below are annual flows reported as positive magnitudes
(PaymentsOf*/RepaymentsOf* are positive outflows). Dividends and repurchases are
summed across members; debt repayment and issuance are netted downstream.
"""
from __future__ import annotations

# Weighted-average diluted share count. Bare tag name: callers add their own
# "us-gaap_" / "us-gaap:" prefix.
DILUTED_SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

# Inventory balance (balance-sheet instant). Balance only: the two source paths sign
# the cash-flow companion `IncreaseDecreaseInInventories` oppositely.
# docs/design/PLAN_INVENTORY_DECOMPOSITION.md §0.1.
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
