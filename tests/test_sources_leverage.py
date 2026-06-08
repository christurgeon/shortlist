from shortlist.data.models import Statements


def test_statements_has_leverage_fields():
    st = Statements(operating_income=[200.0], dep_amort=[50.0],
                    interest_expense=[20.0], cash_and_equivalents=[120.0],
                    total_debt=[500.0])
    assert st.operating_income[0] == 200.0
    assert st.dep_amort[0] == 50.0
    assert st.interest_expense[0] == 20.0
    assert st.cash_and_equivalents[0] == 120.0
