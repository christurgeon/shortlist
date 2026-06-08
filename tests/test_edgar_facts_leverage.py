import pandas as pd

from shortlist.providers._edgar_facts import extract_financials


def _fy_df(rows):
    # Income / cash-flow: FY-suffixed period columns (matches edgartools to_dataframe()).
    cols = ["standard_concept", "2024-12-31 (FY)", "2023-12-31 (FY)"]
    data = [[sc, v.get("2024"), v.get("2023")] for sc, v in rows]
    return pd.DataFrame(data, columns=cols)


def _instant_df(rows):
    # Balance sheet: INSTANT date columns, NO "(FY)" suffix (real edgartools shape).
    cols = ["standard_concept", "2024-12-31", "2023-12-31"]
    data = [[sc, v.get("2024"), v.get("2023")] for sc, v in rows]
    return pd.DataFrame(data, columns=cols)


def test_balance_and_income_leverage_rows_extracted():
    income = _fy_df([
        ("Revenue", {"2024": 1000.0, "2023": 900.0}),
        ("OperatingIncomeLoss", {"2024": 200.0, "2023": 180.0}),
        ("InterestExpense", {"2024": 20.0, "2023": 18.0}),
    ])
    cashflow = _fy_df([
        ("NetCashFromOperatingActivities", {"2024": 230.0, "2023": 210.0}),
        ("DepreciationExpense", {"2024": 50.0, "2023": 45.0}),
    ])
    balance = _instant_df([
        ("LongTermDebt", {"2024": 400.0, "2023": 380.0}),
        ("CurrentPortionOfLongTermDebt", {"2024": 80.0, "2023": 70.0}),
        ("ShortTermDebt", {"2024": 20.0, "2023": 30.0}),
        ("CashAndMarketableSecurities", {"2024": 120.0, "2023": 100.0}),
    ])
    ef = extract_financials(income, cashflow, balance, shares_diluted=None)
    assert ef.operating_income[0] == 200.0
    assert ef.dep_amort[0] == 50.0          # from the cash-flow statement
    assert ef.interest_expense[0] == 20.0
    assert ef.total_debt[0] == 500.0        # 400 + 80 + 20 (summed components)
    assert ef.cash_and_equivalents[0] == 120.0


def test_total_debt_partial_components_still_sum():
    # Only long-term debt tagged (no current/short) -> still returns it.
    income = _fy_df([("Revenue", {"2024": 1.0, "2023": 1.0})])
    cashflow = _fy_df([("NetCashFromOperatingActivities", {"2024": 1.0, "2023": 1.0})])
    balance = _instant_df([("LongTermDebt", {"2024": 300.0, "2023": 280.0})])
    ef = extract_financials(income, cashflow, balance, shares_diluted=None)
    assert ef.total_debt[0] == 300.0
