from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import MomentumSignalSource
from shortlist.backtest.engine import observation_grid, run_backtest

THRESH = {"price_vs_200dma": [-0.10, 0.30], "rel_strength_6m": [-0.15, 0.25],
          "eps_revision": [-0.05, 0.10]}


def test_observation_grid_non_overlapping():
    g = observation_grid(date(2020, 1, 1), date(2021, 1, 1), step_months=3)
    assert g[0] == date(2020, 1, 1)
    assert g[1] == date(2020, 4, 1)
    assert all((g[i + 1] - g[i]).days >= 80 for i in range(len(g) - 1))


def _hist(ticker, slope, n=900):
    d0 = date(2019, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    closes = [100.0 + slope * i for i in range(n)]
    return PriceHistory(ticker, dates, closes)


def test_run_backtest_recovers_positive_ic_for_planted_signal():
    hists = {"AAA": _hist("AAA", 0.30), "BBB": _hist("BBB", 0.10),
             "CCC": _hist("CCC", 0.02)}
    spy = _hist("SPY", 0.01)
    src = MomentumSignalSource(hists, spy, THRESH, min_history=200)
    grid = observation_grid(date(2019, 9, 1), date(2021, 3, 1), step_months=3)
    report = run_backtest([src], hists, spy, grid, horizons=[3],
                          n_buckets=3, return_mode="excess", xs_min_breadth=3,
                          price_asof=date(2026, 6, 1))
    r = [x for x in report.reports if x.signal == "momentum" and x.horizon == 3][0]
    assert r.n_obs > 0
    assert r.spread is not None and r.spread.spread >= 0
    assert report.price_asof is not None
