import importlib.util
import pytest
from datetime import date

if importlib.util.find_spec("PIL") is None:
    pytest.skip("Pillow not installed", allow_module_level=True)

from shortlist.bot.report.png import render_glance
from shortlist.bot.report.viewmodel import (
    AssessmentVM, LeaderVM, MetricsVM, ReportVM)


def _vm(call_stance):
    a = AssessmentVM(call_stance=call_stance, call_label="Buy" if call_stance else "")
    ld = LeaderVM(ticker="X", name=None, composite=70, subscores={"quality": 80},
                  masked=set(), gates=[], flags=[], confidence=0.8, thin=False,
                  scored=True, coverage_note=None, metrics=MetricsVM(), assessment=a)
    return ReportVM(session=date(2026, 6, 7), leaders=[ld],
                    notes=[])


def test_png_renders_with_and_without_call():
    assert render_glance(_vm("BUY")).startswith(b"\x89PNG")
    assert render_glance(_vm("")).startswith(b"\x89PNG")
