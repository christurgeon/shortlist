"""Regression: the enabled config key for the FINRA short-interest signal must build the
signal AND be a known discovery key (the dead-feature failure mode: an enabled key absent
from _KNOWN_SIGNAL_KEYS is silently ignored)."""
from shortlist.scout.daily import (
    _DISCOVERY_SIGNAL_NAMES,
    _enabled_signal_names,
    _signal_kwargs,
)


def test_short_interest_key_is_known_and_enabled():
    cfg = {"signals": {"finra_short_interest": {"enabled": True, "weight": 0.5}}}
    assert "finra_short_interest" in _enabled_signal_names(cfg)


def test_short_interest_in_discovery_names():
    assert "finra_short_interest" in _DISCOVERY_SIGNAL_NAMES


def test_signal_kwargs_threads_short_interest_block_and_last_settlement():
    cfg = {"short_interest": {"min_jump_pct": 0.3, "min_dtc": 4.0, "max_dtc": 9.0,
                              "max_prior_dtc": 8.0, "min_avg_daily_volume": 200_000,
                              "top_n": 7}}
    kw = _signal_kwargs(cfg, last_finra_settlement="2026-06-15")["finra_short_interest"]
    assert kw["min_jump_pct"] == 0.3
    assert kw["max_dtc"] == 9.0
    assert kw["top_n"] == 7
    assert kw["last_settlement"] == "2026-06-15"


def test_signal_kwargs_short_interest_defaults_when_block_absent():
    kw = _signal_kwargs({})["finra_short_interest"]
    assert kw["min_jump_pct"] == 0.25
    assert kw["min_avg_daily_volume"] == 100_000
    assert kw["last_settlement"] is None
