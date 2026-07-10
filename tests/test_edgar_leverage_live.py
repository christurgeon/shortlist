import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra"),
]


def test_aapl_leverage_fields_populate():
    from edgar import Company, set_identity

    from shortlist.providers._edgar_facts import extract_financials
    set_identity(os.environ["SEC_IDENTITY"])
    fin = Company("AAPL").get_financials()
    ef = extract_financials(
        fin.income_statement().to_dataframe(),
        fin.cashflow_statement().to_dataframe(),
        fin.balance_sheet().to_dataframe(),
        shares_diluted=None,
    )
    assert ef.total_debt and ef.total_debt[0] > 0
    assert ef.cash_and_equivalents and ef.cash_and_equivalents[0] > 0
    assert ef.operating_income and ef.operating_income[0] > 0
    # If D&A comes back empty the standard_concept bucket missed -> add a _row_*
    # matcher / alternate concept (see _edgar_facts.py:51).
    assert ef.dep_amort, "D&A bucket missed — fix the concept/matcher"
