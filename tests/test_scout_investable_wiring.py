"""`scout.investable_floor` wiring: the config-absence contract and loud degradation.

The contract that matters most is the inert one. Both doubles below RAISE, so these tests
pass only if `_investable_verdicts` returns *before* either source is touched — the
`test_removing_the_form4_block_leaves_the_signal_inert` lesson, where an
empty-return double made the assertion a tautology that would also have passed against a
signal still calling both.
"""
import pytest
import yaml
from pathlib import Path

from shortlist.scout.daily import _investable_notes, _investable_verdicts
from shortlist.scout.funnel import apply_investable_floor
from shortlist.scout.models import Candidate, Emission

SESSION = __import__("datetime").date(2026, 8, 7)


def _boom_universe(**kw):
    raise AssertionError("universe was fetched despite the floor being off")


def _boom_finra(cfg):
    raise AssertionError("FINRA was fetched despite the floor being off")


def _cand(ticker):
    c = Candidate(ticker=ticker)
    c.add(Emission(ticker, "edgar:activist_13d", 0.8, "ev", is_discovery=True), 1.5)
    return c


# --- the absence contract --------------------------------------------------------------

@pytest.mark.parametrize("cfg", [{}, {"investable_floor": {}},
                                 {"investable_floor": {"enabled": False}}])
def test_absent_or_disabled_block_is_inert_with_zero_fetches(cfg):
    v, notes = _investable_verdicts(cfg, SESSION,
                                    _fetch_universe=_boom_universe, _fetch_finra=_boom_finra)
    assert v == {} and notes == []


def test_an_empty_verdict_map_is_the_identity_on_the_funnel():
    cands = [_cand("AAA"), _cand("BBB")]
    kept, dropped = apply_investable_floor(cands, {})
    assert [c.ticker for c in kept] == ["AAA", "BBB"] and dropped == []


# --- loud degradation, never silent -----------------------------------------------------

def test_a_failed_universe_fetch_degrades_to_inert_with_a_LOUD_note():
    def boom(**kw):
        raise RuntimeError("WAF blocked")
    v, notes = _investable_verdicts({"investable_floor": {"enabled": True}}, SESSION,
                                    _fetch_universe=boom, _fetch_finra=lambda cfg: [])
    assert v == {}
    assert any("UNAVAILABLE" in n for n in notes), notes


def test_an_empty_universe_degrades_to_inert_with_a_LOUD_note():
    """Distinct from a raise: the endpoint can answer 200 with nothing useful."""
    v, notes = _investable_verdicts({"investable_floor": {"enabled": True}}, SESSION,
                                    _fetch_universe=lambda **kw: {},
                                    _fetch_finra=lambda cfg: [])
    assert v == {} and any("UNAVAILABLE" in n for n in notes), notes


def test_volume_failure_still_applies_the_market_cap_leg_and_says_so():
    """A FINRA outage must not disable the whole floor — the cap leg is independent."""
    def boom(cfg):
        raise RuntimeError("finra down")
    v, notes = _investable_verdicts(
        {"investable_floor": {"enabled": True, "min_market_cap": 1e8,
                              "min_dollar_adv": 5e5}},
        SESSION, _fetch_universe=lambda **kw: {"SHELL": (5e6, 0.30)}, _fetch_finra=boom)
    assert "SHELL" in v and not v["SHELL"].keep
    assert any("VOLUME UNAVAILABLE" in n for n in notes), notes


# --- the happy path ---------------------------------------------------------------------

def test_drops_are_named_with_a_reason_never_a_bare_count():
    v, _ = _investable_verdicts(
        {"investable_floor": {"enabled": True, "min_market_cap": 1e8,
                              "min_dollar_adv": 5e5}},
        SESSION,
        _fetch_universe=lambda **kw: {"BIG": (5e9, 40.0), "THIN": (4e8, 2.0),
                                      "SHELL": (5e6, 0.30)},
        _fetch_finra=lambda cfg: [
            {"symbolCode": "BIG", "averageDailyVolumeQuantity": 900_000},
            {"symbolCode": "THIN", "averageDailyVolumeQuantity": 15_000},
            {"symbolCode": "SHELL", "averageDailyVolumeQuantity": 900_000}])
    assert set(v) == {"THIN", "SHELL"}, "BIG clears both floors"
    kept, dropped = apply_investable_floor(
        [_cand("BIG"), _cand("THIN"), _cand("SHELL")], v)
    assert [c.ticker for c in kept] == ["BIG"]
    notes = _investable_notes(dropped)
    assert any("THIN" in n and "volume" in n for n in notes), notes
    assert any("SHELL" in n and "market cap" in n for n in notes), notes


def test_a_candidate_absent_from_the_universe_is_kept():
    """Abstain-never-guess: absence also captures OTC names, recent listings and API gaps,
    so it must never be read as 'not investable'."""
    v, _ = _investable_verdicts(
        {"investable_floor": {"enabled": True}}, SESSION,
        _fetch_universe=lambda **kw: {"OTHER": (5e9, 40.0)}, _fetch_finra=lambda cfg: [])
    kept, dropped = apply_investable_floor([_cand("UNKNOWN")], v)
    assert [c.ticker for c in kept] == ["UNKNOWN"] and dropped == []


# --- the shipped config -----------------------------------------------------------------

def test_shipped_config_enables_the_floor_at_the_measured_knee():
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    block = cfg["scout"]["investable_floor"]
    assert block["enabled"] is True
    assert block["min_market_cap"] == 1.0e8
    assert block["min_dollar_adv"] == 5.0e5


def test_shipped_gate_sits_above_the_floor():
    """The two instruments must not invert: the funnel floor is the cheap early cut, the
    gate is the actionability line. A gate BELOW the floor would make the floor the real
    gate and silently change what `passed` means."""
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    assert cfg["gates"]["min_market_cap"] == 3.0e8
    assert cfg["gates"]["min_market_cap"] > cfg["scout"]["investable_floor"]["min_market_cap"]
