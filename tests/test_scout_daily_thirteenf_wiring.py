"""Regression: the enabled config key for the 13F signal must actually build the signal
(the silent-dead-feature trap — an enabled key absent from _KNOWN_SIGNAL_KEYS is ignored),
the kwargs block threads through (seen-set as a KEYWORD arg), the repo config ships it ON,
and the ScoutState processed-accession set round-trips + is forward-compatible."""
from pathlib import Path

import yaml

from datetime import date

from shortlist.scout.daily import (
    _DISCOVERY_SIGNAL_NAMES,
    _KNOWN_SIGNAL_KEYS,
    _enabled_signal_names,
    _scan_discovery,
    _signal_kwargs,
)
from shortlist.scout.models import Emission
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


# --- per-emission config keys (weight per kind, cap per signal) -------------------------

class _FakeSignal:
    """Minimal discovery signal emitting a fixed set of (signal_string, ticker) pairs."""
    is_discovery = True

    def __init__(self, name, pairs, *, cfg_keys=None):
        self.name = name
        self._pairs = pairs
        self._cfg_keys = cfg_keys      # None => no cfg_key_for hook at all

    def scan(self, session):
        return [Emission(t, s, 0.5, "ev", is_discovery=True) for s, t in self._pairs]

    def available(self):
        return (True, "ok")

    def __getattr__(self, item):
        # Only expose cfg_key_for when this fake was built with a mapping, so the
        # hook-absent back-compat path is genuinely exercised.
        if item == "cfg_key_for" and self.__dict__.get("_cfg_keys") is not None:
            return lambda e: self.__dict__["_cfg_keys"].get(e.signal)
        raise AttributeError(item)


def test_scan_discovery_resolves_per_emission_config_keys(tmp_path):
    """One signal object, two emission strings, two different weights."""
    sig = _FakeSignal("edgar_13f",
                      [("edgar:13f_new_position", "NEW"),
                       ("edgar:13f_material_add", "ADD")],
                      cfg_keys={"edgar:13f_new_position": "edgar_13f",
                                "edgar:13f_material_add": "edgar_13f_material_add"})
    sig_cfg = {"edgar_13f": {"enabled": True, "weight": 1.0},
               "edgar_13f_material_add": {"weight": 0.75}}
    _ems, weights, _caps = _scan_discovery(
        [sig], state=ScoutState(tmp_path / "s.json"), demo=False,
        session=date(2026, 8, 14), sig_cfg=sig_cfg, statuses=[])
    assert weights["edgar:13f_new_position"] == 1.0
    assert weights["edgar:13f_material_add"] == 0.75


def test_cap_survives_an_adds_only_night(tmp_path):
    """max_slots lives on edgar_13f; a night with ONLY adds must still be capped.

    Regression for the defect where the cap was resolved from the per-emission key:
    edgar_13f_material_add deliberately carries no max_slots, so an adds-only night
    produced caps == {} and ran the family uncapped.
    """
    sig = _FakeSignal("edgar_13f", [("edgar:13f_material_add", "ADD")],
                      cfg_keys={"edgar:13f_material_add": "edgar_13f_material_add"})
    sig_cfg = {"edgar_13f": {"enabled": True, "weight": 1.0, "max_slots": 4},
               "edgar_13f_material_add": {"weight": 0.75}}
    _ems, weights, caps = _scan_discovery(
        [sig], state=ScoutState(tmp_path / "s.json"), demo=False,
        session=date(2026, 8, 14), sig_cfg=sig_cfg, statuses=[])
    assert weights["edgar:13f_material_add"] == 0.75
    assert caps == {"edgar:13f": 4}          # NOT {} -- the family stays capped


def test_scan_discovery_without_cfg_key_for_is_unchanged(tmp_path):
    """Back-compat pin: a signal with no hook resolves exactly as before."""
    sig = _FakeSignal("edgar_form4", [("edgar:form4_opportunistic", "XYZ")])
    sig_cfg = {"edgar_form4": {"enabled": True, "weight": 1.0, "max_slots": 3}}
    _ems, weights, caps = _scan_discovery(
        [sig], state=ScoutState(tmp_path / "s.json"), demo=False,
        session=date(2026, 8, 14), sig_cfg=sig_cfg, statuses=[])
    assert weights == {"edgar:form4_opportunistic": 1.0}
    assert caps == {"edgar:form4_opportunistic": 3}


# --- repo config ships material adds ON, and plumbs through ------------------------------

def test_repo_config_ships_material_add_enabled():
    ma = _CFG["scout"]["thirteenf"]["material_add"]
    assert ma["enabled"] is True
    assert ma["ratio"] == 1.50
    assert ma["top_n"] == 5


def test_repo_config_material_add_weight_is_below_new_positions():
    sig = _CFG["scout"]["signals"]
    assert sig["edgar_13f_material_add"]["weight"] == 0.75
    assert sig["edgar_13f"]["weight"] == 1.0
    assert sig["edgar_13f_material_add"]["weight"] < sig["edgar_13f"]["weight"]


def test_repo_config_material_add_key_is_weight_only_and_not_buildable():
    """No `enabled` here (that would be a dead knob), and it must never build a signal."""
    assert set(_CFG["scout"]["signals"]["edgar_13f_material_add"]) == {"weight"}
    assert "edgar_13f_material_add" not in _KNOWN_SIGNAL_KEYS
    assert "edgar_13f_material_add" not in _DISCOVERY_SIGNAL_NAMES
    assert "edgar_13f_material_add" not in _enabled_signal_names(_CFG["scout"])


def test_repo_config_family_cap_lives_on_the_parent_key():
    """max_slots governs the family, so it must be on edgar_13f, not the add's key."""
    assert _CFG["scout"]["signals"]["edgar_13f"]["max_slots"] == 4
    assert "max_slots" not in _CFG["scout"]["signals"]["edgar_13f_material_add"]


def test_signal_kwargs_threads_material_add_block():
    cfg = {"thirteenf": {"funds": [], "material_add": {"enabled": True, "ratio": 1.5,
                                                       "top_n": 5}}}
    kw = _signal_kwargs(cfg)["edgar_13f"]
    assert kw["material_add"] == {"enabled": True, "ratio": 1.5, "top_n": 5}


def test_signal_kwargs_material_add_absent_is_empty_dict():
    """A config with no material_add block must yield the inert default, never a KeyError."""
    kw = _signal_kwargs({"thirteenf": {"funds": []}})["edgar_13f"]
    assert kw["material_add"] == {}
