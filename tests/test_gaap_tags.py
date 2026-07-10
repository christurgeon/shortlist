# tests/test_gaap_tags.py
"""The financing-tag families and the fiscal-year span window are single-sourced
(providers/_gaap_tags.py and stats.py); both extraction paths must consume the
shared definitions so they can never drift again."""
from __future__ import annotations

import pytest

from shortlist.providers import _gaap_tags as g
from shortlist.providers import _xbrl_facts as x
from shortlist import stats


def test_xbrl_families_come_from_the_shared_leaf():
    assert list(g.DIVIDEND_TAGS) == x.DIVIDENDS_PAID
    assert list(g.REPURCHASE_TAGS) == x.REPURCHASES
    assert list(g.DEBT_REPAYMENT_TAGS) == x.DEBT_REPAYMENTS
    assert list(g.DEBT_ISSUANCE_TAGS) == x.DEBT_ISSUANCE


def test_edgar_families_come_from_the_shared_leaf():
    pytest.importorskip("pandas")                  # _edgar_facts needs the edgar extra
    from shortlist.providers import _edgar_facts as e
    assert e._DIVIDEND_TAGS is g.DIVIDEND_TAGS
    assert e._REPURCHASE_TAGS is g.REPURCHASE_TAGS
    assert e._DEBT_REPAYMENT_TAGS is g.DEBT_REPAYMENT_TAGS
    assert e._DEBT_ISSUANCE_TAGS is g.DEBT_ISSUANCE_TAGS


def test_fy_span_window_single_sourced_in_stats():
    assert x._MIN_PERIOD_DAYS is stats._FY_MIN_DAYS
    assert x._MAX_PERIOD_DAYS is stats._FY_MAX_DAYS
    assert (stats._FY_MIN_DAYS, stats._FY_MAX_DAYS) == (350, 380)


def test_family_values_pinned():
    # The exact live-verified tag sets (regression pin — values, not just identity).
    assert g.DIVIDEND_TAGS == (
        "PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividendsPreferredStockAndPreferenceStock",
        "PaymentsOfDividendsMinorityInterest")
    assert g.REPURCHASE_TAGS == (
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
        "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock",
        "PaymentsForRepurchaseOfRedeemablePreferredStock")
    assert g.DEBT_REPAYMENT_TAGS == (
        "RepaymentsOfLongTermDebt", "RepaymentsOfDebt",
        "RepaymentsOfDebtMaturingInMoreThanThreeMonths",
        "RepaymentsOfLongTermDebtAndCapitalSecurities",
        "RepaymentsOfSeniorDebt", "RepaymentsOfNotesPayable")
    assert g.DEBT_ISSUANCE_TAGS == (
        "ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt",
        "ProceedsFromIssuanceOfSeniorLongTermDebt",
        "ProceedsFromLongTermLinesOfCredit", "ProceedsFromNotesPayable")
