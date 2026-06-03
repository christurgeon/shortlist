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


def test_fwd_return_is_public_and_excess():
    from shortlist.backtest.engine import fwd_return
    hist = _hist("AAA", 0.10)        # rising series
    spy = _hist("SPY", 0.00)         # flat
    r = fwd_return(hist, spy, date(2019, 6, 1), 3, "excess")
    assert r is not None and r > 0.0   # rises faster than flat SPY
    raw = fwd_return(hist, spy, date(2019, 6, 1), 3, "raw")
    assert raw is not None and raw >= r  # raw >= excess when SPY flat/positive


def test_run_backtest_recovers_positive_ic_for_planted_signal():
    hists = {"AAA": _hist("AAA", 0.30), "BBB": _hist("BBB", 0.10),
             "CCC": _hist("CCC", 0.02)}
    spy = _hist("SPY", 0.01)
    src = MomentumSignalSource(hists, spy, THRESH, min_history=200)
    report = run_backtest([src], hists, spy, start=date(2019, 9, 1),
                          end=date(2021, 3, 1), horizons=[3],
                          n_buckets=3, return_mode="excess", xs_min_breadth=3,
                          price_asof=date(2026, 6, 1))
    r = [x for x in report.reports if x.signal == "momentum" and x.horizon == 3][0]
    assert r.n_obs > 0
    assert r.spread is not None and r.spread.spread >= 0
    assert report.price_asof is not None


def test_run_backtest_emits_one_report_per_emitted_signal():
    """A source emitting multiple signals per observation must yield one report per
    signal key, not one report for the source name."""
    from shortlist.backtest.signals import Observation

    hists = {"AAA": _hist("AAA", 0.30), "BBB": _hist("BBB", 0.10),
             "CCC": _hist("CCC", 0.02)}
    spy = _hist("SPY", 0.01)

    class _MultiSrc:
        name = "multi"

        def observe(self, ticker, as_of):
            return Observation(as_of, ticker, {"alpha": 60.0, "beta": 40.0})

    report = run_backtest(
        [_MultiSrc()], hists, spy,
        start=date(2019, 9, 1), end=date(2021, 3, 1), horizons=[3],
        n_buckets=3, return_mode="excess", xs_min_breadth=3,
        price_asof=date(2026, 6, 1),
    )
    signals = {r.signal for r in report.reports}
    assert {"alpha", "beta"} <= signals, (
        f"expected alpha and beta reports; got signals={signals}"
    )
    for r in report.reports:
        if r.signal in {"alpha", "beta"}:
            assert r.n_obs > 0, f"signal={r.signal} has n_obs=0"
