"""The statement series the /deep capital-allocation section reads off the card."""
from shortlist.data.bridge import ROIC_TAX_RATE, _roic_by_fiscal_year, snapshot_to_metrics
from shortlist.data.models import Statements, TickerSnapshot


def _st(**over) -> Statements:
    base = dict(
        fiscal_years=[2025, 2024, 2023],
        operating_income=[200.0, 150.0, 120.0],
        total_debt=[100.0, 100.0, 100.0],
        total_equity=[500.0, 400.0, 300.0],
        dividends_paid=-30.0, repurchases=-50.0,
    )
    base.update(over)
    return Statements(**base)


def test_invested_capital_series_is_newest_first_on_the_roic_basis():
    m = snapshot_to_metrics(TickerSnapshot(ticker="X", statements=_st()))
    assert m.invested_capital_series == [
        {"fiscal_year": 2025, "nopat": 200.0 * (1 - ROIC_TAX_RATE), "invested_capital": 600.0},
        {"fiscal_year": 2024, "nopat": 150.0 * (1 - ROIC_TAX_RATE), "invested_capital": 500.0},
        {"fiscal_year": 2023, "nopat": 120.0 * (1 - ROIC_TAX_RATE), "invested_capital": 400.0},
    ]


def test_the_series_and_the_computed_roic_share_one_basis():
    st = _st(total_equity=[500.0, -150.0, 300.0], operating_income=[200.0, 150.0, None])
    m = snapshot_to_metrics(TickerSnapshot(ticker="X", statements=st))
    by_year = _roic_by_fiscal_year(st, ROIC_TAX_RATE)
    assert {r["fiscal_year"]: r["nopat"] / r["invested_capital"]
            for r in m.invested_capital_series} == by_year
    # A year with invested capital <= 0 or a missing input is left out, never zero-filled.
    assert [r["fiscal_year"] for r in m.invested_capital_series] == [2025]


def test_latest_year_buybacks_and_dividends_reach_the_card_as_filed():
    m = snapshot_to_metrics(TickerSnapshot(ticker="X", statements=_st()))
    assert (m.dividends_paid, m.repurchases) == (-30.0, -50.0)


def test_absent_inputs_stay_none():
    m = snapshot_to_metrics(TickerSnapshot(
        ticker="X", statements=Statements(fiscal_years=[2025], revenue=[1.0])))
    assert m.invested_capital_series is None
    assert (m.dividends_paid, m.repurchases) == (None, None)


def test_the_scorer_ignores_the_new_fields():
    from pathlib import Path

    from shortlist.config import load_config
    from shortlist.scoring import score

    config = load_config(str(Path(__file__).resolve().parents[1] / "config.yaml"))
    m = snapshot_to_metrics(TickerSnapshot(ticker="X", statements=_st()))
    card = score(m, config)
    m.invested_capital_series, m.dividends_paid, m.repurchases = None, None, None
    again = score(m, config)
    assert (card.composite, card.passed, card.scored, card.gates, card.flags) == (
        again.composite, again.passed, again.scored, again.gates, again.flags)
