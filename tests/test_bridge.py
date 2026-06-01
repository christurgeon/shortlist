from shortlist.models import StockMetrics
from shortlist.data.models import (
    Analyst, Fundamentals, Insider, Price, Profile, Statements, TickerSnapshot,
)
from shortlist.data.bridge import snapshot_to_metrics


def test_new_risk_fields_default_none():
    assert StockMetrics(ticker="X").realized_vol is None
    assert StockMetrics(ticker="X").max_drawdown is None
    assert Price().realized_vol is None
    assert Price().max_drawdown is None


def _full_snapshot() -> TickerSnapshot:
    return TickerSnapshot(
        ticker="AAA",
        profile=Profile(name="Triple A", sector="Tech", market_cap=1.0e11),
        fundamentals=Fundamentals(
            pe_ttm=20.0, pe_median_5y=28.0, peg=1.5, fcf_yield=0.05,
            roe=0.30, roic=0.25, roic_5y_avg=0.21,
            gross_margin=0.45, net_margin=0.22, debt_to_equity=0.4,
            interest_coverage=12.0,
        ),
        statements=Statements(
            fiscal_years=[2025, 2024, 2023],
            revenue=[100.0, 90.0, 80.0],
            gross_profit=[45.0, 40.0, 36.0],   # margins ~0.45/0.444/0.45 -> stable
            net_income=[22.0, 20.0, 18.0],
            free_cash_flow=[10.0, 8.0, 7.0],   # most-recent positive
            total_debt=[40.0, 40.0, 40.0],
            total_equity=[100.0, 90.0, 80.0],
        ),
        analyst=Analyst(target_median=120.0, buy=15, hold=3, sell=1),
        insider=Insider(net_value_6m=-500_000.0, sentiment_mspr=0.1),
        price=Price(price=100.0, ma200=80.0, rel_strength_6m=0.06,
                    realized_vol=0.22, max_drawdown=-0.14),
    )


def test_bridge_maps_direct_fields():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.ticker == "AAA"
    assert m.name == "Triple A" and m.sector == "Tech"
    assert m.market_cap == 1.0e11 and m.price == 100.0
    assert m.pe_ttm == 20.0 and m.peg == 1.5 and m.fcf_yield == 0.05
    assert m.pe_median_5y == 28.0
    assert m.roe == 0.30 and m.roic == 0.25 and m.roic_5y_avg == 0.21
    assert m.gross_margin == 0.45 and m.net_margin == 0.22
    assert m.debt_to_equity == 0.4 and m.interest_coverage == 12.0
    assert m.target_median == 120.0
    assert (m.rating_buy, m.rating_hold, m.rating_sell) == (15, 3, 1)
    assert m.insider_net_6m == -500_000.0 and m.insider_sentiment == 0.1
    assert m.realized_vol == 0.22 and m.max_drawdown == -0.14


def test_bridge_computes_price_vs_200dma():
    m = snapshot_to_metrics(_full_snapshot())
    assert abs(m.price_vs_200dma - (100.0 / 80.0 - 1.0)) < 1e-9
    assert m.rel_strength_6m == 0.06


def test_bridge_derives_stability_and_fcf_positive():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.gross_margin_stability is not None and m.gross_margin_stability > 0.95
    assert m.fcf_positive is True


def test_bridge_fcf_positive_false_when_recent_negative():
    snap = _full_snapshot()
    snap.statements.free_cash_flow = [-5.0, 8.0, 7.0]
    assert snapshot_to_metrics(snap).fcf_positive is False


def test_bridge_maps_annual_history_fields():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.pe_median_5y == 28.0      # harness now fetches annual ratios
    assert m.roic_5y_avg == 0.21       # harness now computes 5y roic average
    # pe_vs_history() = pe_median_5y / pe_ttm - 1 = 28/20 - 1 = 0.4
    assert abs(m.pe_vs_history() - 0.4) < 1e-9


def test_bridge_remaining_parity_gap_is_none():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.eps_revision is None      # out of scope (Alpha Vantage)


def test_bridge_history_fields_none_when_fundamentals_lack_them():
    snap = _full_snapshot()
    snap.fundamentals.pe_median_5y = None
    snap.fundamentals.roic_5y_avg = None
    m = snapshot_to_metrics(snap)
    assert m.pe_median_5y is None and m.roic_5y_avg is None
    assert m.pe_vs_history() is None   # follows from pe_median_5y being None


def test_bridge_empty_snapshot_does_not_raise():
    m = snapshot_to_metrics(TickerSnapshot(ticker="ZZZ"))
    assert m.ticker == "ZZZ"
    assert m.price is None and m.roe is None
    assert m.gross_margin_stability is None and m.fcf_positive is None
