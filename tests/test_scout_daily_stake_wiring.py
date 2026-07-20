"""Regression: the enabled config key for the stake-increase signal must actually build the
signal (the silent-dead-feature trap — mirrors test_scout_daily_buyback_wiring.py), plus the
ships-disabled config pin and the seen-accessions/baselines kwarg threading."""
from pathlib import Path

import yaml

from shortlist.scout.daily import (_DISCOVERY_SIGNAL_NAMES, _KNOWN_SIGNAL_KEYS,
                                    _enabled_signal_names, _signal_kwargs)

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_stake_increase_name_is_known_and_discovery():
    # the silent-omission guard: a registered signal absent from these sets parses as
    # enabled yet never constructs or runs, with no error.
    assert "edgar_13d_stake_increase" in _KNOWN_SIGNAL_KEYS
    assert "edgar_13d_stake_increase" in _DISCOVERY_SIGNAL_NAMES


def test_stake_increase_key_is_known_and_buildable():
    cfg = {"signals": {"edgar_13d_stake_increase": {"enabled": True, "weight": 0.5}}}
    assert "edgar_13d_stake_increase" in _enabled_signal_names(cfg)


def test_disabled_stake_increase_key_not_built():
    cfg = {"signals": {"edgar_13d_stake_increase": {"enabled": False, "weight": 0.5}}}
    assert "edgar_13d_stake_increase" not in _enabled_signal_names(cfg)


def test_ships_disabled_at_half_weight_in_repo_config():
    sig = _CFG["scout"]["signals"]["edgar_13d_stake_increase"]
    assert sig["enabled"] is False and sig["weight"] == 0.5


def test_signal_kwargs_threads_stake_increase_block_and_seen_baselines():
    cfg = {"activist_13d": {"drop_spacs": False, "drop_affiliates": False,
                            "stake_increase": {"min_increase_pp": 3.5,
                                               "max_prior_fetches": 4,
                                               "daily_cap": 150}}}
    kw = _signal_kwargs(cfg, stake_increase_seen=["a-1", "a-2"],
                        stake_baselines={"900|123": {"pct": 5.0, "date": "2026-01-01"}}
                        )["edgar_13d_stake_increase"]
    assert kw["min_increase_pp"] == 3.5
    assert kw["max_prior_fetches"] == 4
    assert kw["max_filings"] == 150
    assert kw["drop_spacs"] is False
    assert kw["drop_affiliates"] is False
    assert kw["seen_accessions"] == ["a-1", "a-2"]
    assert kw["baselines"] == {"900|123": {"pct": 5.0, "date": "2026-01-01"}}
    assert "identity" in kw


def test_signal_kwargs_defaults_when_block_absent():
    kw = _signal_kwargs({})["edgar_13d_stake_increase"]
    assert kw["min_increase_pp"] is None      # None => stake.MIN_INCREASE_PP in the ctor
    assert kw["max_prior_fetches"] == 10
    assert kw["max_filings"] == 300
    assert kw["drop_spacs"] is True
    assert kw["drop_affiliates"] is True
    assert kw["seen_accessions"] == []
    assert kw["baselines"] == {}


def test_repo_config_stake_increase_block_defaults():
    sti = _CFG["scout"]["activist_13d"]["stake_increase"]
    assert sti["min_increase_pp"] == 2.0
    assert sti["max_prior_fetches"] == 10
    assert sti["daily_cap"] == 300
