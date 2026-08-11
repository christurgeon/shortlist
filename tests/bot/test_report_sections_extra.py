from datetime import date

from shortlist.bot.report.sections import (Detail, _DeepBlock, _PriorPicks,
                                              _ValidationScoreboard, render_html_body,
                                              render_text)
from shortlist.bot.report.viewmodel import FunnelVM, ReportVM


class _VM:
    """Minimal duck-typed view model for section unit tests."""
    def __init__(self, deep, picks):
        self.deep_block = deep
        self.prior_picks = picks


def test_deep_block_chunks_three_per_line():
    vm = _VM(["A", "B", "C", "D"], [])
    assert _DeepBlock().applies(vm)
    txt = "\n".join(_DeepBlock().render_text(vm, Detail.FULL))
    assert "/deep A, B, C" in txt
    assert "/deep D" in txt
    assert "not investment advice" in txt.lower()


def test_deep_block_absent_when_empty():
    assert not _DeepBlock().applies(_VM([], []))
    assert _DeepBlock().render_text(_VM([], []), Detail.FULL) == []


def test_prior_picks_shows_excess_and_bucket():
    picks = [{"ticker": "XYZ", "ret": 0.30, "excess": 0.20, "horizon_bucket": "3m",
              "evidence": "Activist 13D: Elliott → XYZ"}]
    vm = _VM([], picks)
    assert _PriorPicks().applies(vm)
    txt = "\n".join(_PriorPicks().render_text(vm, Detail.FULL))
    assert "XYZ" in txt and "+20" in txt and "3m" in txt


def test_prior_picks_none_safe_dash():
    picks = [{"ticker": "ABC", "ret": None, "excess": None, "horizon_bucket": None,
              "evidence": ""}]
    txt = "\n".join(_PriorPicks().render_text(_VM([], picks), Detail.FULL))
    assert "ABC" in txt and "—" in txt


# ---- byte-identical-absent pin (digest-verdicts Task 2) ----
# `validation=None` (the default and every run today, since nothing yet writes
# scout/validate-latest.json on the live daily path) must render EXACTLY this text/html --
# recorded from the pre-Task-2 codebase with the same inputs, so a future change to the
# validation section (or the render_text loop it lives in) can never silently perturb the
# other sections' output.
_PRE_TASK2_TEXT = (
    '📊 Scout shortlist — session 2026-06-01\n\n\n'
    'Pass to /deep (activist re-rating candidates — screening triage, not investment advice):\n'
    '/deep AAA, BBB\n\n'
    'Prior picks scoreboard (return since selection vs SPY):\n'
    '  X [3m] ret +10% · vs SPY +5% — e\n\n'
    'Note: a note'
)
_PRE_TASK2_HTML = (
    '<section class="sec"><div class="sec-label">Pass to /deep</div>'
    '<div class="deep"><div class="deepcmd">/deep AAA, BBB</div>'
    '<div class="muted">activist re-rating candidates — screening triage, '
    'not investment advice</div></div></section>'
    '<section class="sec"><div class="sec-label">Prior picks</div>'
    '<div class="picks"><div class="pick">X [3m] ret +10% · vs SPY +5% — e</div></div>'
    '</section>'
    '<section class="sec"><div class="sec-label">Coverage</div>'
    '<div class="cov"><div class="note">a note</div></div></section>'
)


def _report_vm(validation):
    return ReportVM(session=date(2026, 6, 1), leaders=[], signals=[],
                    funnel=FunnelVM(10, 8, 6, 4, 2), notes=["a note"],
                    deep_block=["AAA", "BBB"],
                    prior_picks=[{"ticker": "X", "ret": 0.1, "excess": 0.05,
                                 "horizon_bucket": "3m", "evidence": "e"}],
                    validation=validation)


def test_validation_section_absent_from_render_when_validation_none():
    vm = _report_vm(None)
    assert _ValidationScoreboard().applies(vm) is False
    assert render_text(vm, Detail.FULL) == _PRE_TASK2_TEXT
    assert render_html_body(vm) == _PRE_TASK2_HTML


def test_other_sections_unchanged_when_validation_present_vs_absent():
    """Adding a populated `validation` envelope must not perturb any OTHER section's own
    rendered lines -- the validation section's output is additive-only, not interleaved
    into (or reordering) the rest of the report."""
    from shortlist.bot.report.sections import SECTIONS

    present = {"as_of": "2026-07-01", "source": "live",
              "verdicts": [{"signal": "x", "verdict": "HOLD", "ir": 0.1,
                           "effective_blocks": 3, "n_selected": 5, "n_measurable": 4,
                           "cohort_type": "raw", "notes": [], "double_sort": None}]}

    def _other_section_lines(vm) -> list[str]:
        out: list[str] = []
        for s in SECTIONS:
            if s.id != "validation" and s.applies(vm):
                out += s.render_text(vm, Detail.FULL)
        return out

    vm_absent, vm_present = _report_vm(None), _report_vm(present)
    assert _other_section_lines(vm_absent) == _other_section_lines(vm_present)
    # ... while the full render DOES differ, because validation now contributes lines.
    assert render_text(vm_absent, Detail.FULL) != render_text(vm_present, Detail.FULL)
    assert "Signal validation" in render_text(vm_present, Detail.FULL)
