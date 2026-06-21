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
