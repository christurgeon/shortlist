# tests/test_run_harness_macro.py
from __future__ import annotations
import inspect
from shortlist.screen import run_harness

def test_run_harness_accepts_macro_kwarg():
    sig = inspect.signature(run_harness)
    assert "macro" in sig.parameters
    assert sig.parameters["macro"].default is None


def test_run_harness_does_not_mark_aux_only_sources_empty(monkeypatch):
    import shortlist.data.collector as collector
    from shortlist.config import load_config
    from shortlist.data.models import TickerSnapshot

    snap = TickerSnapshot(ticker="X", errors=["edgar: boom"])
    monkeypatch.setattr(collector, "collect", lambda tickers, names, config=None: [snap])
    (card,) = run_harness(["X"], ["edgar", "wsb", "lobbying"], load_config("config.yaml"))
    assert card.coverage.providers == {"edgar": "error", "wsb": "ok", "lobbying": "ok"}
