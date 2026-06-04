from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import Observation
from shortlist.backtest.fit_data import build_fit_rows


def _hist(ticker, slope, n=900):
    d0 = date(2019, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    closes = [100.0 + slope * i for i in range(n)]
    return PriceHistory(ticker, dates, closes)


class _AllAxes:
    """Emits all four fundamental axes for every (ticker, date)."""
    name = "fake"

    def observe(self, ticker, as_of):
        return Observation(as_of, ticker,
                           {"quality": 60.0, "moat": 55.0, "growth": 70.0, "value": 40.0})


class _MissingValue:
    """Emits only 3 of the 4 axes (value missing) -> rows must be dropped."""
    name = "fake"

    def observe(self, ticker, as_of):
        return Observation(as_of, ticker, {"quality": 60.0, "moat": 55.0, "growth": 70.0})


AXES = ["quality", "moat", "growth", "value"]


def test_build_fit_rows_keeps_full_dict_and_dates_as_period_id():
    hists = {"AAA": _hist("AAA", 0.10), "BBB": _hist("BBB", 0.05)}
    spy = _hist("SPY", 0.01)
    rows = build_fit_rows(_AllAxes(), ["AAA", "BBB"], hists, spy,
                          start=date(2019, 9, 1), end=date(2020, 9, 1),
                          horizon=3, axes=AXES)
    assert rows, "expected non-empty rows"
    for period_id, sub, fwd in rows:
        assert isinstance(period_id, date)            # date period_id for the gap guard
        assert set(sub) == set(AXES)                  # full co-emitting dict
        assert isinstance(fwd, float)


def test_build_fit_rows_drops_rows_missing_an_axis():
    hists = {"AAA": _hist("AAA", 0.10)}
    spy = _hist("SPY", 0.01)
    rows = build_fit_rows(_MissingValue(), ["AAA"], hists, spy,
                          start=date(2019, 9, 1), end=date(2020, 9, 1),
                          horizon=3, axes=AXES)
    assert rows == []                                 # co-emission filter drops every row


def test_build_fit_rows_uses_non_overlapping_grid():
    hists = {"AAA": _hist("AAA", 0.10)}
    spy = _hist("SPY", 0.01)
    rows = build_fit_rows(_AllAxes(), ["AAA"], hists, spy,
                          start=date(2019, 9, 1), end=date(2020, 9, 1),
                          horizon=3, axes=AXES)
    period_ids = sorted({p for p, _, _ in rows})
    assert all((b - a).days >= 80 for a, b in zip(period_ids, period_ids[1:], strict=False))


def test_build_fit_rows_drops_when_no_forward_return():
    # grid date past the end of the price series -> no forward return -> dropped
    hists = {"AAA": _hist("AAA", 0.10, n=300)}        # series ends ~2019-10-27
    spy = _hist("SPY", 0.01, n=300)
    rows = build_fit_rows(_AllAxes(), ["AAA"], hists, spy,
                          start=date(2025, 1, 1), end=date(2025, 6, 1),
                          horizon=3, axes=AXES)
    assert rows == []
