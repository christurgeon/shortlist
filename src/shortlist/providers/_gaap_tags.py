"""Single source of truth for us-gaap tag names. A duplicated literal lets call
sites drift. Dependency-free leaf shared by:
- providers/_edgar_facts.py (edgartools DataFrames; matches the raw `concept` column)
- providers/_xbrl_facts.py (SEC companyfacts; family members are summed per fiscal end)
- data/sources/edgar.py (the companyconcept fallback; the tag is also in the request URL)

The financing families below are annual flows reported as positive magnitudes
(PaymentsOf*/RepaymentsOf* are positive outflows). Each family is combined with
combine_family; debt repayment and issuance are netted downstream.
"""
from __future__ import annotations

from typing import Optional

# Weighted-average diluted share count. Bare tag name: callers add their own
# "us-gaap_" / "us-gaap:" prefix.
DILUTED_SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

# Inventory balance (balance-sheet instant). Balance only: the two source paths sign
# the cash-flow companion `IncreaseDecreaseInInventories` oppositely.
# docs/design/PLAN_INVENTORY_DECOMPOSITION.md §0.1.
INVENTORY_BALANCE_TAG = "InventoryNet"

# PaymentsOfDividendsMinorityInterest is deliberately absent: distributions to
# noncontrolling interests are not paid to the parent's shareholders. The two totals
# (PaymentsOfDividends, PaymentsOfOrdinaryDividends) may still include them by definition.
DIVIDEND_TAGS = ("PaymentsOfDividends", "PaymentsOfOrdinaryDividends",
                 "PaymentsOfDividendsCommonStock",
                 "PaymentsOfDividendsPreferredStockAndPreferenceStock")
REPURCHASE_TAGS = ("PaymentsForRepurchaseOfCommonStock",
                   "PaymentsForRepurchaseOfEquity",
                   "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock",
                   "PaymentsForRepurchaseOfRedeemablePreferredStock")
# Each debt tag keeps its counterpart in the other family where us-gaap defines one.
# `*ShortTermDebt*` and commercial-paper tags are out of scope on both sides.
DEBT_REPAYMENT_TAGS = ("RepaymentsOfLongTermDebt", "RepaymentsOfDebt",
                       "RepaymentsOfDebtMaturingInMoreThanThreeMonths",
                       "RepaymentsOfLongTermDebtAndCapitalSecurities",
                       "RepaymentsOfSeniorDebt", "RepaymentsOfNotesPayable",
                       "RepaymentsOfDebtAndCapitalLeaseObligations",
                       "RepaymentsOfMediumTermNotes", "RepaymentsOfLongTermLinesOfCredit",
                       "RepaymentsOfConvertibleDebt", "RepaymentsOfSecuredDebt",
                       "RepaymentsOfUnsecuredDebt", "RepaymentsOfSubordinatedDebt",
                       "RepaymentsOfOtherLongTermDebt",
                       "EarlyRepaymentOfSeniorDebt", "MaturitiesOfSeniorDebt",
                       "EarlyRepaymentOfSubordinatedDebt", "MaturitiesOfSubordinatedDebt")
DEBT_ISSUANCE_TAGS = ("ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt",
                      "ProceedsFromIssuanceOfSeniorLongTermDebt",
                      "ProceedsFromLongTermLinesOfCredit", "ProceedsFromNotesPayable",
                      "ProceedsFromDebtMaturingInMoreThanThreeMonths",
                      "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet",
                      "ProceedsFromIssuanceOfMediumTermNotes", "ProceedsFromConvertibleDebt",
                      "ProceedsFromIssuanceOfSecuredDebt", "ProceedsFromIssuanceOfUnsecuredDebt",
                      "ProceedsFromIssuanceOfSubordinatedLongTermDebt",
                      "ProceedsFromIssuanceOfOtherLongTermDebt")

# Altman Z balance inputs (balance-sheet instants).
CURRENT_ASSETS_TAG = "AssetsCurrent"
CURRENT_LIABILITIES_TAG = "LiabilitiesCurrent"
LIABILITIES_TAG = "Liabilities"
RETAINED_EARNINGS_TAG = "RetainedEarningsAccumulatedDeficit"   # a deficit is negative

_LTD_REPAYMENT_PARTS = ("RepaymentsOfSeniorDebt", "RepaymentsOfMediumTermNotes",
                        "RepaymentsOfLongTermLinesOfCredit", "RepaymentsOfConvertibleDebt",
                        "RepaymentsOfSecuredDebt", "RepaymentsOfUnsecuredDebt",
                        "RepaymentsOfSubordinatedDebt", "RepaymentsOfOtherLongTermDebt")
