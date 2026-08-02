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


def test_diluted_shares_tag_single_sourced_everywhere(monkeypatch):
    """PLAN_EDGAR_ROOT_CAUSE_B.md fix-wave item 5: the bare
    WeightedAverageNumberOfDilutedSharesOutstanding tag used to be typed three
    times independently (_edgar_facts, _xbrl_facts, and the companyconcept
    fallback's request URL in data/sources/edgar.py) -- exactly the drift this
    module exists to prevent. All three must now derive from one constant."""
    pytest.importorskip("pandas")                  # _edgar_facts/edgar.py need the edgar extra
    from shortlist.providers import _edgar_facts as e

    assert g.DILUTED_SHARES_TAG == "WeightedAverageNumberOfDilutedSharesOutstanding"
    assert x.WTD_DIL_SHARES == [g.DILUTED_SHARES_TAG]
    assert (f"us-gaap_{g.DILUTED_SHARES_TAG}",) == e._DILUTED_SHARES_CONCEPTS

    # data/sources/edgar.py: the URL is built from the same constant, not a
    # fourth hardcoded literal -- assert against the actual request, not just
    # an import, so a re-hardcoded string would still fail this test.
    monkeypatch.setenv("SEC_IDENTITY", "test test@example.com")
    from shortlist.data.sources import EdgarSource

    class _FakeCompany:
        def __init__(self, ticker):
            self.cik = 1

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    captured = {}

    def _fake_get(url, **kw):
        captured["url"] = url
        return _FakeResponse()

    monkeypatch.setattr("edgar.Company", _FakeCompany)
    monkeypatch.setattr("httpx.get", _fake_get)

    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    src.identity = "test test@example.com"
    src._fetch_diluted_shares_concept("ZZZ")

    assert captured["url"].endswith(f"/us-gaap/{g.DILUTED_SHARES_TAG}.json")


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
