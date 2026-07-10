# src/shortlist/providers/_gaap_tags.py
"""Single source of truth for the cash-flow financing us-gaap concept FAMILIES
used by the total-shareholder-yield extraction (PREDICTIVE_SIGNALS §5).

Dependency-free leaf shared by BOTH extraction paths so they can never drift:
- providers/_edgar_facts.py (edgartools statement DataFrames; matches on the
  raw `concept` column)
- providers/_xbrl_facts.py (SEC companyfacts; summed per fiscal end via
  sum_family — a filer may tag several distinct members in the same year, e.g.
  common + preferred repurchases, which must be ADDED, not overridden)

Verified live (AAPL/MSFT/LMT). All are ANNUAL flows reported as POSITIVE
magnitudes (PaymentsOf*/RepaymentsOf* are positive outflows). Dividends and
repurchases are summed across common+preferred members; debt repayment/issuance
are netted (repayments - issuance) downstream.
"""
from __future__ import annotations

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
