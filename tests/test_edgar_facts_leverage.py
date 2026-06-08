import pandas as pd

from shortlist.providers._edgar_facts import extract_financials


def _df(rows):
    # rows: list of (standard_concept, {col: val}); FY column shape used by _fy_columns.
    cols = ["standard_concept", "2024-12-31 (FY)", "2023-12-31 (FY)"]
    data = [[sc, v.get("2024-12-31 (FY)"), v.get("2023-12-31 (FY)")] for sc, v in rows]
    return pd.DataFrame(data, columns=cols)


def test_balance_and_income_leverage_rows_extracted():
    income = _df([
        ("Revenue", {"2024-12-31 (FY)": 1000.0, "2023-12-31 (FY)": 900.0}),
        ("NetIncomeLoss", {"2024-12-31 (FY)": 100.0, "2023-12-31 (FY)": 90.0}),
        ("OperatingIncomeLoss", {"2024-12-31 (FY)": 200.0, "2023-12-31 (FY)": 180.0}),
        ("DepreciationDepletionAndAmortization", {"2024-12-31 (FY)": 50.0, "2023-12-31 (FY)": 45.0}),
        ("InterestExpense", {"2024-12-31 (FY)": 20.0, "2023-12-31 (FY)": 18.0}),
    ])
    cashflow = _df([
        ("NetCashFromOperatingActivities", {"2024-12-31 (FY)": 230.0, "2023-12-31 (FY)": 210.0}),
    ])
    balance = _df([
        ("LongTermDebt", {"2024-12-31 (FY)": 500.0, "2023-12-31 (FY)": 480.0}),
        ("CashAndCashEquivalents", {"2024-12-31 (FY)": 120.0, "2023-12-31 (FY)": 100.0}),
    ])
    ef = extract_financials(income, cashflow, balance, shares_diluted=None)
    assert ef.operating_income[0] == 200.0
    assert ef.dep_amort[0] == 50.0
    assert ef.interest_expense[0] == 20.0
    assert ef.total_debt[0] == 500.0
    assert ef.cash_and_equivalents[0] == 120.0
