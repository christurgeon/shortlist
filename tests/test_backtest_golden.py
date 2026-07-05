"""Golden numeric pin for run_backtest's per-signal aggregation (design spec
2026-07-05-leverage-residualized-ic-design.md, review finding I8): a fixed,
by-hand-derivable planted dataset run through the REAL run_backtest, asserting
EXACT float outputs. This test must pass BEFORE the engine.py aggregation block
(~125-161) is extracted into `_signal_report` and again AFTER, unchanged — it is
the thing that proves the extraction is byte-identical, since it predates it.

--- Planted universe -------------------------------------------------------

3 tickers (A, B, C), 3 non-overlapping monthly grid dates (T1=2020-01-01,
T2=2020-02-01, T3=2020-03-01; horizon=1 month, so step_months defaults to the
horizon and the grid is exactly [T1, T2, T3]). A 4th anchor date T4=2020-04-01
supplies the forward-return endpoint for the T3 observation.

The planted SignalSource returns a literal, hand-picked value per (ticker,
date) — no reconstruction from prices needed (unlike MomentumSignalSource):

        T1   T2   T3
    A:  10   20   30
    B:  40   50   60
    C:  70   80   90

Within each column (cross-section, a fixed date) the natural order is
A < B < C. Within each row (one ticker over time) the natural order is
T1 < T2 < T3. Both orderings are used below to derive expected Spearman ICs
by hand from RANK reasoning alone (Spearman only depends on rank).

Planted closes (PriceHistory) give each ticker's 3 window-forward-returns
(T1->T2, T2->T3, T3->T4). SPY is planted FLAT (100 at every date), so its
forward_return is exactly 0.0 and the default return_mode="excess" subtracts
0.0 -- i.e. excess return == raw return, bit-for-bit (subtracting a real 0.0
never perturbs a float).

Closes (chosen so a single ticker/date cell -- B's T2 window -- is deliberately
boosted to break monotonicity in exactly one cross-section and one time-series,
see below):

    A: T1=1000  T2=1010  T3=1030  T4=1060
    B: T1=1000  T2=1040  T3=1130  T4=1200
    C: T1=1000  T2=1070  T3=1156  T4=1260

Resulting window returns (computed by the real PriceHistory.forward_return,
i.e. by the code under test -- only the RANK ORDER is hand-derived, not the
decimal magnitudes):

    R(A,T1)=1010/1000-1 ~= 0.0100   R(A,T2)=1030/1010-1 ~= 0.0198   R(A,T3)=1060/1030-1 ~= 0.0291
    R(B,T1)=1040/1000-1 ~= 0.0400   R(B,T2)=1130/1040-1 ~= 0.0865   R(B,T3)=1200/1130-1 ~= 0.0619
    R(C,T1)=1070/1000-1 ~= 0.0700   R(C,T2)=1156/1070-1 ~= 0.0804   R(C,T3)=1260/1156-1 ~= 0.0900

--- Hand-derived per-date (cross-sectional, xs) Spearman ICs ---------------

T1: returns A(0.01) < B(0.04) < C(0.07) -- SAME order as the signal (A<B<C)
    -> perfect monotone match over 3 points -> rho = 1.0
T2: returns A(0.0198) < C(0.0804) < B(0.0865) -- B and C have SWAPPED versus
    the signal's A<B<C. For n=3 with signal-ranks x=(1,2,3) [A,B,C] and
    return-ranks y=(1,3,2) [A,B,C] (A smallest, B largest, C middle), the
    Spearman formula rho = 1 - 6*sum(d_i^2)/(n*(n^2-1)) with n=3 gives
    d=(0,-1,1), sum(d^2)=2, rho = 1 - 6*2/24 = 1 - 0.5 = 0.5.
T3: returns A(0.0291) < B(0.0619) < C(0.0900) -- SAME order as the signal
    -> rho = 1.0

xs Spearman ICs by date = [1.0, 1.0, 0.5] (order-independent for aggregation).

--- Hand-derived per-name (time-series, ts) Spearman ICs -------------------

A: signal (10,20,30) increasing T1<T2<T3; returns (0.0100,0.0198,0.0291) --
   SAME increasing order -> rho = 1.0
B: signal (40,50,60) increasing T1<T2<T3 (signal-ranks x=(1,2,3) for
   [T1,T2,T3]); returns (0.0400,0.0865,0.0619) -- T1 smallest, T2 LARGEST,
   T3 middle -> return-ranks y=(1,3,2) for [T1,T2,T3] -- the identical
   single-swap permutation as T2 above -> rho = 0.5
C: signal (70,80,90) increasing T1<T2<T3; returns (0.0700,0.0804,0.0900) --
   SAME increasing order -> rho = 1.0

ts Spearman ICs by name = [1.0, 1.0, 0.5] -- the SAME multiset as the xs ICs
(by design), so xs_ic and ts_ic aggregate to identical ICStats.

--- Hand-derived aggregate ICStats (mean/std/icir/t_stat/hit_rate) ---------

vals = [1.0, 1.0, 0.5], n=3
  mean = (1.0 + 1.0 + 0.5) / 3 = 2.5/3 = 0.8333333333333334
  sample variance = sum((v-mean)^2)/(n-1); deviations are (1/6, 1/6, -1/3),
    squared-sum = 1/36 + 1/36 + 1/9 = 6/36 = 1/6; /(n-1=2) = 1/12
  std = sqrt(1/12) = 0.28867513459481287
  icir = mean/std = (2.5/3) / sqrt(1/12) = (2.5/3) * sqrt(12)
  t_stat = icir * sqrt(n=3) = (2.5/3) * sqrt(12) * sqrt(3) = (2.5/3) * sqrt(36)
         = (2.5/3) * 6 = 5.0 EXACTLY (a clean algebraic identity, not a
    floating-point coincidence: sqrt(12)*sqrt(3) = sqrt(36) = 6).
  hit_rate = 3/3 = 1.0 (all three ICs are positive)

--- Hand-derived quantile spread (n_buckets=3, 9 pooled rows) --------------

quantile_spread sorts ALL 9 (signal, forward_return) rows by signal value
(all 9 signal values 10..90 are distinct, so no tie-break needed) and splits
into 3 equal buckets of 3 rows each (9/3 exactly -- no rounding):

  bucket0 (signal 10,20,30 = ticker A's 3 rows): mean of
    R(A,T1),R(A,T2),R(A,T3) ~= mean(0.0100, 0.0198, 0.0291) ~= 0.0196
  bucket1 (signal 40,50,60 = ticker B's 3 rows): mean of
    R(B,T1),R(B,T2),R(B,T3) ~= mean(0.0400, 0.0865, 0.0619) ~= 0.0628
  bucket2 (signal 70,80,90 = ticker C's 3 rows): mean of
    R(C,T1),R(C,T2),R(C,T3) ~= mean(0.0700, 0.0804, 0.0900) ~= 0.0801

  spread = bucket2_mean - bucket0_mean ~= 0.0801 - 0.0196 ~= 0.0605
  monotonic: bucket0 < bucket1 < bucket2 -> True

The exact float literals below were obtained by running the real PriceHistory
/ spearman_ic / aggregate_ic / quantile_spread primitives (shortlist.backtest.
metrics) over the by-hand pairs derived above -- an independent path from
run_backtest's own internal wiring, which is exactly what this test checks.
"""
from __future__ import annotations

