"""The collinearity diagnostic: corr(candidate axis, scored axis) over the panel.

A standalone candidate axis that is highly cross-sectionally correlated with an
already-scored axis adds a correlated bet, not new signal (the EV/EBIT precedent).
This pins the generalized multi-pair diagnostic in cli.py.
"""
from datetime import date

from shortlist.backtest.signals import Observation
from shortlist.backtest.cli import (
    _collinearity, _COLLINEARITY_PAIRS, _COLLINEARITY_REDUNDANT)


def _obs(d, ticker, **sigs):
    return Observation(as_of=d, ticker=ticker, signals=sigs)


def test_collinearity_computes_present_pairs_and_skips_absent():
    d = date(2024, 1, 1)
    # net_debt_to_ebitda perfectly rank-correlated with growth; uncorrelated-ish with value.
    obs = [
        _obs(d, "A", net_debt_to_ebitda=10.0, growth=10.0, value=30.0),
        _obs(d, "B", net_debt_to_ebitda=20.0, growth=20.0, value=20.0),
        _obs(d, "C", net_debt_to_ebitda=30.0, growth=30.0, value=10.0),
    ]
    out = _collinearity(obs)
    # growth pair is present and perfectly rank-correlated -> +1.0
    assert out["net_debt_to_ebitda~growth"] == 1.0
    # value pair present and inversely rank-correlated -> -1.0
    assert out["net_debt_to_ebitda~value"] == -1.0
    # quality never appears -> pair skipped (no co-present data)
    assert "net_debt_to_ebitda~quality" not in out
    # ebit_ev_yield/value_fcf_yield never appear -> skipped
    assert "ebit_ev_yield~value_fcf_yield" not in out


def test_redundancy_threshold_flags_growth_collinearity():
    # The measured live corr was ~0.54 (> threshold) -> leverage duplicates growth.
    assert _COLLINEARITY_REDUNDANT == 0.5
    assert abs(0.54) >= _COLLINEARITY_REDUNDANT       # would be flagged
    assert abs(0.18) < _COLLINEARITY_REDUNDANT        # value pair would not


def test_pairs_registry_includes_leverage_vs_growth():
    # Guard: the leverage-vs-growth check (the decision-relevant one) stays registered.
    assert ("net_debt_to_ebitda", "growth") in _COLLINEARITY_PAIRS
    assert ("ebit_ev_yield", "value_fcf_yield") in _COLLINEARITY_PAIRS   # original preserved


def test_pairs_registry_includes_sue_vs_momentum():
    # SUE (§1) rides the SNAPSHOT-REPLAY path; its drift-vs-price-momentum collinearity
    # is measured once accumulation carries the earnings inputs (no-op until then).
    assert ("sue", "momentum") in _COLLINEARITY_PAIRS


def test_pairs_registry_includes_residual_vs_momentum():
    # Residual momentum (§2) rides the LIVE-price path; it WILL correlate with raw
    # momentum (it IS momentum, de-betaed) — the diagnostic exists to confirm it
    # dominates on rank IC, not that it is orthogonal.
    assert ("residual_momentum", "momentum") in _COLLINEARITY_PAIRS


# ---------------------------------------------------------------------------
# Integration: momentum-panel observations feed _collinearity correctly (§2)
# ---------------------------------------------------------------------------

def test_momentum_collinearity_yields_price_axis_pair():
    """Integration: a MomentumSignalSource observation panel, fed through
    _collinearity, produces the load-bearing pct_to_52w_high~price_vs_200dma pair.

    Pins two things jointly:
      (a) MomentumSignalSource emits both `pct_to_52w_high` and `price_vs_200dma`
          axes when those bands are in thresholds;
      (b) _collinearity finds the pair (registered in _COLLINEARITY_PAIRS) and
          returns a float.

    Three names are required — the cross_signal_xs_corr n>=3 guard means fewer
    than 3 co-present names per date returns None (not a float), and the pair
    would be absent from the result dict.  The names are given distinct price
    trajectories (one rising, two declining at different rates) so both
    pct_to_52w_high and price_vs_200dma have non-degenerate rank vectors.
    """
    from datetime import timedelta

    from shortlist.backtest.engine import collect_observations, observation_grid
    from shortlist.backtest.prices import PriceHistory
    from shortlist.backtest.signals import MomentumSignalSource

    d0 = date(2020, 1, 1)
    n = 300

    # Three names: rising, slowly declining, faster declining.
    # Declining names land below their 52-week high AND below the 200dma,
    # giving distinct, non-tied signal values so the rank correlation is defined.
    hists = {
        "AAA": PriceHistory("AAA",
                            [d0 + timedelta(days=i) for i in range(n)],
                            [150.0 + 0.5 * i for i in range(n)]),   # rising → at 52wk high
        "BBB": PriceHistory("BBB",
                            [d0 + timedelta(days=i) for i in range(n)],
                            [180.0 - 0.1 * i for i in range(n)]),   # slow decline
        "CCC": PriceHistory("CCC",
                            [d0 + timedelta(days=i) for i in range(n)],
                            [200.0 - 0.2 * i for i in range(n)]),   # faster decline
    }
    spy = PriceHistory("SPY",
                       [d0 + timedelta(days=i) for i in range(n)],
                       [100.0 + 0.1 * i for i in range(n)])

    thresholds = {
        "price_vs_200dma": [-0.10, 0.30],
        "rel_strength_6m": [-0.15, 0.25],
        "eps_revision":    [-0.05, 0.10],
        "pct_to_52w_high": [0.70, 1.00],
    }
    src = MomentumSignalSource(hists, spy, thresholds, min_history=200)

    # Grid within the histories' date range, after >= 200 closes have accumulated.
    # d0 + 200 days ~ 2020-07-18; end within the 300-day window (~ 2020-10-25).
    grid = observation_grid(date(2020, 8, 1), date(2020, 10, 1), 1)
    obs = collect_observations(src, sorted(hists.keys()), grid)

    result = _collinearity(obs)
    assert "pct_to_52w_high~price_vs_200dma" in result, (
        f"Expected 'pct_to_52w_high~price_vs_200dma' in collinearity result; "
        f"got keys={list(result)}")
    val = result["pct_to_52w_high~price_vs_200dma"]
    assert isinstance(val, float), f"Expected float, got {type(val)}: {val}"
    assert -1.0 <= val <= 1.0, f"Correlation {val!r} out of [-1, 1]"
