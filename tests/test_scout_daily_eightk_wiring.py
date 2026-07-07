"""Regression: the enabled config key for the 8-K signal must actually build the signal
(the silent-dead-feature trap — mirrors test_scout_daily_activist_wiring.py), plus the
ships-disabled config pin and the ScoutState accession-set round-trip."""
from pathlib import Path

import yaml

from shortlist.scout.daily import _enabled_signal_names, _signal_kwargs
from shortlist.scout.state import ScoutState

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_eightk_key_is_known_and_buildable():
    cfg = {"signals": {"edgar_8k": {"enabled": True, "weight": 0.5}}}
    assert "edgar_8k" in _enabled_signal_names(cfg)


def test_disabled_eightk_key_not_built():
    cfg = {"signals": {"edgar_8k": {"enabled": False, "weight": 0.5}}}
    assert "edgar_8k" not in _enabled_signal_names(cfg)


def test_signal_kwargs_threads_eightk_block_and_seen_accessions():
    cfg = {"eightk": {"item_sets": [["1.01", "3.03"], ["2.01"]], "daily_cap": 4,
                      "drop_spacs": False, "deny_list": ["SPY"]}}
    kw = _signal_kwargs(cfg, eightk_seen=["a-1", "a-2"])["edgar_8k"]
    assert kw["item_sets"] == [["1.01", "3.03"], ["2.01"]]
    assert kw["daily_cap"] == 4
    assert kw["drop_spacs"] is False
    assert kw["deny_list"] == ["SPY"]
    assert kw["seen_accessions"] == ["a-1", "a-2"]
    assert "identity" in kw


def test_signal_kwargs_defaults_when_block_absent():
    kw = _signal_kwargs({})["edgar_8k"]
    assert kw["item_sets"] == [["1.01", "3.03"]]
    assert kw["daily_cap"] == 6
    assert kw["drop_spacs"] is True
    assert kw["seen_accessions"] == []


def test_ships_disabled_at_half_weight_in_repo_config():
    sig = _CFG["scout"]["signals"]["edgar_8k"]
    assert sig["enabled"] is False and sig["weight"] == 0.5


def test_repo_config_eightk_block_defaults():
    ek = _CFG["scout"]["eightk"]
    assert ek["item_sets"] == [["1.01", "3.03"]]
    assert ek["daily_cap"] == 6
    assert ek["negative_veto"]["enabled"] is True     # the veto half ships ON (Task 4 consumes)
    assert ek["negative_veto"]["lookback_days"] == 30


def test_state_accession_set_round_trip_and_cap(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    assert st.eightk_seen_accessions() == []          # absent key: back-compat, no migration
    st.add_eightk_accessions(["a-1", "a-2"])
    st.add_eightk_accessions(["a-2", "a-3"])          # idempotent on repeats
    assert ScoutState(tmp_path / "s.json").eightk_seen_accessions() == ["a-1", "a-2", "a-3"]
    st.add_eightk_accessions([f"x-{i}" for i in range(600)], cap=500)
    kept = st.eightk_seen_accessions()
    assert len(kept) == 500
    assert "a-1" not in kept and "x-599" in kept      # oldest evicted, newest kept
