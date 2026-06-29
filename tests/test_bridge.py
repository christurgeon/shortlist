import pytest

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


def test_bridge_derives_per_share_eps_cagr_and_share_count_trend():
    snap = _full_snapshot()
    # Diluted EPS rising, share count FALLING (a buyback compounder): newest-first.
    snap.statements.diluted_eps = [5.0, 4.0, 3.0]
    snap.statements.diluted_shares = [900.0, 950.0, 1000.0]
    m = snapshot_to_metrics(snap)
    # per-share EPS CAGR over 3->5 across 2 periods = (5/3)**0.5 - 1
    assert m.eps_cagr_ps == pytest.approx((5.0 / 3.0) ** 0.5 - 1.0)
    # buybacks => negative share-count CAGR: (900/1000)**0.5 - 1
    assert m.share_count_cagr == pytest.approx((900.0 / 1000.0) ** 0.5 - 1.0)
    assert m.share_count_cagr < 0


def test_bridge_share_count_cagr_none_without_series():
    # The default snapshot has no diluted_shares -> field stays None (redistributed).
    assert snapshot_to_metrics(_full_snapshot()).share_count_cagr is None


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


def test_bridge_derives_fcf_yield_from_edgar_when_fmp_absent():
    # Absolute USD on BOTH sides (verified EDGAR units) -> quotient is a fraction.
    snap = TickerSnapshot(
        ticker="GEV",
        profile=Profile(market_cap=20_000_000_000.0),       # $20B
        fundamentals=Fundamentals(fcf_yield=None),           # FMP gated -> no fcf_yield
        statements=Statements(free_cash_flow=[1_000_000_000.0]),  # $1B FCF
    )
    m = snapshot_to_metrics(snap)
    assert m.fcf_yield == pytest.approx(0.05)                # 1e9 / 20e9


def test_bridge_keeps_fmp_fcf_yield_when_present():
    snap = TickerSnapshot(
        ticker="AAPL",
        profile=Profile(market_cap=20_000_000_000.0),
        fundamentals=Fundamentals(fcf_yield=0.03),
        statements=Statements(free_cash_flow=[1_000_000_000.0]),
    )
    assert snapshot_to_metrics(snap).fcf_yield == 0.03       # FMP wins, no override


from shortlist.data.bridge import _close_near


def test_close_near_exact_match():
    closes = [["2024-01-15", 100.0], ["2024-01-31", 105.0], ["2024-02-15", 102.0]]
    assert _close_near(closes, "2024-01-31") == 105.0


def test_close_near_picks_nearest_when_no_exact():
    closes = [["2024-01-15", 100.0], ["2024-02-15", 102.0]]
    # 2024-01-31 is 16d from Jan-15, 15d from Feb-15 -> Feb-15
    assert _close_near(closes, "2024-01-31") == 102.0


def test_close_near_empty_and_none_safe():
    assert _close_near([], "2024-01-31") is None
    assert _close_near([["2024-02-15", 102.0]], "garbage") is None
    assert _close_near([["2024-01-15", None], ["2024-02-15", 102.0]], "2024-01-31") == 102.0


def test_bridge_derives_pe_history_from_edgar_eps_and_yahoo_closes():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="GEV",
        fundamentals=Fundamentals(pe_ttm=None, pe_median_5y=None),
        statements=Statements(
            diluted_eps=[10.0, 8.0, 7.0],
            fiscal_period_end=["2024-12-31", "2023-12-31", "2022-12-31"],
        ),
        price=Price(
            price=200.0,
            monthly_closes=[["2022-12-29", 105.0], ["2023-12-29", 120.0], ["2024-12-31", 180.0]],
        ),
    )
    m = snapshot_to_metrics(snap)
    assert m.pe_ttm == pytest.approx(20.0)               # 200 / 10 (latest annual EPS)
    # annual PEs: 180/10=18, 120/8=15, 105/7=15 -> median = 15
    assert m.pe_median_5y == pytest.approx(15.0)
    assert m.pe_vs_history() == pytest.approx(15.0 / 20.0 - 1)


def test_bridge_pe_history_degrades_to_none_without_prices():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="GEV",
        fundamentals=Fundamentals(pe_ttm=None, pe_median_5y=None),
        statements=Statements(diluted_eps=[10.0], fiscal_period_end=["2024-12-31"]),
        price=Price(price=None, monthly_closes=[]),
    )
    m = snapshot_to_metrics(snap)
    assert m.pe_median_5y is None
    assert m.pe_ttm is None


def test_bridge_keeps_fmp_pe_when_present():
    from shortlist.data.bridge import snapshot_to_metrics
    snap = TickerSnapshot(
        ticker="AAPL",
        fundamentals=Fundamentals(pe_ttm=30.0, pe_median_5y=25.0),
        statements=Statements(diluted_eps=[10.0], fiscal_period_end=["2024-12-31"]),
        price=Price(price=200.0, monthly_closes=[["2024-12-31", 180.0]]),
    )
    m = snapshot_to_metrics(snap)
    assert (m.pe_ttm, m.pe_median_5y) == (30.0, 25.0)    # FMP untouched


from shortlist.data.models import ShortInterest


def _snap_with_si(**si_kwargs):
    from shortlist.data.models import Profile, Price
    return TickerSnapshot(
        ticker="AAA", as_of="2026-06-01T00:00:00+00:00",
        profile=Profile(market_cap=1.0e10), price=Price(price=100.0),
        short_interest=ShortInterest(settlement_date="2026-05-15", **si_kwargs),
    )


