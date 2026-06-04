"""Unit tests for the research_phase_budget_s wall-clock ceiling (FIX 1).

We inject fake is_available / enrich callables via the _is_available / _enrich kwargs
so the test never touches the real research module or spawns claude.
"""
from __future__ import annotations

import time

from shortlist.scout.daily import _research_phase


def _make_scout_cfg(budget_s: float) -> dict:
    return {"research_top_n": 1, "research_phase_budget_s": budget_s}


def test_research_phase_times_out_and_returns_note():
    """enrich sleeps longer than the budget -> timeout note returned quickly (no blocking hang)."""

    def slow_enrich(cards, config, *, top_n, refresh):
        time.sleep(10)  # much longer than the tiny budget
        return []       # pragma: no cover

    t0 = time.monotonic()
    briefs, assessments, researched, note = _research_phase(
        cards=[],
        config={},
        scout_cfg=_make_scout_cfg(budget_s=0.05),  # 50 ms budget
        _is_available=lambda: True,
        _enrich=slow_enrich,
    )
    elapsed = time.monotonic() - t0

    assert briefs == {}
    assert researched == []
    assert note is not None and "phase budget" in note and "exceeded" in note
    # The call must return quickly after the budget expires — NOT block until the
    # hung thread finishes.  Budget is 50ms; fake enrich sleeps 10s; if shutdown
    # blocks we'd see ~10s elapsed.  Allow generous margin but well under 10s.
    assert elapsed < 2.0, f"_research_phase blocked for {elapsed:.2f}s (expected < 2.0s)"


def test_research_phase_completes_within_budget():
    """enrich returns quickly -> briefs populated, no timeout note."""

    class _FakeResult:
        ticker = "AAPL"
        skipped = False
        synthesis = "Strong moat, great FCF."
        brief_path = None

    def fast_enrich(cards, config, *, top_n, refresh):
        return [_FakeResult()]

    briefs, assessments, researched, note = _research_phase(
        cards=[],
        config={},
        scout_cfg=_make_scout_cfg(budget_s=5.0),  # generous budget
        _is_available=lambda: True,
        _enrich=fast_enrich,
    )
    assert "AAPL" in briefs
    assert "AAPL" in researched
    assert note is None


def test_research_phase_kill_switch_env(monkeypatch):
    """SCOUT_NO_RESEARCH=1 skips before ever calling enrich."""
    called = []

    def should_not_be_called(cards, config, *, top_n, refresh):
        called.append(1)
        return []

    monkeypatch.setenv("SCOUT_NO_RESEARCH", "1")
    briefs, assessments, researched, note = _research_phase(
        cards=[],
        config={},
        scout_cfg=_make_scout_cfg(budget_s=5.0),
        _is_available=lambda: True,
        _enrich=should_not_be_called,
    )
    assert note == "research skipped: kill-switch"
    assert called == []


def test_research_phase_unavailable():
    """is_available() == False -> skipped with a note, no enrich call."""
    called = []

    def should_not_be_called(cards, config, *, top_n, refresh):
        called.append(1)
        return []

    briefs, assessments, researched, note = _research_phase(
        cards=[],
        config={},
        scout_cfg=_make_scout_cfg(budget_s=5.0),
        _is_available=lambda: False,
        _enrich=should_not_be_called,
    )
    assert note is not None and "not available" in note
    assert called == []
