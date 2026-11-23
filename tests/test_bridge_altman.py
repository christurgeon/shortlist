import dataclasses

import pytest

from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import Profile, Statements, TickerSnapshot
from shortlist.stats import altman_z


def _z(wc, re_, ebit, mcap, sales, ta, tl):
    return 1.2 * wc / ta + 1.4 * re_ / ta + 3.3 * ebit / ta + 0.6 * mcap / tl + 1.0 * sales / ta


def test_altman_z_original_coefficients():
    z = altman_z(current_assets=400.0, current_liabilities=250.0, retained_earnings=-120.0,
                 ebit=90.0, market_cap=300.0, revenue=1100.0,
                 total_assets=1000.0, total_liabilities=600.0)
    assert z == pytest.approx(_z(150.0, -120.0, 90.0, 300.0, 1100.0, 1000.0, 600.0))
    assert z == pytest.approx(0.18 - 0.168 + 0.297 + 0.3 + 1.1)


@pytest.mark.parametrize("ta,tl", [(0.0, 600.0), (-1.0, 600.0), (1000.0, 0.0), (1000.0, -5.0)])
def test_altman_z_abstains_on_non_positive_denominators(ta, tl):
    assert altman_z(current_assets=400.0, current_liabilities=250.0, retained_earnings=0.0,
                    ebit=90.0, market_cap=300.0, revenue=1100.0,
                    total_assets=ta, total_liabilities=tl) is None


@pytest.mark.parametrize("field", ["current_assets", "current_liabilities",
                                   "retained_earnings", "ebit", "market_cap", "revenue",
                                   "total_assets", "total_liabilities"])
def test_altman_z_abstains_on_any_missing_input(field):
    kw = dict(current_assets=400.0, current_liabilities=250.0, retained_earnings=0.0,
              ebit=90.0, market_cap=300.0, revenue=1100.0,
              total_assets=1000.0, total_liabilities=600.0)
    kw[field] = None
    assert altman_z(**kw) is None


def test_altman_z_abstains_on_nan():
    assert altman_z(current_assets=float("nan"), current_liabilities=250.0,
                    retained_earnings=0.0, ebit=90.0, market_cap=300.0, revenue=1100.0,
                    total_assets=1000.0, total_liabilities=600.0) is None
    assert altman_z(current_assets=400.0, current_liabilities=250.0, retained_earnings=0.0,
                    ebit=90.0, market_cap=300.0, revenue=1100.0,
                    total_assets=float("nan"), total_liabilities=600.0) is None


def test_altman_z_abstains_on_non_positive_market_cap():
    assert altman_z(current_assets=400.0, current_liabilities=250.0, retained_earnings=0.0,
                    ebit=90.0, market_cap=0.0, revenue=1100.0,
                    total_assets=1000.0, total_liabilities=600.0) is None


def _snap(**st_overrides):
    st = Statements(
        fiscal_years=[2025, 2024], fiscal_period_end=["2025-12-31", "2024-12-31"],
        revenue=[1100.0, 1000.0], operating_income=[90.0, 80.0],
        total_assets=[1000.0, 900.0],
        balance_sheet_end="2025-12-31", current_assets=400.0, current_liabilities=250.0,
        total_liabilities=600.0, retained_earnings=-120.0,
    )
    st = dataclasses.replace(st, **st_overrides)
    return TickerSnapshot(ticker="X", profile=Profile(market_cap=300.0), statements=st)


def test_bridge_computes_altman_z_on_the_latest_fiscal_year():
    m = snapshot_to_metrics(_snap())
    assert m.altman_z == pytest.approx(_z(150.0, -120.0, 90.0, 300.0, 1100.0, 1000.0, 600.0))


@pytest.mark.parametrize("override", [
    {"balance_sheet_end": "2024-12-31"},     # balance instant is not the income FY end
    {"balance_sheet_end": None},
    {"fiscal_period_end": []},
    {"total_liabilities": 0.0},
    {"total_liabilities": None},
    {"total_assets": []},
    {"total_assets": [0.0, 900.0]},
    {"operating_income": []},
    {"revenue": []},
    {"revenue": [None, 1000.0]},
    {"retained_earnings": None},
])
def test_bridge_abstains(override):
    assert snapshot_to_metrics(_snap(**override)).altman_z is None


def test_bridge_abstains_without_market_cap():
    snap = _snap()
    snap.profile = Profile(market_cap=None)
    assert snapshot_to_metrics(snap).altman_z is None


def test_old_snapshot_without_the_new_keys_loads_and_abstains():
    d = _snap().to_dict()
    for k in ("balance_sheet_end", "current_assets", "current_liabilities",
              "total_liabilities", "retained_earnings"):
        d["statements"].pop(k)
    snap = TickerSnapshot.from_dict(d)
    assert snap.statements.balance_sheet_end is None
    assert snapshot_to_metrics(snap).altman_z is None


def test_new_fields_do_not_move_coverage():
    with_inputs = _snap()
    without = _snap(balance_sheet_end=None, current_assets=None, current_liabilities=None,
                    total_liabilities=None, retained_earnings=None)
    assert with_inputs.coverage() == without.coverage()


def test_card_dict_surfaces_altman_z():
    from shortlist.models import ScoreCard, StockMetrics
    from shortlist.screen import _card_dict

    def card(z):
        return ScoreCard(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                         momentum=None, value=None, opportunity=None, insider=None,
                         metrics=StockMetrics(ticker="X", altman_z=z))
    assert _card_dict(card(1.23456))["altman_z"] == 1.23
    assert _card_dict(card(None))["altman_z"] is None
