"""Regression: the enabled config key for the Form 4 opportunistic-insider signal must
build the signal AND thread scout.form4 (cfg["dera"]/min_value/roles/...) through to
EdgarForm4Signal -- else config.yaml's form4 block is dead weight on the live path
(_signal_kwargs is the only place that turns config into constructor args)."""
from shortlist.scout.daily import (
    _DISCOVERY_SIGNAL_NAMES,
    _enabled_signal_names,
    _signal_kwargs,
)


def test_form4_key_is_known_and_enabled():
    cfg = {"signals": {"edgar_form4": {"enabled": True, "weight": 1.5}}}
    assert "edgar_form4" in _enabled_signal_names(cfg)


def test_form4_in_discovery_names():
    assert "edgar_form4" in _DISCOVERY_SIGNAL_NAMES


def test_signal_kwargs_threads_form4_block_and_daily_cap():
    cfg = {"form4": {"min_value": 50_000, "roles": ["officer"],
                     "dera": {"quarters": 8, "cache_dir": "/tmp/dera"}},
           "edgar_index_daily_cap": 900}
    kw = _signal_kwargs(cfg)["edgar_form4"]
    assert kw["cfg"] == cfg["form4"]
    assert kw["max_filings"] == 900


def test_signal_kwargs_form4_defaults_when_block_absent():
    # C-2: cfg must be None (not {}) when scout.form4 is absent -- EdgarForm4Signal treats
    # None as "no config block, stay inert" and {} as "block present but empty, run on
    # code defaults". Collapsing both to {} here would silently defeat that contract.
    kw = _signal_kwargs({})["edgar_form4"]
    assert kw["cfg"] is None
    assert kw["max_filings"] == 400
    assert "identity" in kw   # sourced from SEC_IDENTITY env, not asserted here (env-dependent)
