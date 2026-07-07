"""Pre-registration pins for the two 8-K cohorts + validate --backfill routing (mirrors
tests/test_scout_backfill_cli.py's run_validate idiom: fetches faked, verify_untampered
monkeypatched, REAL committed prereg files loaded from the repo root)."""
from datetime import date

from shortlist.scout import daily
from shortlist.scout.daily import _DEFAULT_CONFIG, _slug_for_signal, run_validate
from shortlist.scout.preregister import load_prereg

_REPO_ROOT = str(_DEFAULT_CONFIG.parent)


def test_slugs_route_to_the_committed_filenames():
    assert _slug_for_signal("edgar:8k") == "edgar_8k"
    assert _slug_for_signal("edgar:8k_negative") == "edgar_8k_negative"


def _common_pins(p):
    assert p["window_start"] == date(2022, 1, 1)
    assert p["window_end"] == date(2025, 12, 31)
    assert p["k_months"] == 3
    assert p["factor_model"] == "ff3"
    assert p["weighting"] == "equal"
    assert p["min_measurable_frac"] == 0.90
    assert p["min_independent_blocks"] == 8
    assert p["min_bucket_events"] == 5
    assert p["delisting_return"] == -0.55                 # Shumway partial, 13D convention
    assert p["regime_down_rule"] == "spy_trailing_3m_negative"
    # window_end + K = 2026-03-31 < as_of: the FIRST verdict is canonical, never INTERIM
    assert p["verdict_as_of"] == p["as_of"] == date(2026, 7, 7)


def test_prereg_edgar_8k_pins():
    p = load_prereg("edgar_8k", repo_root=_REPO_ROOT)
    assert p["signal"] == "edgar:8k"
    _common_pins(p)


def test_prereg_edgar_8k_negative_pins():
    p = load_prereg("edgar_8k_negative", repo_root=_REPO_ROOT)
    assert p["signal"] == "edgar:8k_negative"
    _common_pins(p)


def test_validate_backfill_routes_both_8k_signals(monkeypatch):
    """A synthetic JSONL cohort carrying both 8-K signals gets one verdict per signal,
    each loading its REAL committed prereg (no 'missing or unparsable' degradation), each
    labeled SYNTHETIC, and neither INTERIM (verdict_as_of has passed)."""
    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))
    events = [
        {"signal": "edgar:8k", "ticker": "AAA", "cik": None, "event_date": "2024-01-16",
         "as_of_price": None, "strength": 0.6, "gated": None, "composite": None,
         "origin": "backfill", "meta": {}},
        {"signal": "edgar:8k_negative", "ticker": "BBB", "cik": None,
         "event_date": "2024-01-16", "as_of_price": None, "strength": 0.6, "gated": None,
         "composite": None, "origin": "backfill", "meta": {}},
    ]
    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 7),
                            lookback_days=365, events_override=events)
    by_sig = {v.signal: v for v in verdicts}
    assert set(by_sig) == {"edgar:8k", "edgar:8k_negative"}
    for v in by_sig.values():
        assert not any("missing or unparsable" in n for n in v.notes)
        assert not any("INTERIM" in n for n in v.notes)
        assert any("SYNTHETIC" in n for n in v.notes)
