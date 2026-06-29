"""Regression: the enabled config key for the activist signal must actually build the
signal (the exact dead-feature failure mode this feature exists to fix for Yahoo)."""
from shortlist.scout.daily import _enabled_signal_names, _signal_kwargs


def test_activist_key_is_known_and_enabled():
    cfg = {"signals": {"edgar_activist_13d": {"enabled": True, "weight": 1.5}}}
    assert "edgar_activist_13d" in _enabled_signal_names(cfg)


def test_disabled_activist_key_not_built():
    cfg = {"signals": {"edgar_activist_13d": {"enabled": False, "weight": 1.5}}}
    assert "edgar_activist_13d" not in _enabled_signal_names(cfg)


def test_signal_kwargs_threads_activist_block():
    cfg = {"activist_13d": {"daily_cap": 250, "drop_spacs": True, "drop_affiliates": True,
                            "marquee_boost": 0.3}}
    kw = _signal_kwargs(cfg)["edgar_activist_13d"]
    assert kw["max_filings"] == 250
    assert kw["drop_affiliates"] is True
    assert kw["marquee_boost"] == 0.3
    assert "identity" in kw


def test_signal_kwargs_defaults_when_block_absent():
    kw = _signal_kwargs({})["edgar_activist_13d"]
    assert kw["max_filings"] == 300
    assert kw["drop_spacs"] is True