from datetime import date

from shortlist.backtest.engine import run_backtest
from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import Observation

T1 = date(2020, 1, 1)
T2 = date(2020, 2, 1)
T3 = date(2020, 3, 1)
T4 = date(2020, 4, 1)

_PLANTED_SIGNAL = {
    ("A", T1): 10.0, ("A", T2): 20.0, ("A", T3): 30.0,
    ("B", T1): 40.0, ("B", T2): 50.0, ("B", T3): 60.0,
    ("C", T1): 70.0, ("C", T2): 80.0, ("C", T3): 90.0,
}


class _PlantedSource:
    """A SignalSource (per signals.py's SignalSource protocol) emitting one
    literal, hand-picked signal value per (ticker, date) -- no price
    reconstruction, unlike MomentumSignalSource."""
    name = "planted"

    def observe(self, ticker: str, as_of: date):
        v = _PLANTED_SIGNAL.get((ticker, as_of))
        if v is None:
            return None
        return Observation(as_of, ticker, {"test_signal": v})


def _hist(ticker: str, closes: dict[date, float]) -> PriceHistory:
    dates = [T1, T2, T3, T4]
    return PriceHistory(ticker, dates, [closes[d] for d in dates])


def _planted_histories() -> dict[str, PriceHistory]:
    return {
        "A": _hist("A", {T1: 1000.0, T2: 1010.0, T3: 1030.0, T4: 1060.0}),
        "B": _hist("B", {T1: 1000.0, T2: 1040.0, T3: 1130.0, T4: 1200.0}),
        "C": _hist("C", {T1: 1000.0, T2: 1070.0, T3: 1156.0, T4: 1260.0}),
    }


