"""Regression: the enabled config key for the 13F signal must actually build the signal
(the silent-dead-feature trap — an enabled key absent from _KNOWN_SIGNAL_KEYS is ignored),
the kwargs block threads through (seen-set as a KEYWORD arg), the repo config ships it ON,
and the ScoutState processed-accession set round-trips + is forward-compatible."""
from pathlib import Path

import yaml

from shortlist.scout.daily import (
    _DISCOVERY_SIGNAL_NAMES,
    _KNOWN_SIGNAL_KEYS,
    _enabled_signal_names,
    _signal_kwargs,
)
from shortlist.scout.state import ScoutState

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_thirteenf_is_known_discovery_key():
    assert "edgar_13f" in _KNOWN_SIGNAL_KEYS          # the silent-omission guard
    assert "edgar_13f" in _DISCOVERY_SIGNAL_NAMES


def test_thirteenf_key_is_known_and_enabled():
    cfg = {"signals": {"edgar_13f": {"enabled": True, "weight": 1.0}}}
    assert "edgar_13f" in _enabled_signal_names(cfg)


def test_signal_kwargs_threads_thirteenf_block_and_seen_accessions():
    cfg = {"thirteenf": {"funds": [{"cik": 1067983, "name": "Berkshire"}],
                         "min_position_pct": 0.01, "full_strength_pct": 0.04,
                         "max_filings_per_day": 5, "top_n": 7, "deny_list": ["SPY"]}}
    kw = _signal_kwargs(cfg, thirteenf_seen=["acc-1", "acc-2"])["edgar_13f"]
    assert kw["funds"] == [{"cik": 1067983, "name": "Berkshire"}]
    assert kw["min_position_pct"] == 0.01
    assert kw["full_strength_pct"] == 0.04
    assert kw["max_filings_per_day"] == 5
    assert kw["top_n"] == 7
    assert kw["deny_list"] == ["SPY"]
    assert kw["seen_accessions"] == ["acc-1", "acc-2"]
    assert "identity" in kw


def test_signal_kwargs_defaults_when_block_absent():
    kw = _signal_kwargs({})["edgar_13f"]
    assert kw["funds"] == []
    assert kw["min_position_pct"] == 0.005
    assert kw["full_strength_pct"] == 0.05
    assert kw["max_filings_per_day"] == 3
    assert kw["top_n"] == 10
    assert kw["seen_accessions"] == []


def test_ships_enabled_at_weight_one_in_repo_config():
    sig = _CFG["scout"]["signals"]["edgar_13f"]
    assert sig["enabled"] is True and sig["weight"] == 1.0


def test_repo_config_thirteenf_block_has_seven_verified_funds():
    tf = _CFG["scout"]["thirteenf"]
    ciks = {f["cik"] for f in tf["funds"]}
    # The seven live-verified CIKs (spec §1) — the stale shells must NOT be here.
    assert ciks == {1067983, 1336528, 1061768, 1418814, 1040273, 1656456, 1647251}
    assert 1054420 not in ciks and 1006438 not in ciks   # stale Baupost/Appaloosa shells
    assert tf["min_position_pct"] == 0.005 and tf["full_strength_pct"] == 0.05
    assert tf["max_filings_per_day"] == 3


def test_state_processed_accession_round_trip_and_forward_compat(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    assert st.thirteenf_seen_accessions() == []       # absent key: back-compat, no migration
    st.add_thirteenf_accessions(["acc-1", "acc-2"])
    st.add_thirteenf_accessions(["acc-2", "acc-3"])    # idempotent on repeats
    assert ScoutState(tmp_path / "s.json").thirteenf_seen_accessions() == ["acc-1", "acc-2", "acc-3"]
    st.add_thirteenf_accessions([f"x-{i}" for i in range(300)], cap=200)
    kept = st.thirteenf_seen_accessions()
    assert len(kept) == 200 and "acc-1" not in kept and "x-299" in kept
