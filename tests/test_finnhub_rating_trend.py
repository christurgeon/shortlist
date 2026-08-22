"""Sell-side rating REVISION capture from Finnhub's `stock/recommendation` payload.

The payload carries ~4 monthly periods on the free tier and the parser used to keep
only `trend[0]`. The deltas are computed inside the Finnhub normalizer on purpose:
`analyst` merges field-by-field with `fmp` ahead of `finnhub`, so a prior level stored
here would be differenced against an FMP level from a different analyst panel. Storing
only the change keeps both ends same-vendor.
"""
from shortlist.data.sources import _normalize_finnhub, _rating_trend
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import TickerSnapshot, Analyst


def _p(period, sb=0, b=0, h=0, s=0, ss=0):
    return {"period": period, "strongBuy": sb, "buy": b,
            "hold": h, "sell": s, "strongSell": ss}


# AAPL, live-probed 2026-08-22.
AAPL = [_p("2026-08-01", sb=13, b=24, h=14, s=3),
        _p("2026-07-01", sb=13, b=23, h=16, s=2),
        _p("2026-06-01", sb=14, b=24, h=15, s=2),
        _p("2026-05-01", sb=15, b=24, h=13, s=2)]


# --- abstention ------------------------------------------------------------------

def test_no_trend_is_none():
    assert _rating_trend(None) is None
    assert _rating_trend([]) is None


def test_single_period_keeps_levels_and_abstains_on_the_drift():
    a = _rating_trend([_p("2026-08-01", sb=13, b=24, h=14, s=3)])
    assert (a.buy, a.hold, a.sell) == (37, 14, 3)
    assert a.rating_months is None
    assert a.buy_delta is None and a.hold_delta is None and a.sell_delta is None


def test_unparseable_periods_abstain_on_the_drift_but_keep_levels():
    """Without a usable period we cannot order the rows or state the span, and a
    delta whose window is unknown is uninterpretable. Levels still come through."""
    rows = [{"strongBuy": 13, "buy": 24, "hold": 14, "sell": 3},
            {"strongBuy": 15, "buy": 24, "hold": 13, "sell": 2}]
    a = _rating_trend(rows)
    assert (a.buy, a.hold, a.sell) == (37, 14, 3)
    assert a.rating_months is None and a.buy_delta is None


# --- the drift itself -------------------------------------------------------------

def test_drift_is_newest_minus_oldest_over_the_month_span():
    a = _rating_trend(AAPL)
    assert (a.buy, a.hold, a.sell) == (37, 14, 3)   # newest period, unchanged behaviour
    assert a.rating_months == 3                      # 2026-05-01 -> 2026-08-01
    assert a.buy_delta == -2                         # 39 -> 37
    assert a.hold_delta == 1                         # 13 -> 14
    assert a.sell_delta == 1                         # 2 -> 3


def test_row_order_does_not_matter():
    """Finnhub returns newest-first today; nothing documents that it must. Ordering
    by `period` means a reversed payload cannot silently flip the sign of the drift."""
    assert _rating_trend(list(reversed(AAPL))) == _rating_trend(AAPL)


def test_flat_consensus_reports_zero_not_none():
    """No revision is a fact, not a data gap — the caller distinguishes them."""
    a = _rating_trend([_p("2026-08-01", sb=5, b=5, h=2, s=1),
                       _p("2026-06-01", sb=5, b=5, h=2, s=1)])
    assert a.rating_months == 2
    assert (a.buy_delta, a.hold_delta, a.sell_delta) == (0, 0, 0)


def test_missing_counts_are_zero_not_none():
    a = _rating_trend([{"period": "2026-08-01", "buy": 4},
                       {"period": "2026-07-01", "strongBuy": 1, "buy": 1, "sell": 2}])
    assert a.buy_delta == 2      # 2 -> 4
    assert a.sell_delta == -2    # 2 -> 0


def test_span_crosses_a_year_boundary():
    a = _rating_trend([_p("2026-02-01", sb=1, b=1), _p("2025-11-01", sb=2, b=2)])
    assert a.rating_months == 3


# --- wiring ------------------------------------------------------------------------

def test_normalize_finnhub_carries_the_drift():
    snap = _normalize_finnhub("AAPL", {"recommendation": AAPL})
    assert snap.analyst.rating_months == 3
    assert snap.analyst.buy_delta == -2


def test_bridge_passes_the_drift_to_metrics():
    snap = TickerSnapshot(ticker="AAPL")
    snap.analyst = Analyst(buy=37, hold=14, sell=3, rating_months=3,
                           buy_delta=-2, hold_delta=1, sell_delta=1)
    m = snapshot_to_metrics(snap)
    assert m.rating_months == 3
    assert (m.rating_buy_delta, m.rating_hold_delta, m.rating_sell_delta) == (-2, 1, 1)


def test_revision_fields_do_not_move_coverage():
    """`analyst` is a KEY_OBJECT, so an un-excluded field enters the coverage
    DENOMINATOR for every snapshot ever taken — which shifts accumulate's
    GATED/THIN/CAPTURED classification (the `inventory` precedent, and the
    ret_* note in data/models.py). These four are DERIVED and Finnhub-only,
    so they must never dilute a source's coverage."""
    base = TickerSnapshot(ticker="AAPL")
    base.analyst = Analyst(buy=37, hold=14, sell=3)
    with_drift = TickerSnapshot(ticker="AAPL")
    with_drift.analyst = Analyst(buy=37, hold=14, sell=3, rating_months=3,
                                 buy_delta=-2, hold_delta=1, sell_delta=1)
    assert base.coverage() == with_drift.coverage()
    assert not any("delta" in f or "rating_months" in f for f in base.missing())
