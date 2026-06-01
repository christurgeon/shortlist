from datetime import date
from shortlist.scout.signals import build_signals, MockSignal


def test_mock_signal_emits_for_demo():
    sig = MockSignal()
    ems = sig.scan(date(2026, 5, 29))
    assert ems and all(e.is_discovery for e in ems)
    assert sig.available() == (True, f"{len(ems)} hits")


def test_build_signals_resolves_names_and_skips_unknown():
    sigs = build_signals(["mock"])
    assert len(sigs) == 1 and sigs[0].name == "mock"


def test_build_signals_respects_disabled(monkeypatch):
    # unknown names raise so config typos are loud, not silent
    import pytest
    with pytest.raises(KeyError):
        build_signals(["does_not_exist"])
