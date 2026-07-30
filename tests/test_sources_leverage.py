from shortlist.data.models import Statements


def test_statements_has_leverage_fields():
    st = Statements(operating_income=[200.0], dep_amort=[50.0],
                    interest_expense=[20.0], cash_and_equivalents=[120.0],
                    total_debt=[500.0])
    assert st.operating_income[0] == 200.0
    assert st.dep_amort[0] == 50.0
    assert st.interest_expense[0] == 20.0
    assert st.cash_and_equivalents[0] == 120.0


def test_pick_first_merge_carries_leverage_fields():
    """Statements now year-joins backfill (`_merge_statements`); this fixture's EDGAR
    donor has nothing new beyond what FMP's spine already supplies, so it still exercises
    only the priority-pick path, not the backfill. Both builders populate the leverage
    fields on FMP's side, so the merged snapshot keeps them."""
    from shortlist.data.models import SourceResult, TickerSnapshot, merge_snapshots

    fmp = SourceResult(source="fmp", partial=TickerSnapshot(
        ticker="X", statements=Statements(
            fiscal_years=[2024], revenue=[1000.0], total_debt=[500.0],
            cash_and_equivalents=[120.0], operating_income=[200.0], dep_amort=[50.0],
            ebitda=[250.0], interest_expense=[20.0])))
    edgar = SourceResult(source="edgar", partial=TickerSnapshot(
        ticker="X", statements=Statements(fiscal_years=[2024], revenue=[999.0])))
    merged = merge_snapshots("X", [fmp, edgar], priority=["fmp", "edgar"])
    assert merged.statements.ebitda == [250.0]
    assert merged.statements.total_debt == [500.0]
    assert merged.statements.cash_and_equivalents == [120.0]


def test_edgar_statements_win_when_fmp_absent():
    """When FMP gives no statements, EDGAR's (carrying leverage fields) win the merge."""
    from shortlist.data.models import SourceResult, TickerSnapshot, merge_snapshots

    edgar = SourceResult(source="edgar", partial=TickerSnapshot(
        ticker="X", statements=Statements(
            fiscal_years=[2024], revenue=[999.0], total_debt=[400.0],
            cash_and_equivalents=[90.0], ebitda=[180.0])))
    merged = merge_snapshots("X", [edgar], priority=["fmp", "edgar"])
    assert merged.statements.total_debt == [400.0]
    assert merged.statements.ebitda == [180.0]
