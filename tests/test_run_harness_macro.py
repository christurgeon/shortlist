# tests/test_run_harness_macro.py
from __future__ import annotations
import inspect
from shortlist.screen import run_harness

def test_run_harness_accepts_macro_kwarg():
    sig = inspect.signature(run_harness)
    assert "macro" in sig.parameters
    assert sig.parameters["macro"].default is None