def test_bridge_short_pct_outstanding_and_dtc():
    # shares_out = 1e10/100 = 1e8; short 1e7 => 10% of outstanding
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, prev_short_shares=9.0e6, days_to_cover=4.2))
    assert abs(m.short_pct_outstanding - 0.10) < 1e-9
    assert m.days_to_cover == 4.2
    assert m.short_interest_rising is True
    assert m.short_data_age_days == 17        # 2026-06-01 minus 2026-05-15


def test_bridge_dtc_sentinel_dropped():
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, days_to_cover=999.99))
    assert m.days_to_cover is None


def test_bridge_short_pct_sanity_clamp():
    # short > 60% of outstanding => denominator suspect (ADR/dual-class) => dropped
    m = snapshot_to_metrics(_snap_with_si(short_shares=9.0e7))   # 90% of 1e8
    assert m.short_pct_outstanding is None


def test_bridge_rising_none_across_split():
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, prev_short_shares=9.0e6, split_flag=True))
    assert m.short_interest_rising is None


def test_bridge_no_short_interest_leaves_fields_none():
    m = snapshot_to_metrics(_full_snapshot())   # defined earlier in tests/test_bridge.py; has no short_interest
    assert m.short_pct_outstanding is None and m.days_to_cover is None


def test_bridge_populates_piotroski_from_statements():
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.data.models import TickerSnapshot, Statements
    st = Statements(
        fiscal_years=[2022, 2021],
        revenue=[1300, 1100], gross_profit=[700, 560],
        net_income=[160, 120], operating_cash_flow=[240, 200],
        total_debt=[300, 350],   # equity not used by the asset-free Core-6
        fiscal_period_end=["2022-12-31", "2021-12-31"],
    )
    snap = TickerSnapshot(ticker="T", statements=st)
    m = snapshot_to_metrics(snap)
    assert (m.piotroski_f, m.piotroski_f_legs) == (6, 6)


def test_financial_series_defaults_none():
    assert StockMetrics(ticker="X").financial_series is None


def test_financial_series_helper_newest_first_and_shaped():
    from shortlist.data.bridge import _financial_series
    st = Statements(
        fiscal_years=[2025, 2024],
        fiscal_period_end=["2025-09-28", "2024-09-30"],
        revenue=[100.0, 90.0], gross_profit=[45.0, 40.0],
        net_income=[22.0, 20.0], operating_cash_flow=[30.0, 28.0],
        free_cash_flow=[10.0, 8.0], diluted_eps=[6.08, 6.11],
        total_debt=[40.0, 41.0], diluted_shares=[15.0, 15.4],
    )
    out = _financial_series(st)
    assert len(out) == 2
    assert out[0]["fiscal_year"] == 2025          # newest-first preserved
    assert out[0]["revenue"] == 100.0 and out[0]["gross_profit"] == 45.0
    assert out[0]["diluted_eps"] == 6.08 and out[0]["total_debt"] == 40.0
    assert out[1]["fiscal_year"] == 2024


def test_financial_series_helper_ragged_is_none_safe():
    from shortlist.data.bridge import _financial_series
    st = Statements(fiscal_years=[2025, 2024], revenue=[100.0])  # shorter revenue
    out = _financial_series(st)
    assert len(out) == 2
    assert out[0]["revenue"] == 100.0
    assert out[1]["revenue"] is None              # ragged -> None, no IndexError
    assert out[1]["gross_profit"] is None


def test_financial_series_helper_empty_statements_is_empty_list():
    from shortlist.data.bridge import _financial_series
    assert _financial_series(Statements()) == []   # all-empty lists -> []


def test_bridge_populates_financial_series():
    snap = _full_snapshot()
    snap.statements.fiscal_period_end = ["2025-09-28", "2024-09-30", "2023-09-30"]
    m = snapshot_to_metrics(snap)
    assert m.financial_series is not None
    assert m.financial_series[0]["fiscal_year"] == 2025      # newest-first
    assert m.financial_series[0]["revenue"] == 100.0
    assert m.financial_series[0]["gross_profit"] == 45.0


def test_bridge_financial_series_none_when_statements_empty():
    # if st: is TRUTHY for an empty Statements() instance, so the assignment guard
    # (if series:) is what keeps the field None -- exercise it distinctly.
    snap = _full_snapshot()
    snap.statements = Statements()
    assert snapshot_to_metrics(snap).financial_series is None


def test_bridge_financial_series_none_when_no_statements():
    snap = _full_snapshot()
    snap.statements = None
    assert snapshot_to_metrics(snap).financial_series is None


# --- FIX M5: bridge round-trip for the three §2 price-refinement axes --------

def test_bridge_copies_price_refinement_axes():
    from shortlist.data.sources import _normalize_yahoo
    from shortlist.data.bridge import snapshot_to_metrics
    closes = [100.0 * (1.0 + 0.0008 * i) + (1.5 if i % 2 else -1.5) for i in range(300)]
    snap = _normalize_yahoo("AAA", closes, closes)
    m = snapshot_to_metrics(snap)
    assert m.pct_to_52w_high == snap.price.pct_to_52w_high
    assert m.max_daily_return == snap.price.max_daily_return
    assert m.vol_scaled_momentum == snap.price.vol_scaled_momentum
    assert m.pct_to_52w_high is not None        # the trending series populates it