_LTD_ISSUANCE_PARTS = ("ProceedsFromIssuanceOfSeniorLongTermDebt",
                       "ProceedsFromIssuanceOfMediumTermNotes",
                       "ProceedsFromLongTermLinesOfCredit", "ProceedsFromConvertibleDebt",
                       "ProceedsFromIssuanceOfSecuredDebt", "ProceedsFromIssuanceOfUnsecuredDebt",
                       "ProceedsFromIssuanceOfSubordinatedLongTermDebt",
                       "ProceedsFromIssuanceOfOtherLongTermDebt")

# Member -> the same-family members whose us-gaap meaning includes it (the calculation
# linkbase, or the parent's definition for aggregates such as RepaymentsOfDebt).
FAMILY_PARENTS: dict[str, tuple[str, ...]] = {
    "PaymentsOfOrdinaryDividends": ("PaymentsOfDividends",),
    "PaymentsOfDividendsCommonStock": ("PaymentsOfOrdinaryDividends",),
    "PaymentsOfDividendsPreferredStockAndPreferenceStock": ("PaymentsOfOrdinaryDividends",),
    "PaymentsForRepurchaseOfCommonStock": ("PaymentsForRepurchaseOfEquity",),
    "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock": ("PaymentsForRepurchaseOfEquity",),
    "RepaymentsOfDebt": ("RepaymentsOfDebtAndCapitalLeaseObligations",),
    "RepaymentsOfLongTermDebtAndCapitalSecurities": ("RepaymentsOfDebtAndCapitalLeaseObligations",),
    "RepaymentsOfDebtMaturingInMoreThanThreeMonths": ("RepaymentsOfDebt",),
    "RepaymentsOfNotesPayable": ("RepaymentsOfDebt",),
    "RepaymentsOfLongTermDebt": ("RepaymentsOfDebtMaturingInMoreThanThreeMonths",
                                 "RepaymentsOfLongTermDebtAndCapitalSecurities"),
    **dict.fromkeys(_LTD_REPAYMENT_PARTS, ("RepaymentsOfLongTermDebt",)),
    "EarlyRepaymentOfSeniorDebt": ("RepaymentsOfSeniorDebt",),
    "MaturitiesOfSeniorDebt": ("RepaymentsOfSeniorDebt",),
    "EarlyRepaymentOfSubordinatedDebt": ("RepaymentsOfSubordinatedDebt",),
    "MaturitiesOfSubordinatedDebt": ("RepaymentsOfSubordinatedDebt",),
    "ProceedsFromDebtMaturingInMoreThanThreeMonths": ("ProceedsFromIssuanceOfDebt",),
    "ProceedsFromNotesPayable": ("ProceedsFromIssuanceOfDebt",),
    "ProceedsFromIssuanceOfLongTermDebt": ("ProceedsFromDebtMaturingInMoreThanThreeMonths",
                                           "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet"),
    **dict.fromkeys(_LTD_ISSUANCE_PARTS, ("ProceedsFromIssuanceOfLongTermDebt",)),
}

# Relative gap under which two related members are one amount rounded to different scales.
_SAME_AMOUNT_TOL = 0.005

# Debt members only: a parent smaller than a reported child cannot include it, so the
# two are separate lines (e.g. a revolver line beside a term-debt line) and add.
_SMALLER_PARENT_ADDS = frozenset(DEBT_REPAYMENT_TAGS) | frozenset(DEBT_ISSUANCE_TAGS)


def _ancestors(tag: str) -> set[str]:
    out: set[str] = set()
    stack = list(FAMILY_PARENTS.get(tag, ()))
    while stack:
        p = stack.pop()
        if p not in out:
            out.add(p)
            stack.extend(FAMILY_PARENTS.get(p, ()))
    return out


def _descendants(tag: str) -> set[str]:
    return {t for t in FAMILY_PARENTS if tag in _ancestors(t)}


def combine_family(values: dict[str, float]) -> Optional[float]:
    """One period's family total from overlapping facts {tag: value}, or None when
    empty. Unrelated members add. Related members (one includes the other, or both
    include a common member) count once, by the larger, except that a debt parent
    smaller than its reported child adds to it. Near-equal related amounts always count
    once."""
    if not values:
        return None
    order = sorted(values, key=lambda t: (-abs(values[t]), len(_ancestors(t)), t))
    kept: list[str] = []
    for m in order:
        vm = abs(values[m])
        covered = False
        for k in kept:
            vk = abs(values[k])
            if m in _ancestors(k):                       # m is a parent of a larger kept k
                rel = vk - vm <= _SAME_AMOUNT_TOL * vk or m not in _SMALLER_PARENT_ADDS
            else:
                rel = k in _ancestors(m) or bool(_descendants(k) & _descendants(m))
            if rel:
                covered = True
                break
        if not covered:
            kept.append(m)
    return magnitude_sum([values[t] for t in kept])


def magnitude_sum(vals: list[float]) -> float:
    """Sum of magnitudes, signed like the largest value. A family's members all flow
    one way, but a source may sign one row against the rest."""
    total = sum(abs(v) for v in vals)
    return -total if vals and max(vals, key=abs) < 0 else total
