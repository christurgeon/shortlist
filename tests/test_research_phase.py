"""Unit tests for the research_phase_budget_s wall-clock ceiling (FIX 1).

We inject fake is_available / enrich callables via the _is_available / _enrich kwargs
so the test never touches the real research module or spawns claude.
"""
from __future__ import annotations

import time

from shortlist.research.phase import research_phase as _research_phase


def _make_research_cfg(budget_s: float) -> dict:
    return {"research_top_n": 1, "research_phase_budget_s": budget_s}


def test_research_phase_times_out_and_returns_note():
    """enrich sleeps longer than the budget -> timeout note returned quickly (no blocking hang)."""

    def slow_enrich(cards, config, *, top_n, refresh, require_passed=True, macro=None):
        time.sleep(10)  # much longer than the tiny budget
        return []       # pragma: no cover

    t0 = time.monotonic()
    briefs, assessments, researched, note, skipped = _research_phase(
        cards=[],
        config={},
        research_cfg=_make_research_cfg(budget_s=0.05),  # 50 ms budget
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

    def fast_enrich(cards, config, *, top_n, refresh, require_passed=True, macro=None):
        return [_FakeResult()]

    briefs, assessments, researched, note, skipped = _research_phase(
        cards=[],
        config={},
        research_cfg=_make_research_cfg(budget_s=5.0),  # generous budget
        _is_available=lambda: True,
        _enrich=fast_enrich,
    )
    assert "AAPL" in briefs
    assert "AAPL" in researched
    assert note is None


def test_research_phase_kill_switch_env(monkeypatch):
    """SHORTLIST_NO_RESEARCH=1 skips before ever calling enrich."""
    called = []

    def should_not_be_called(cards, config, *, top_n, refresh):
        called.append(1)
        return []

    monkeypatch.setenv("SHORTLIST_NO_RESEARCH", "1")
    briefs, assessments, researched, note, skipped = _research_phase(
        cards=[],
        config={},
        research_cfg=_make_research_cfg(budget_s=5.0),
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

    briefs, assessments, researched, note, skipped = _research_phase(
        cards=[],
        config={},
        research_cfg=_make_research_cfg(budget_s=5.0),
        _is_available=lambda: False,
        _enrich=should_not_be_called,
    )
    assert note is not None and "not available" in note
    assert called == []


def test_research_phase_forwards_require_passed_and_top_n():
    from shortlist.research.phase import research_phase as _research_phase
    captured = {}

    # The fake mirrors the REAL enrich signature (incl. require_passed) so that when
    # _research_phase calls _enrich(..., require_passed=..., top_n=...) it doesn't
    # TypeError. The red here is "_research_phase has no top_n kwarg", not a fake mismatch.
    def fake_enrich(cards, config, *, top_n, refresh, require_passed=True, macro=None):
        captured["top_n"] = top_n
        captured["require_passed"] = require_passed
        return []   # no results -> empty briefs

    _research_phase([], {}, {"research_top_n": 3}, top_n=7, require_passed=False,
                    _is_available=lambda: True, _enrich=fake_enrich)
    assert captured["top_n"] == 7
    assert captured["require_passed"] is False

    # Defaults: top_n falls back to scout_cfg, require_passed stays True (autonomous path).
    _research_phase([], {}, {"research_top_n": 5},
                    _is_available=lambda: True, _enrich=fake_enrich)
    assert captured["top_n"] == 5 and captured["require_passed"] is True


def test_research_phase_surfaces_per_ticker_skip_reasons():
    from shortlist.research import ResearchResult
    from shortlist.research.phase import research_phase as _research_phase

    def fake_enrich(cards, config, *, top_n, refresh, require_passed=True, macro=None):
        return [ResearchResult("NVDA", skipped="assessment failed")]

    out = _research_phase([object()], {}, {"research_top_n": 1},
                          _is_available=lambda: True, _enrich=fake_enrich)
    skipped = out[4]          # 5th element
    assert skipped == {"NVDA": "assessment failed"}


def test_research_phase_forwards_macro_to_enrich():
    """The daily run fetches MacroContext and threads it into run_harness and
    build_report; the research phase must get it too, or daily auto-research briefs
    lack the macro line that /deep briefs have (the D8 bug, other code path)."""
    captured = {}

    def fake_enrich(cards, config, *, top_n, refresh, require_passed=True, macro=None):
        captured["macro"] = macro
        return []

    sentinel = object()
    _research_phase([], {}, {"research_top_n": 1}, macro=sentinel,
                    _is_available=lambda: True, _enrich=fake_enrich)
    assert captured["macro"] is sentinel