def _planted_spy() -> PriceHistory:
    # Flat -> forward_return is exactly 0.0 at every window, so excess
    # return == raw return bit-for-bit (subtracting 0.0 never perturbs a float).
    return _hist("SPY", {T1: 100.0, T2: 100.0, T3: 100.0, T4: 100.0})


def test_run_backtest_golden_exact_aggregation():
    histories = _planted_histories()
    spy = _planted_spy()

    report = run_backtest(
        [_PlantedSource()], histories, spy,
        start=T1, end=T3, horizons=[1],
        n_buckets=3, return_mode="excess",
        xs_min_breadth=3,               # 3-name universe; default floor (30) would suppress xs_ic
        price_asof=date(2020, 4, 2),
    )

    assert report.universe == ["A", "B", "C"]
    assert len(report.reports) == 1, (
        f"expected exactly one signal report (test_signal @ h=1); got {report.reports}"
    )
    r = report.reports[0]
    assert r.signal == "test_signal"
    assert r.horizon == 1
    assert r.n_obs == 9                 # 3 tickers x 3 grid dates, no drops
    assert r.breadth == 3.0             # 3 names present at every one of the 3 grid dates

    # --- xs_ic (cross-sectional, aggregated over the 3 grid dates: [1.0, 1.0, 0.5]) ---
    assert r.xs_ic is not None
    assert r.xs_ic.mean == 0.8333333333333334
    assert r.xs_ic.std == 0.28867513459481287
    assert r.xs_ic.icir == 2.886751345948129
    assert r.xs_ic.t_stat == 5.0
    assert r.xs_ic.hit_rate == 1.0
    assert r.xs_ic.n == 3

    # --- ts_ic (time-series, aggregated over the 3 names: [1.0, 1.0, 0.5]) ---
    # Identical ICStats to xs_ic by construction (same multiset of per-unit ICs).
    assert r.ts_ic is not None
    assert r.ts_ic.mean == 0.8333333333333334
    assert r.ts_ic.std == 0.28867513459481287
    assert r.ts_ic.icir == 2.886751345948129
    assert r.ts_ic.t_stat == 5.0
    assert r.ts_ic.hit_rate == 1.0
    assert r.ts_ic.n == 3

    # --- quantile spread (9 pooled rows, 3 exact buckets of 3) ---
    assert r.spread is not None
    assert r.spread.bucket_means == [
        0.019642731263417607,   # ticker A's 3 window returns
        0.06282845473110961,    # ticker B's 3 window returns
        0.08011307656652551,    # ticker C's 3 window returns
    ]
    assert r.spread.spread == 0.06047034530310791
    assert r.spread.monotonic is True
    assert r.spread.n_buckets == 3
    assert r.spread.n == 9

    # Both dates-count (3) and breadth (3.0) are below the engine's trust floor
    # (_TRUST_MIN_PERIODS=24, _TRUST_MIN_BREADTH=30) -- an EXPLORATORY note fires,
    # but xs_ic is NOT suppressed since we passed xs_min_breadth=3 explicitly.
    assert len(r.notes) == 1
    assert "EXPLORATORY" in r.notes[0]
    assert "below trust floor" in r.notes[0]
