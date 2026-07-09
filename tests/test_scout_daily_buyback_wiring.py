"""Regression: the enabled config key for the buyback signal must actually build the signal
(the silent-dead-feature trap — mirrors test_scout_daily_eightk_wiring.py), plus the
ships-disabled config pin and the ScoutState buyback-accession round-trip."""
from pathlib import Path

import yaml

from shortlist.scout.daily import (_DISCOVERY_SIGNAL_NAMES, _KNOWN_SIGNAL_KEYS,
                                    _enabled_signal_names, _signal_kwargs)
from shortlist.scout.state import ScoutState

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_buyback_name_is_known_and_discovery():
    # the silent-omission guard: a registered signal absent from these sets parses as
    # enabled yet never constructs or runs, with no error.
    assert "edgar_buyback" in _KNOWN_SIGNAL_KEYS
    assert "edgar_buyback" in _DISCOVERY_SIGNAL_NAMES


def test_buyback_key_is_known_and_buildable():
    cfg = {"signals": {"edgar_buyback": {"enabled": True, "weight": 0.5}}}
    assert "edgar_buyback" in _enabled_signal_names(cfg)


def test_disabled_buyback_key_not_built():
    cfg = {"signals": {"edgar_buyback": {"enabled": False, "weight": 0.5}}}
    assert "edgar_buyback" not in _enabled_signal_names(cfg)


def test_signal_kwargs_threads_buyback_block_and_seen_accessions():
    cfg = {"buyback": {"phrases": ["approved a new share repurchase program"],
                       "daily_cap": 4, "drop_spacs": False, "deny_list": ["SPY"]}}
    kw = _signal_kwargs(cfg, buyback_seen=["a-1", "a-2"])["edgar_buyback"]
    assert kw["phrases"] == ["approved a new share repurchase program"]
    assert kw["daily_cap"] == 4
    assert kw["drop_spacs"] is False
    assert kw["deny_list"] == ["SPY"]
    assert kw["seen_accessions"] == ["a-1", "a-2"]
    assert "identity" in kw


def test_signal_kwargs_defaults_when_block_absent():
    kw = _signal_kwargs({})["edgar_buyback"]
    assert kw["phrases"] is None       # None => buyback.DEFAULT_PHRASES in the ctor
    assert kw["daily_cap"] == 6
    assert kw["drop_spacs"] is True
    assert kw["deny_list"] == []
    assert kw["seen_accessions"] == []


def test_ships_disabled_at_half_weight_in_repo_config():
    sig = _CFG["scout"]["signals"]["edgar_buyback"]
    assert sig["enabled"] is False and sig["weight"] == 0.5


def test_repo_config_buyback_block_defaults():
    bb = _CFG["scout"]["buyback"]
    assert bb["daily_cap"] == 6
    assert bb["drop_spacs"] is True
    assert bb["deny_list"] == []


def test_state_buyback_accession_round_trip_and_cap(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    assert st.buyback_seen_accessions() == []          # absent key: back-compat, no migration
    st.add_buyback_accessions(["a-1", "a-2"])
    st.add_buyback_accessions(["a-2", "a-3"])           # idempotent on repeats
    assert ScoutState(tmp_path / "s.json").buyback_seen_accessions() == ["a-1", "a-2", "a-3"]
    st.add_buyback_accessions([f"x-{i}" for i in range(600)], cap=500)
    kept = st.buyback_seen_accessions()
    assert len(kept) == 500
    assert "a-1" not in kept and "x-599" in kept


def test_state_forward_compat_old_file_without_buyback_key(tmp_path):
    # an old state file that predates the buyback key must load + read [] (no migration)
    p = tmp_path / "s.json"
    p.write_text('{"screened": {}, "runs": [], "held": [], "eightk_seen": ["z-1"]}')
    st = ScoutState(p)
    assert st.buyback_seen_accessions() == []
    assert st.eightk_seen_accessions() == ["z-1"]      # unrelated keys untouched
